"""
telegram_logic/flezen.py
~~~~~~~~~~~~~~~~~~~~~~~~
Handlers for Flezen links.
"""

import os
import time
import threading
import asyncio
import logging
from telethon import Button
from telethon.errors import FloodWaitError

from .bot import (
    bot, _find_cached_video, _pre_upload_file, _upload_to_storage,
    _cancellable, terabox_queue, _safe_send, active_tasks, STORAGE_GROUP_ID,
    shutting_down, acquire_user_slot, release_user_slot, USER_MAX_CONCURRENT,
)
from .helpers import format_size, format_duration, check_size_limit, env_int
from .media_info import extract_video_metadata, generate_video_thumbnail, get_video_attributes
from . import rate_limit
from . import alerts
from firebase_db.cache import add_to_cache
from firebase_db.stats import record_success as stats_ok, record_failure as stats_fail
from firebase_db.users import record_history, get_today_count, bump_today
from .progress_callbacks import make_upload_progress_cb, make_download_progress_cb
from .structured_log import ctx_logger, bind_context, new_request_id
from flezenDL import get_flezen_info, download_flezen_file, extract_flezen_id, FlezenError, FlezenDirectError
from teraboxDL.errors import CancelledError

log = ctx_logger(__name__)

ADMIN_ID = env_int("ADMIN_ID")
DAILY_LIMIT_PER_USER = env_int("DAILY_LIMIT_PER_USER", 0)
FLEZEN_MODE = "flezen"


def _build_flezen_caption(filename: str, size_str: str, dl_time: float, ul_time: float, total_time: float) -> str:
    """Build delivery caption for Flezen videos."""
    return (
        f"📦 **{filename}**\n"
        f"📐 Size: `{size_str}`\n"
        f"⬇️ Download: `{format_duration(dl_time)}`\n"
        f"⬆️ Upload: `{format_duration(ul_time)}`\n"
        f"⚡ Total: `{format_duration(total_time)}`\n\n"
        f"✨ *Powered by Flezen Engine*"
    )


