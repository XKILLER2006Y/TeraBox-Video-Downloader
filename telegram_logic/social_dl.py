"""
telegram_logic/social_dl.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Social media and generic video downloader powered by yt-dlp.
Supports YouTube, Instagram Reels, TikTok (no watermark), Twitter/X,
Facebook, Reddit, Pinterest, Twitch, and more.
"""
import os
import re
import time
import uuid
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
from .progress_callbacks import make_upload_progress_cb
from .structured_log import ctx_logger, bind_context, new_request_id

log = ctx_logger(__name__)

ADMIN_ID = env_int("ADMIN_ID")
DAILY_LIMIT_PER_USER = env_int("DAILY_LIMIT_PER_USER", 0)
SOCIAL_MODE = "social"

# Regex for supported social media platforms
SOCIAL_DOMAINS = (
    r"youtube\.com|youtu\.be|"
    r"instagram\.com|"
    r"tiktok\.com|douyin\.com|"
    r"twitter\.com|x\.com|"
    r"facebook\.com|fb\.watch|"
    r"reddit\.com|v\.redd\.it|"
    r"pinterest\.com|pin\.it|"
    r"twitch\.tv|"
    r"threads\.net|"
    r"vimeo\.com|dailymotion\.com"
)

SOCIAL_URL_RE = re.compile(
    rf"https?://(?:[\w.-]+\.)?(?:{SOCIAL_DOMAINS})/\S+",
    re.IGNORECASE,
)


def is_social_url(url: str) -> bool:
    """Check if a URL belongs to a supported social media platform."""
    return bool(SOCIAL_URL_RE.search(url or ""))


def extract_all_social_urls(text: str) -> list[str]:
    """Extract all unique social media URLs from message text."""
    seen: set[str] = set()
    urls: list[str] = []
    for m in SOCIAL_URL_RE.finditer(text or ""):
        url = m.group(0).rstrip(").,]}\"'")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _sanitize_filename(name: str) -> str:
    """Sanitize title into a safe filename."""
    clean = re.sub(r'[\\/*?:"<>|]', "", name).strip()
    return clean[:100] if clean else "social_video"


def download_social_video_sync(
    url: str,
    output_dir: str,
    cancel_event: threading.Event | None = None,
    progress_cb=None,
) -> tuple[str, str, int]:
    """
    Download a social media video using yt-dlp (synchronous).
    Returns (filepath, title, file_size_bytes).
    """
    import yt_dlp

    os.makedirs(output_dir, exist_ok=True)
    temp_prefix = f"dl_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    outtmpl = os.path.join(output_dir, f"{temp_prefix}_%(title).70s.%(ext)s")

    ydl_opts = {
        "outtmpl": outtmpl,
        # Prefer direct MP4 / H.264 up to 1080p, fallback to best available
        "format": "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]/best[ext=mp4][height<=1080]/best[height<=1080]/best",
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 3,
        "nocheckcertificate": True,
    }

    if progress_cb:
        def _hook(d):
            if cancel_event and cancel_event.is_set():
                raise Exception("Download cancelled by user")
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes") or 0
                if total > 0 and downloaded > 0:
                    progress_cb(downloaded, total)

        ydl_opts["progress_hooks"] = [_hook]

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if not info:
            raise ValueError("yt-dlp could not extract video info")

        title = info.get("title") or "video"
        filename = ydl.prepare_filename(info)
        base, _ = os.path.splitext(filename)
        # Check merged mp4 first, then exact filename
        if os.path.exists(f"{base}.mp4"):
            filepath = f"{base}.mp4"
        elif os.path.exists(filename):
            filepath = filename
        else:
            # Search output_dir for prefix match
            candidates = [
                os.path.join(output_dir, f) for f in os.listdir(output_dir)
                if f.startswith(temp_prefix)
            ]
            if not candidates:
                raise FileNotFoundError("Downloaded video file not found on disk")
            filepath = candidates[0]

        file_size = os.path.getsize(filepath)
        return filepath, _sanitize_filename(title) + ".mp4", file_size


async def process_social(event, url: str) -> None:
    """Public entry point to process a social media URL."""
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
        await terabox_queue.put(_social_helper, event, url)
        try:
            await event.respond(
                "⏳ Bot overloaded! Your request has been queued "
                f"and will be processed automatically in ~{rem}s."
            )
        except FloodWaitError as e:
            terabox_queue.update_flood_until(e.seconds)
        except Exception:
            pass
        return

    # Try processing normally under the semaphore
    async with terabox_queue.semaphore:
        try:
            await _social_helper(event, url)
        except FloodWaitError as e:
            terabox_queue.update_flood_until(e.seconds)
            ahead = terabox_queue.pending
            await terabox_queue.put(_social_helper, event, url)
            try:
                pos = f" (position {ahead + 1})" if ahead else ""
                await event.respond(
                    f"⏳ Bot overloaded! Your request has been queued"
                    f"{pos} and will be processed automatically in ~{e.seconds}s."
                )
            except Exception:
                pass


