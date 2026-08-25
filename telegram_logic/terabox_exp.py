import os
import time
import base64
import threading
import asyncio
import tempfile
from telethon import Button
from telethon.errors import FloodWaitError

from .bot import bot, _find_cached_video, _pre_upload_file, _upload_to_storage, _cancellable, terabox_queue, _safe_send, active_tasks, STORAGE_GROUP_ID, shutting_down, acquire_user_slot, release_user_slot, USER_MAX_CONCURRENT
from .helpers import format_size, format_duration, extract_surl_exp, check_size_limit, DEFAULT_QUALITY, env_int, AUTO_COMPRESS_THRESHOLD_MB
from . import rate_limit
from firebase_db.cache import add_to_cache
from firebase_db.stats import record_success as stats_ok, record_failure as stats_fail
from firebase_db.users import record_history, get_today_count, bump_today
from .progress_callbacks import make_download_progress_cb, make_upload_progress_cb
from .structured_log import ctx_logger, bind_context, new_request_id
from .compress import maybe_compress

from teraboxDL.errors import TeraBoxError, CancelledError, TeraBoxDirectError, TeraBoxMultipleChoice
from teraboxDL.public_api import download_terabox_file_experimental
from teraboxDL.terabox_dl import get_video_info

ADMIN_ID = env_int("ADMIN_ID")
DAILY_LIMIT_PER_USER = env_int("DAILY_LIMIT_PER_USER", 0)

log = ctx_logger(__name__)

# — Heart Function —————————————————————————————————————————————————————————————

#! ONLY PUBLIC API
async def process_terabox_experimental(
    event,
    terabox_url: str,
    is_hd: bool = False,
    quality: str | None = None,
    fs_id: int | None = None,
    compress: bool = False,
) -> None:
    rid = new_request_id()
    bind_context(request_id=rid, user_id=event.chat_id, download_id=rid)
    if shutting_down.is_set():
        await _safe_send(event.respond, "🛑 Bot is restarting — please try again in a minute.")
        return

    # Per-user retry budget
    blocked = rate_limit.check_rate_limit(event.chat_id)
    if blocked:
        await _safe_send(event.respond, blocked)
        return

    # Daily download quota (0 = unlimited)
    if DAILY_LIMIT_PER_USER > 0 and not (ADMIN_ID and event.chat_id == ADMIN_ID):
        used = await asyncio.to_thread(get_today_count, event.chat_id)
        if used >= DAILY_LIMIT_PER_USER:
            await _safe_send(
                event.respond,
                f"📊 **Daily limit reached** ({used}/{DAILY_LIMIT_PER_USER} downloads).\n"
                "The counter resets at midnight UTC. Come back tomorrow!",
            )
            return

    # If currently in flood cooldown → queue immediately
    rem = terabox_queue.flood_remaining()
    if rem > 0:
        ahead = terabox_queue.pending
        await terabox_queue.put(helper, event, terabox_url, is_hd, quality or DEFAULT_QUALITY, fs_id, compress)
        try:
            pos = f" (position {ahead + 1})" if ahead else ""
            await event.respond(
                "⏳ Bot overloaded! Your request has been queued"
                f"{pos} and will be processed automatically in ~{rem}s."
            )
        except FloodWaitError as e:
            terabox_queue.update_flood_until(e.seconds)
        except Exception:
            pass
        return

    # Try processing normally under the semaphore
    async with terabox_queue.semaphore:
        try:
            await helper(event, terabox_url, is_hd, quality or DEFAULT_QUALITY, fs_id, compress)
        except FloodWaitError as e:
            # Pipeline hit flood → set cooldown, queue, notify user
            terabox_queue.update_flood_until(e.seconds)
            await terabox_queue.put(helper, event, terabox_url, is_hd, quality or DEFAULT_QUALITY, fs_id, compress)
            try:
                await event.respond(
                    f"⏳ Bot overloaded! Your request has been queued "
                    f"and will be processed automatically in ~{e.seconds}s."
                )
            except Exception:
                pass