async def process_flezen(event, flezen_url: str):
    """
    Process a Flezen link end-to-end.
    """
    bind_context(request_id=new_request_id(), chat_id=event.chat_id, link_id=flezen_url)
    chat_id = event.chat_id
    link_id = extract_flezen_id(flezen_url) or flezen_url
    user_mode = FLEZEN_MODE
    task_key = (chat_id, link_id)
    is_admin = bool(ADMIN_ID and chat_id == ADMIN_ID)
    total_start = time.time()

    cancel_btn = [[Button.inline("❌ Cancel", data=f"cancel:{link_id}")]]

    def _cleanup_files(*paths):
        """Remove temp files from disk."""
        for p in paths:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                    log.info(f"Cleaned up file: {p}")
                except Exception as e:
                    log.warning(f"Could not clean up {p}: {e}")

    cancel_event = threading.Event()
    acquired = False

    try:
        existing = active_tasks.get(task_key)
        if existing is not None and not existing.is_set():
            await _safe_send(event.respond, f"⚠️ `{link_id}` is already being processed. Use the ❌ button on that message to cancel it first.")
            return

        if not acquire_user_slot(chat_id, is_admin):
            await _safe_send(
                event.respond,
                f"⏳ You already have **{USER_MAX_CONCURRENT}** download(s) running. Wait for them to finish first."
            )
            return
        acquired = True

        active_tasks[task_key] = cancel_event

        # — Phase 1: Cache lookup ——————————————————————————————————————————
        status = await _safe_send(event.respond, f"🔍 Checking cache for `{link_id}`…")

        cached_msg = await _find_cached_video(link_id, user_mode)
        if cached_msg is not None:
            try:
                f = cached_msg.file
                fn = f.name or f"flezen_{link_id}.mp4"
                sz_str = format_size(f.size)
                cap = (
                    f"📦 **{fn}**\n"
                    f"📐 Size: `{sz_str}`\n"
                    f"⚡ *Served instantly from cache*\n\n"
                    f"✨ *Powered by Flezen Engine*"
                )
                await _safe_send(status.edit, "⚡ Serving from cache…")
                await bot.send_file(
                    chat_id,
                    cached_msg.media,
                    caption=cap,
                    supports_streaming=True,
                    reply_to=event.message.id,
                )
                await status.delete()
                stats_ok(f.size)
                await asyncio.to_thread(record_history, chat_id, fn, f"flezen:{link_id}", f.size)
                if not is_admin:
                    await asyncio.to_thread(bump_today, chat_id)
                rate_limit.register_success(chat_id)
                return
            except Exception as e:
                log.warning(f"Cache delivery failed: {e}")

        # — Phase 2: Metadata Extraction ————————————————————————————————————
        await _safe_send(status.edit, f"🔎 Resolving Flezen link `{link_id}`…", buttons=cancel_btn)

        try:
            info = await asyncio.to_thread(get_flezen_info, flezen_url)
        except FlezenDirectError as e:
            await _safe_send(status.edit, f"⚠️ {e}")
            stats_fail()
            rate_limit.register_failure(chat_id)
            return
        except Exception as e:
            log.error(f"Flezen resolve failed for {flezen_url}: {e}")
            await _safe_send(status.edit, f"❌ Failed to resolve Flezen link: {e}")
            stats_fail()
            rate_limit.register_failure(chat_id)
            return

        if cancel_event.is_set():
            await _safe_send(status.edit, "🚫 Cancelled.")
            return

        filename = info.get("filename", f"flezen_{link_id}.mp4")
        size_bytes = info.get("size", 0)
        size_str = format_size(size_bytes)

        # Check size limit
        ok, reason = check_size_limit(size_bytes, is_admin)
        if not ok:
            await _safe_send(status.edit, f"❌ File is too large ({size_str}). {reason}")
            stats_fail()
            rate_limit.register_failure(chat_id)
            return

        # Check if direct download URL is present
        download_url = info.get("download_url")
        if not download_url:
            caption = (
                f"📦 **{filename}**\n"
                f"📐 Size: `{size_str}`\n"
                f"👁️ Views: `{info.get('views', 0)}`\n"
                f"📅 Uploaded: `{info.get('upload_date', 'N/A')}`\n\n"
                f"⚠️ *Flezen mobile app token required for protected download.*\n"
                f"🔗 [Open in Flezen App]({flezen_url})"
            )
            await _safe_send(status.edit, caption)
            stats_ok(size_bytes)
            return

        # — Phase 3: Download ———————————————————————————————————————————————
        dl_start = time.time()
        output_file = f"downloads/{int(time.time())}_{filename}"
        os.makedirs("downloads", exist_ok=True)

        download_cb = make_download_progress_cb(status, filename, size_bytes, cancel_event)

        try:
            await _safe_send(status.edit, f"⬇️ Downloading **{filename}** (`{size_str}`)…", buttons=cancel_btn)
            downloaded_path = await asyncio.to_thread(
                download_flezen_file, info, output_file, download_cb
            )
        except CancelledError:
            await _safe_send(status.edit, "🚫 Download cancelled.")
            _cleanup_files(output_file)
            return
        except Exception as e:
            log.error(f"Flezen download failed: {e}")
            await _safe_send(status.edit, f"❌ Download failed: {e}")
            _cleanup_files(output_file)
            stats_fail()
            rate_limit.register_failure(chat_id)
            return

        if cancel_event.is_set():
            await _safe_send(status.edit, "🚫 Cancelled.")
            _cleanup_files(downloaded_path)
            return

        dl_time = time.time() - dl_start

        # — Phase 4: Video Metadata & Thumbnail —————————————————————————————
        meta = extract_video_metadata(downloaded_path)
        thumb_path = generate_video_thumbnail(downloaded_path)
        video_attrs = get_video_attributes(downloaded_path, meta)

        # — Phase 5: Storage Upload & Delivery ———————————————————————————————
        ul_start = time.time()
        upload_cb = make_upload_progress_cb(status, filename, size_bytes, cancel_event)

        try:
            await _safe_send(status.edit, f"⬆️ Uploading **{filename}**…", buttons=cancel_btn)

            uploaded_file = await _pre_upload_file(
                downloaded_path,
                progress_cb=upload_cb,
            )

            if cancel_event.is_set():
                await _safe_send(status.edit, "🚫 Cancelled.")
                _cleanup_files(downloaded_path, thumb_path)
                return

            ul_time = time.time() - ul_start
            total_time = time.time() - total_start
            caption = _build_flezen_caption(filename, size_str, dl_time, ul_time, total_time)

            storage_msg = None
            if STORAGE_GROUP_ID:
                try:
                    storage_msg = await _upload_to_storage(
                        uploaded_file,
                        filename=filename,
                        thumb=thumb_path,
                        attributes=video_attrs,
                    )
                except Exception as e:
                    log.warning(f"Storage upload failed: {e}")

            # Send to user
            await bot.send_file(
                chat_id,
                storage_msg.media if storage_msg else uploaded_file,
                caption=caption,
                thumb=thumb_path,
                attributes=video_attrs,
                supports_streaming=True,
                reply_to=event.message.id,
            )

            if storage_msg:
                await asyncio.to_thread(add_to_cache, link_id, storage_msg.id, user_mode)

            await status.delete()
            stats_ok(size_bytes)
            await asyncio.to_thread(record_history, chat_id, filename, f"flezen:{link_id}", size_bytes)
            if not is_admin:
                await asyncio.to_thread(bump_today, chat_id)
            rate_limit.register_success(chat_id)

        except Exception as e:
            log.error(f"Flezen upload/delivery failed: {e}")
            await _safe_send(status.edit, f"❌ Delivery failed: {e}")
            stats_fail()
            rate_limit.register_failure(chat_id)
        finally:
            _cleanup_files(downloaded_path, thumb_path)

    finally:
        active_tasks.pop(task_key, None)
        if acquired:
            release_user_slot(chat_id, is_admin)