async def _social_helper(event, url: str) -> None:
    """Inner pipeline for social media video downloads."""
    chat_id = event.chat_id
    link_id = url
    user_mode = SOCIAL_MODE
    task_key = (chat_id, link_id)
    is_admin = bool(ADMIN_ID and chat_id == ADMIN_ID)
    total_start = time.time()

    cancel_btn = [[Button.inline("❌ Cancel", data=f"cancel:{link_id[:30]}")]]

    def _cleanup_files(*paths):
        for p in paths:
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception as e:
                    log.warning(f"Could not clean up {p}: {e}")

    cancel_event = threading.Event()
    acquired = False
    status = None
    filepath = None
    thumb_path = None

    try:
        existing = active_tasks.get(task_key)
        if existing is not None and not existing.is_set():
            await _safe_send(event.respond, "⚠️ This link is already being processed.")
            return

        if not acquire_user_slot(chat_id, is_admin):
            await _safe_send(
                event.respond,
                f"⏳ You already have **{USER_MAX_CONCURRENT}** download(s) running. "
                "Wait for them to finish first.",
            )
            return
        acquired = True
        active_tasks[task_key] = cancel_event

        # — Phase 1: Cache lookup —————————————————————————————————————————————
        status = await _safe_send(event.respond, "🔍 Checking cache…")
        cached_msg = await _find_cached_video(link_id, user_mode)
        if cached_msg is not None:
            try:
                f = cached_msg.file
                fname = f.name if f and f.name else "Video"
                caption = f"📦 `{fname}`"
                await _safe_send(
                    bot.send_file,
                    chat_id, cached_msg.media,
                    caption=caption, supports_streaming=True, reply_to=event.message.id,
                )
                await _safe_send(status.delete)
                return
            except Exception as e:
                log.warning(f"Cache re-send failed for {url}: {e}")

        # — Phase 2: Download with yt-dlp —————————————————————————————————————
        await _safe_send(status.edit, "⏳ Fetching social media video…", buttons=cancel_btn)

        storage_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "storage")
        os.makedirs(storage_dir, exist_ok=True)

        loop = asyncio.get_running_loop()
        dl_start = time.time()

        try:
            filepath, filename, file_size = await asyncio.to_thread(
                download_social_video_sync, url, storage_dir, cancel_event
            )
        except Exception as e:
            log.error(f"yt-dlp download failed for {url}: {e}")
            rate_limit.register_failure(chat_id)
            stats_fail()
            await _safe_send(status.edit, f"❌ Failed to download: {e}")
            return

        dl_time = time.time() - dl_start
        size_str = format_size(file_size)

        # File size limit check
        size_error = check_size_limit(file_size)
        if size_error:
            _cleanup_files(filepath)
            await _safe_send(status.edit, f"📦 **{filename}**\n📐 Size: **{size_str}**\n\n{size_error}")
            return

        if cancel_event.is_set():
            _cleanup_files(filepath)
            await _safe_send(status.edit, "🚫 Cancelled.")
            return

        # Extract video metadata & generate native thumbnail
        meta = await asyncio.to_thread(extract_video_metadata, filepath)
        thumb_path = await asyncio.to_thread(generate_video_thumbnail, filepath)
        video_attrs = get_video_attributes(
            filepath,
            duration=meta.get("duration"),
            width=meta.get("width"),
            height=meta.get("height"),
        )

        # — Phase 3: Upload ———————————————————————————————————————————————————
        up_start = time.time()
        await _safe_send(
            status.edit,
            f"📦 **{filename}**\n📐 Size: **{size_str}**\n\n📤 Uploading…",
            buttons=cancel_btn,
        )
        progress_cb = make_upload_progress_cb(status, filename, size_str, loop, cancel_btn)

        storage_msg = None
        input_file = None

        if STORAGE_GROUP_ID:
            try:
                input_file = await _cancellable(_pre_upload_file(filepath, progress_cb), cancel_event)
                try:
                    storage_msg = await _cancellable(
                        _upload_to_storage(
                            input_file,
                            filename,
                            thumb=thumb_path,
                            attributes=video_attrs,
                        ),
                        cancel_event,
                    )
                    if storage_msg is not None:
                        await asyncio.to_thread(add_to_cache, link_id, storage_msg.id, user_mode)
                except Exception as e:
                    log.error(f"Storage upload failed for {url}: {e}")
            except asyncio.CancelledError:
                _cleanup_files(filepath, thumb_path)
                await _safe_send(status.edit, "🚫 Cancelled.")
                return
            except Exception as e:
                log.error(f"Pre-upload failed for {url}: {e}")
                input_file = None

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
                log.warning(f"Re-send from storage failed for {url}: {e}")

        if sent_video is None:
            upload_source = input_file if input_file else filepath
            up_time = time.time() - up_start
            total_time = time.time() - total_start
            try:
                sent_video = await _cancellable(
                    _safe_send(
                        bot.send_file,
                        chat_id,
                        upload_source,
                        caption=_build_caption(dl_time, up_time, total_time),
                        thumb=thumb_path,
                        attributes=video_attrs,
                        supports_streaming=True,
                        reply_to=event.message.id,
                    ),
                    cancel_event,
                )
            except asyncio.CancelledError:
                _cleanup_files(filepath, thumb_path)
                await _safe_send(status.edit, "🚫 Cancelled.")
                return
            except Exception as e:
                log.error(f"Direct upload failed for {url}: {e}")
                _cleanup_files(filepath, thumb_path)
                await _safe_send(status.edit, f"❌ Upload failed: {e}")
                return

        _cleanup_files(filepath, thumb_path)
        rate_limit.register_success(chat_id)
        stats_ok(file_size)
        await asyncio.to_thread(record_history, chat_id, filename, link_id, file_size)
        await asyncio.to_thread(bump_today, chat_id)

        try:
            await _safe_send(status.delete)
        except Exception:
            pass

    finally:
        _cleanup_files(filepath, thumb_path)
        active_tasks.pop(task_key, None)
        if acquired:
            release_user_slot(chat_id, is_admin)
