"""
telegram_logic/flare.py
~~~~~~~~~~~~~~~~~~~~~~~
Handlers for Flare / CashSnap / HugeBox links.
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
from flareDL import get_flare_info, download_flare_file, extract_flare_id, FlareError, FlareDirectError
from teraboxDL.errors import CancelledError

log = ctx_logger(__name__)

ADMIN_ID = env_int("ADMIN_ID")
DAILY_LIMIT_PER_USER = env_int("DAILY_LIMIT_PER_USER", 0)
FLARE_MODE = "flare"


def _build_flare_caption(filename: str, size_str: str, dl_time: float, ul_time: float, total_time: float) -> str:
    """Build delivery caption for Flare videos."""
    return (
        f"📦 **{filename}**\n"
        f"📐 Size: `{size_str}`\n"
        f"⬇️ Download: `{format_duration(dl_time)}`\n"
        f"⬆️ Upload: `{format_duration(ul_time)}`\n"
        f"⚡ Total: `{format_duration(total_time)}`\n\n"
        f"✨ *Powered by Flare Engine*"
    )


async def process_flare(event, flare_url: str):
    """
    Process a Flare / CashSnap / HugeBox link end-to-end.
    """
    bind_context(request_id=new_request_id(), chat_id=event.chat_id, link_id=flare_url)
    chat_id = event.chat_id
    link_id = extract_flare_id(flare_url) or flare_url
    user_mode = FLARE_MODE
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
                fname = (f.name if f and f.name else link_id)
                caption = f"📦 `{fname}`"
                await _safe_send(
                    bot.send_file,
                    chat_id, cached_msg.media,
                    caption=caption, supports_streaming=True, reply_to=event.message.id,
                )
                await _safe_send(status.delete)
            except Exception as e:
                log.warning(f"re-send failed for link_id={link_id}: {e}")
                await _safe_send(status.edit, "❌ Failed to send video.")
            return

        # — Phase 2: Prepare metadata ——————————————————————————————————————
        await _safe_send(status.edit, "⏳ Fetching Flare metadata…", buttons=cancel_btn)

        try:
            info = await asyncio.to_thread(get_flare_info, flare_url)
        except FlareDirectError as e:
            log.error(f"Flare direct resolution failed for {link_id}: {e}")
            rate_limit.register_failure(chat_id)
            stats_fail()
            await _safe_send(status.edit, f"❌ {e}")
            return
        except FlareError as e:
            log.error(f"Flare metadata fetch failed for {link_id}: {e}")
            rate_limit.register_failure(chat_id)
            stats_fail()
            await _safe_send(status.edit, f"❌ Failed to get video info: {e}")
            return
        except Exception as e:
            log.exception(f"Unexpected Flare metadata error for {link_id}")
            rate_limit.register_failure(chat_id)
            await _safe_send(status.edit, f"❌ Failed to get video info: {e}")
            return

        download_url = info["download_url"]
        filename = info["filename"]
        size_str = format_size(info["size"])

        # Size limit check
        size_error = check_size_limit(info["size"])
        if size_error:
            log.info(f"Size limit hit for {link_id}: {info['size']} bytes")
            await _safe_send(
                status.edit,
                f"📦 **{filename}**\n📐 Size: **{size_str}**\n\n{size_error}",
            )
            return

        await _safe_send(
            status.edit,
            f"📦 **{filename}**\n📐 Size: **{size_str}**\n\n⬇️ Downloading… **0%**",
            buttons=cancel_btn,
        )

        # — Phase 3: Download ——————————————————————————————————————————————
        loop = asyncio.get_running_loop()
        dl_start = time.time()
        dl_progress_cb = make_download_progress_cb(
            status, filename, size_str, loop, cancel_btn,
            expected_total=int(info.get("size") or 0),
        )

        temp_dir = "downloads"
        os.makedirs(temp_dir, exist_ok=True)
        local_output_path = os.path.join(temp_dir, filename)

        try:
            filepath = await asyncio.to_thread(
                download_flare_file, download_url, local_output_path, cancel_event, dl_progress_cb
            )
        except CancelledError:
            await _safe_send(status.edit, "🚫 Cancelled.")
            return
        except Exception as e:
            log.exception(f"Flare download error for {link_id}: {e}")
            rate_limit.register_failure(chat_id)
            stats_fail()
            await _safe_send(status.edit, f"❌ Download failed: {e}")
            return

        dl_time = time.time() - dl_start

        if cancel_event.is_set():
            _cleanup_files(filepath, os.path.splitext(filepath)[0] + ".ts")
            await _safe_send(status.edit, "🚫 Cancelled.")
            return

        # Actual file size on disk
        actual_size = os.path.getsize(filepath)
        size_str = format_size(actual_size)

        # Extract video metadata & thumbnail for streaming
        meta = await asyncio.to_thread(extract_video_metadata, filepath)
        thumb_path = await asyncio.to_thread(generate_video_thumbnail, filepath)
        video_attrs = get_video_attributes(
            filepath,
            duration=meta.get("duration"),
            width=meta.get("width"),
            height=meta.get("height"),
        )

        # — Phase 4: Pre-upload & Upload ———————————————————————————————————
        await _safe_send(
            status.edit,
            f"📦 **{filename}**\n📐 Size: **{size_str}**\n\n⬆️ Uploading… **0%**",
            buttons=cancel_btn,
        )

        ul_start = time.time()
        ul_progress_cb = make_upload_progress_cb(status, filename, size_str, loop, cancel_btn)

        try:
            uploaded_file = await _cancellable(
                _pre_upload_file(filepath, cancel_event=cancel_event, progress_callback=ul_progress_cb),
                cancel_event,
            )
        except (CancelledError, asyncio.CancelledError):
            _cleanup_files(filepath, thumb_path)
            await _safe_send(status.edit, "🚫 Cancelled.")
            return
        except Exception as e:
            log.exception(f"Upload preparation failed for {link_id}")
            _cleanup_files(filepath, thumb_path)
            rate_limit.register_failure(chat_id)
            stats_fail()
            await _safe_send(status.edit, f"❌ Upload failed: {e}")
            return

        if cancel_event.is_set():
            _cleanup_files(filepath, thumb_path)
            await _safe_send(status.edit, "🚫 Cancelled.")
            return

        # Upload to storage channel
        storage_msg = await _upload_to_storage(
            uploaded_file,
            caption=f"📦 `{filename}`",
            file_name=filename,
            thumb=thumb_path,
            attributes=video_attrs,
        )

        # Cache in DB
        if storage_msg is not None:
            file_id_str = str(storage_msg.id)
            await asyncio.to_thread(add_to_cache, link_id, file_id_str, user_mode)

        ul_time = time.time() - ul_start
        total_time = time.time() - total_start
        caption = _build_flare_caption(filename, size_str, dl_time, ul_time, total_time)

        # Deliver to user
        try:
            if storage_msg is not None:
                await _safe_send(
                    bot.send_file,
                    chat_id, storage_msg.media,
                    caption=caption, supports_streaming=True, reply_to=event.message.id,
                    thumb=thumb_path, attributes=video_attrs,
                )
            else:
                await _safe_send(
                    bot.send_file,
                    chat_id, uploaded_file,
                    caption=caption, supports_streaming=True, reply_to=event.message.id,
                    thumb=thumb_path, attributes=video_attrs,
                )
            await _safe_send(status.delete)
        except FloodWaitError as e:
            log.warning(f"FloodWait encountered while sending to {chat_id}: {e.seconds}s")
            terabox_queue.enqueue(
                lambda c=caption: _safe_send(
                    bot.send_file, chat_id,
                    storage_msg.media if storage_msg is not None else uploaded_file,
                    caption=c, supports_streaming=True, reply_to=event.message.id,
                    thumb=thumb_path, attributes=video_attrs,
                ),
                chat_id=chat_id,
            )
            await _safe_send(status.edit, f"⏳ Rate limited by Telegram. Video queued and will be delivered shortly.")
        except Exception as e:
            log.warning(f"Direct send_file failed for {chat_id}, falling back: {e}")
            await _safe_send(status.edit, "❌ Failed to send video.")

        # Cleanup
        _cleanup_files(filepath, thumb_path)

        # Stats & Quota
        stats_ok()
        rate_limit.register_success(chat_id)
        if not is_admin:
            await asyncio.to_thread(bump_today, chat_id)
        await asyncio.to_thread(record_history, chat_id, link_id, user_mode, filename, actual_size)

    finally:
        active_tasks.pop(task_key, None)
        if acquired:
            release_user_slot(chat_id)