async def helper(event, terabox_url: str, is_hd: bool, quality: str = DEFAULT_QUALITY, fs_id: int | None = None, compress: bool = False) -> None:
    """Inner pipeline, runs under the concurrency semaphore."""
    chat_id = event.chat_id
    surl = extract_surl_exp(terabox_url)
    user_mode = "exphd" if is_hd else "exp"
    # Multi-file picks get their own cache/track key so two files from one
    # share never collide.
    cache_key = surl if fs_id is None else f"{surl}-{fs_id}"
    task_key = (chat_id, cache_key)
    total_start = time.time()
    is_admin = bool(ADMIN_ID and chat_id == ADMIN_ID)

    # Reject duplicate concurrent requests for the same link from this chat —
    # a second registration would orphan the first task's cancel event.
    existing = active_tasks.get(task_key)
    if existing is not None and not existing.is_set():
        await _safe_send(event.respond, f"⚠️ `{surl}` is already being processed. Use the ❌ button on that message to cancel it first.")
        return

    cancel_event = threading.Event()
    active_tasks[task_key] = cancel_event

    if not acquire_user_slot(chat_id, is_admin):
        await _safe_send(event.respond,
            f"⏳ You already have **{USER_MAX_CONCURRENT}** download(s) running. "
            "Wait for them to finish first (or they'll finish on their own).")
        return

    cancel_btn = [[Button.inline("❌ Cancel", data=f"cancel:{cache_key}")]]

    def _cleanup_files(*paths):
        """Remove temp/downloaded files from disk."""
        for p in paths:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                    log.info(f"Cleaned up file: {p}")
                except Exception as e:
                    log.warning(f"Could not clean up {p}: {e}")

    # Temp artifacts for this task: downloaded file, remux leftover, local M3U8
    m3u8_tmp = os.path.join(tempfile.gettempdir(), f"terabox_{cache_key}.m3u8")

    def _cleanup_all(filepath=None):
        paths = [filepath, os.path.splitext(filepath)[0] + ".ts" if filepath else None,
                 m3u8_tmp]
        _cleanup_files(*paths)

    try:
        # — Phase 1: Cache lookup ——————————————————————————————————————————————————————————————
        status = await _safe_send(event.respond, f"🔍 Checking cache for `{surl}`…")

        cached_msg = await _find_cached_video(surl, user_mode)
        if cached_msg is not None:
            try:
                f = cached_msg.file
                fname = (f.name if f and f.name else surl)
                caption = f"📦 `{fname}`"
                await _safe_send(
                    bot.send_file,
                    chat_id, cached_msg.media,
                    caption=caption, supports_streaming=True, reply_to=event.message.id,
                )
                await _safe_send(status.delete)
            except Exception as e:
                log.warning(f"re-send failed for surl={surl}: {e}")
                await _safe_send(status.edit, "❌ Failed to send video.")
            return

        # — Phase 2: Prepare metadata ————————————————————————————————————————————————————
        await _safe_send(status.edit, "⏳ Fetching metadata…", buttons=cancel_btn)

        #! GET FILE INFO
        try:
            info = await asyncio.to_thread(get_video_info, terabox_url, is_hd, quality, fs_id)
        except TeraBoxMultipleChoice as mc:
            # Multi-file share without a pick → show inline chooser
            buttons = _build_file_picker(mc.files, surl)
            if buttons:
                await _safe_send(
                    status.edit,
                    f"📂 This share contains **{len(mc.files)}** files — pick one:",
                    buttons=buttons,
                )
            else:
                await _safe_send(status.edit, "❌ Share contains too many files to list. Ask the admin to split it.")
            return
        except TeraBoxDirectError as e:
            log.error(f"Metadata fetch failed for surl={surl}: {e}")
            rate_limit.register_failure(chat_id)
            stats_fail()
            await _safe_send(status.edit, f"❌ {e}\n\nYou can try different *mode* to download.\nSwitch *mode* from /settings")
            return
        except Exception as e:
            log.error(f"Metadata fetch failed for surl={surl}: {e}")
            rate_limit.register_failure(chat_id)
            stats_fail()
            await _safe_send(status.edit, f"❌ Failed to get video info: {e}\n\nYou can try different *mode* to download.\nSwitch *mode* from /settings")
            return

        download_url = info["download_url"]
        filename = info["filename"]
        size_str = format_size(info["size"])

        # — File size limit ————————————————————————————————————————————————————————————————
        size_error = check_size_limit(info["size"])
        if size_error:
            log.info(f"Size limit hit for surl={surl}: {info['size']} bytes")
            await _safe_send(
                status.edit,
                f"📦 **{filename}**\n📐 Size: **{size_str}**\n\n{size_error}",
            )
            return

        # Thumbnail preview (best-effort — never block the pipeline)
        thumb_url = info.get("thumb_url") or ""
        if thumb_url:
            try:
                await _cancellable(_safe_send(
                    bot.send_file,
                    chat_id, thumb_url,
                    caption=f"🎬 **{filename}**\n📐 Size: **{size_str}**",
                    reply_to=event.message.id,
                ), cancel_event)
            except Exception:
                pass  # preview is cosmetic

        # Single status update — a separate "Quality:" edit would be
        # instantly overwritten and just burns an API call.
        quality_line = f"\n🎯 Quality: **{quality.split('_')[-1]}**\n" if (quality != DEFAULT_QUALITY and not is_hd) else "\n"
        await _safe_send(
            status.edit,
            f"📦 **{filename}**\n📐 Size: **{size_str}**{quality_line}\n⬇️ Downloading… **0%**",
            buttons=cancel_btn,
        )

        # — Phase 3: Download ——————————————————————————————————————————————————
        loop = asyncio.get_running_loop()
        dl_start = time.time()
        dl_progress_cb = make_download_progress_cb(status, filename, size_str, loop, cancel_btn)
        try:
            filepath = await asyncio.to_thread(download_terabox_file_experimental, download_url, filename, cancel_event, dl_progress_cb)
        except CancelledError:
            await _safe_send(status.edit, "🚫 Cancelled.")
            return
        except TeraBoxError as e:
            log.error(f"Download error for surl={surl}: {e}")
            rate_limit.register_failure(chat_id)
            stats_fail()
            await _safe_send(status.edit, f"❌ Download failed: {e}\n\nYou can try different *mode* to download.\nSwitch *mode* from /settings")
            return
        except Exception as e:
            log.exception(f"Unexpected download error for surl={surl}")
            rate_limit.register_failure(chat_id)
            stats_fail()
            await _safe_send(status.edit, f"❌ Download failed: {e}\n\nYou can try different *mode* to download.\nSwitch *mode* from /settings")
            return
        dl_time = time.time() - dl_start

        if cancel_event.is_set():
            _cleanup_all(filepath)
            await _safe_send(status.edit, "🚫 Cancelled.")
            return

        # — Phase 3b: optional H.264 compression ————————————————————————————
        auto_trigger = (
            not compress
            and AUTO_COMPRESS_THRESHOLD_MB > 0
            and os.path.getsize(filepath) > AUTO_COMPRESS_THRESHOLD_MB * 1024 * 1024
        )
        if compress or auto_trigger:
            await _safe_send(status.edit, "🗜️ Compressing video… (this can take a while)")
            try:
                filepath, saved_note = await asyncio.to_thread(maybe_compress, filepath, cancel_event)
            except CancelledError:
                await _safe_send(status.edit, "🚫 Cancelled.")
                return
            if saved_note:
                log.info("compression done", extra={"note": saved_note})

        # Use actual file size (compressed TS/MP4) instead of original API size
        size_str = format_size(os.path.getsize(filepath))

        # — Phase 4: Upload to storage group (cache) ———————————————————————————————————————
        if cancel_event.is_set():
            _cleanup_all(filepath)
            await _safe_send(status.edit, "🚫 Cancelled.")
            return

        up_start = time.time()
        storage_msg = None
        input_file = None  # reusable Telegram upload handle

        if STORAGE_GROUP_ID:
            await _safe_send(
                status.edit,
                f"📦 **{filename}**\n📐 Size: **{size_str}**\n\n📤 Uploading **0%**",
                buttons=cancel_btn,
            )
            progress_cb = make_upload_progress_cb(status, filename, size_str, loop, cancel_btn)
            try:
                # Upload file bytes to Telegram ONCE → get reusable InputFile handle
                input_file = await _cancellable(_pre_upload_file(filepath, progress_cb), cancel_event)
                try:
                    storage_msg = await _cancellable(_upload_to_storage(input_file, filename), cancel_event)
                    if storage_msg is not None:
                        await asyncio.to_thread(add_to_cache, surl, storage_msg.id, user_mode)
                except Exception as e:
                    # Keep input_file — the handle is still valid for direct delivery
                    log.error(f"Storage send failed (pre-upload kept) for surl={surl}: {e}")
            except asyncio.CancelledError:
                log.info(f"Upload cancelled by user for surl={surl}")
                _cleanup_all(filepath)
                await _safe_send(status.edit, "🚫 Cancelled.")
                return
            except Exception as e:
                log.error(f"Pre-upload failed for surl={surl}: {e}")
                input_file = None  # upload itself failed → fallback re-uploads from disk
                # storage_msg stays None → fall back to direct upload below

        # — Phase 5: Deliver to user ———————————————————————————————————————————————————————
        def _build_caption(dl_t: float, up_t: float, total_t: float) -> str:
            return (
                f"📦 `{filename}`\n"
                f"📐 Size: **{size_str}**\n\n"
                f"⬇️ Download: **{format_duration(dl_t)}**\n"
                f"📤 Upload: **{format_duration(up_t)}**\n"
                f"⏱️ Total: **{format_duration(total_t)}**"
            )

        sent_video = None

        if storage_msg is not None:
            up_time = time.time() - up_start
            total_time = time.time() - total_start
            try:
                sent_video = await _safe_send(
                    bot.send_file,
                    chat_id,
                    storage_msg.media,
                    caption=_build_caption(dl_time, up_time, total_time),
                    supports_streaming=True,
                    reply_to=event.message.id,
                )
            except Exception as e:
                log.warning(f"Re-send from storage failed for surl={surl}, sending directly: {e}")

        if sent_video is None:
            # Use the pre-uploaded handle if available, otherwise fall back to disk
            upload_source = input_file if input_file else filepath
            needs_progress = input_file is None  # only show progress if re-uploading from disk

            if needs_progress:
                try:
                    await _safe_send(
                        status.edit,
                        f"📦 **{filename}**\n📐 Size: **{size_str}**\n\n📤 Uploading… **0%**",
                        buttons=cancel_btn,
                    )
                except Exception as e:
                    log.warning(f"Failed to update status for upload progress: {e}")
            progress_cb = make_upload_progress_cb(status, filename, size_str, loop, cancel_btn) if needs_progress else None
            up_start = time.time()
            try:
                kwargs = {}
                if progress_cb:
                    kwargs["progress_callback"] = progress_cb
                sent_video = await _cancellable(
                    _safe_send(
                        bot.send_file,
                        chat_id,
                        upload_source,
                        caption=f"📦 `{filename}`\n📐 Size: **{size_str}**",
                        supports_streaming=True,
                        reply_to=event.message.id,
                        **kwargs,
                    ),
                    cancel_event,
                )
                up_time = time.time() - up_start
                total_time = time.time() - total_start
                try:
                    await _safe_send(sent_video.edit, _build_caption(dl_time, up_time, total_time))
                except Exception:
                    pass
            except asyncio.CancelledError:
                log.info(f"Direct upload cancelled by user for surl={surl}")
                _cleanup_all(filepath)
                await _safe_send(status.edit, "🚫 Cancelled.")
                return
            except Exception as e:
                log.error(f"Direct upload failed for surl={surl}: {e}")
                _cleanup_all(filepath)
                await _safe_send(status.edit, f"❌ Upload failed: {e}\n\nYou can try different *mode* to download.\nSwitch *mode* from /settings")
                return

        _cleanup_all(filepath)
        rate_limit.register_success(chat_id)
        file_size = os.path.getsize(filepath) if filepath and os.path.exists(filepath) else 0
        stats_ok(file_size)
        await asyncio.to_thread(record_history, chat_id, filename, cache_key, file_size)
        await asyncio.to_thread(bump_today, chat_id)

        try:
            await _safe_send(status.delete)
        except Exception:
            pass

    finally:
        active_tasks.pop(task_key, None)
        release_user_slot(chat_id, is_admin)


# ── Multi-file picker ──────────────────────────────────────────────────────────

_b64 = base64


def _b64e(s: str) -> str:
    return _b64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


def _b64d(s: str) -> str:
    return _b64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode()


def _build_file_picker(files: list[dict], surl: str):
    """Inline keyboard listing share files (max 10 videos) + Download-all."""
    from telethon import Button
    rows = []
    shown = 0
    for f in files:
        if not f.get("is_video"):
            continue
        if shown >= 10:
            break
        data = f"tpick:{_b64e(surl)}:{f['fs_id']}".encode()
        if len(data) > 64:
            continue
        label = f"🎬 {f['name'][:36]} · {format_size(f['size'])}"
        rows.append([Button.inline(label, data=data)])
        shown += 1
    if shown > 1:
        all_data = f"tpickall:{_b64e(surl)}".encode()
        if len(all_data) <= 64:
            rows.append([Button.inline(f"⬇️ Download all {shown} videos", data=all_data)])
    return rows or None
