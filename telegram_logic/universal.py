"""
Telegram pipeline for universal DL platforms (filesadda, GoFile, StreamTape, etc.).

Follows the exact same 5-phase pattern as terabox_exp.py / diskwala.py:
  Phase 1: Cache lookup (skip for now — universal platforms don't have a cache bucket yet)
  Phase 2: Metadata fetch via universal router
  Phase 3: Download (direct HTTP or HLS)
  Phase 4: Upload to storage group
  Phase 5: Deliver to user
"""
import os
import asyncio
import logging
import threading
import time

import requests

from telegram_logic.bot import (
    _safe_send, STORAGE_GROUP_ID, shutting_down,
    acquire_user_slot, release_user_slot,
)
from telegram_logic import rate_limit
from telegram_logic.helpers import env_int
from telegram_logic.helpers import AUTO_COMPRESS_THRESHOLD_MB  # noqa: F401 (future use)
from firebase_db.stats import record_success as stats_ok, record_failure as stats_fail
from firebase_db.users import record_history, bump_today
from universalDL import resolve_universal, UniversalDL
from network import get_session

logger = logging.getLogger(__name__)

ADMIN_ID = env_int("ADMIN_ID")
DAILY_LIMIT_PER_USER = env_int("DAILY_LIMIT_PER_USER", 0)

# Active tasks for duplicate-request rejection
_active_tasks: dict[tuple[int, str], threading.Event] = {}
_lock = threading.Lock()

# Size limit for universal hosts (default 2 GB)
MAX_SIZE = env_int("UNIVERSAL_MAX_SIZE_MB", 2048) * 1024 * 1024


def _cleanup_files(*paths):
    """Remove temp files. Silently ignores errors."""
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except OSError:
            pass


async def process_universal(event, url: str, bot) -> None:
    """
    5-phase pipeline for universal DL platforms.
    Same structure as process_terabox_experimental / process_diskwala.
    """
    if shutting_down.is_set():
        await _safe_send(event.respond, "🛑 Bot is restarting — please try again in a minute.")
        return

    chat_id = event.chat_id
    task_key = (chat_id, url)

    # Per-user retry budget
    blocked = rate_limit.check_rate_limit(chat_id)
    if blocked:
        await _safe_send(event.respond, blocked)
        return

    status_msg = None
    filepath = None
    cancel_event = threading.Event()
    is_admin = bool(ADMIN_ID and chat_id == ADMIN_ID)

    try:
        # Guards inside try: every denial path flows through the finally that
        # releases the slot — a duplicate can no longer burn concurrency slots.
        if not acquire_user_slot(chat_id, is_admin=is_admin):
            await _safe_send(event.respond, "⏳ You already have downloads running — wait for them to finish.")
            return

        with _lock:
            if task_key in _active_tasks and not _active_tasks[task_key].is_set():
                await _safe_send(event.reply, "⚠️ This link is already being processed.")
                return
            _active_tasks[task_key] = cancel_event
        # ── Phase 1: Cache lookup (placeholder for future) ────────────────────────────────

        # ── Phase 2: Metadata fetch ─────────────────────────────────────────────────————————
        status_msg = await _safe_send(event.respond, "🔍 Resolving link...")

        try:
            info = await asyncio.to_thread(resolve_universal, url)
        except UniversalDL as e:
            rate_limit.register_failure(chat_id)
            stats_fail()
            await _safe_send(status_msg.edit, f"❌ Resolution failed: {e}")
            return

        filename = info.get("filename", "download")
        filesize = info.get("size", 0)
        download_url = info.get("download_url", "")
        extra_headers = info.get("headers", {})

        if not download_url:
            await _safe_send(status_msg.edit, "❌ Could not extract download URL.")
            return

        # Size check
        if filesize and filesize > MAX_SIZE:
            size_mb = filesize / (1024 * 1024)
            await _safe_send(
                status_msg.edit,
                f"❌ File too large: {size_mb:.1f} MB (limit {MAX_SIZE // (1024*1024)} MB)",
            )
            return

        # Status update with file info
        size_str = f"{filesize / (1024*1024):.1f} MB" if filesize else "unknown size"
        await _safe_send(
            status_msg.edit,
            f"📦 {filename}\n📐 Size: {size_str}\n\n⬇️ Downloading... 0%"
        )

        # ── Phase 3: Download ─────────────────────────────────────────────────────────———————
        download_start = time.time()

        filepath = await asyncio.to_thread(
            _download_file, download_url, filename, extra_headers, cancel_event
        )

        if not filepath:
            rate_limit.register_failure(chat_id)
            stats_fail()
            await _safe_send(status_msg.edit, "❌ Download failed — no file produced.")
            return

        dl_time = time.time() - download_start
        actual_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
        logger.info(f"Downloaded {filename} ({actual_size / (1024*1024):.1f} MB) in {dl_time:.1f}s")

        await _safe_send(
            status_msg.edit,
            f"📦 {filename}\n📐 Size: {actual_size / (1024*1024):.1f} MB\n\n⬆️ Uploading..."
        )

        # ── Phase 4: Upload to storage group (cache for future) ─────────────────———————
        storage_msg = None
        if STORAGE_GROUP_ID:
            try:
                storage_msg = await asyncio.wait_for(
                    bot.send_file(STORAGE_GROUP_ID, filepath, caption=filename, force_document=True),
                    timeout=300,
                )
                logger.info(f"Stored in warehouse: msg_id={storage_msg.id}")
            except Exception as e:
                logger.warning(f"Storage upload failed (non-fatal): {e}")
                storage_msg = None

        # ── Phase 5: Deliver to user ─────────────────────────────────────────────────—————
        if storage_msg:
            await bot.send_file(chat_id, storage_msg.media, caption=f"✅ {filename}")
        else:
            await asyncio.wait_for(
                bot.send_file(chat_id, filepath,
                              caption=f"✅ {filename}", force_document=True),
                timeout=300,
            )

        await _safe_send(status_msg.edit, f"✅ {filename} — delivered!")
        rate_limit.register_success(chat_id)
        stats_ok(actual_size)
        await asyncio.to_thread(record_history, chat_id, filename, url, actual_size)
        await asyncio.to_thread(bump_today, chat_id)
        logger.info(f"Delivered {filename} to {chat_id} in {time.time() - download_start:.1f}s")

    except asyncio.CancelledError:
        logger.info(f"Task cancelled: {task_key}")
        if filepath and os.path.exists(filepath):
            await asyncio.to_thread(_cleanup_files, filepath)
    except Exception as e:
        if cancel_event.is_set():
            logger.info(f"Cancelled mid-flight: {task_key}")
            await _safe_send(event.respond, "🚫 Cancelled.")
        else:
            logger.error(f"Universal DL error for {url}: {e}", exc_info=True)
            rate_limit.register_failure(chat_id)
            stats_fail()
        if status_msg:
            await _safe_send(status_msg.edit, f"❌ Error: {e}")
    finally:
        with _lock:
            _active_tasks.pop(task_key, None)
        release_user_slot(chat_id)
        if filepath and os.path.exists(filepath):
            await asyncio.to_thread(_cleanup_files, filepath)


def _download_file(url: str, filename: str, headers: dict, cancel_event: threading.Event) -> str | None:
    """Download a file via HTTP with progress tracking and retry backoff."""
    dl_dir = os.path.join(os.path.dirname(__file__), '..', 'storage')
    os.makedirs(dl_dir, exist_ok=True)

    # Sanitize filename
    safe_name = "".join(c for c in filename if c.isalnum() or c in "._- ").strip()
    if not safe_name:
        safe_name = "download"
    unique = f"{int(time.time() * 1000) % 10_000_000_000}_{safe_name}"
    filepath = os.path.join(dl_dir, unique)

    max_retries = 3
    for attempt in range(max_retries):
        if cancel_event.is_set():
            return None

        try:
            session = get_session()
            resp = session.get(url, headers=headers, stream=True, timeout=300)
            resp.raise_for_status()

            with open(filepath, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if cancel_event.is_set():
                        logger.info("Download cancelled")
                        resp.close()
                        os.remove(filepath)
                        return None
                    if chunk:
                        f.write(chunk)

            if os.path.getsize(filepath) > 0:
                return filepath
            return None

        except requests.RequestException as e:
            logger.warning(f"Download attempt {attempt + 1}/{max_retries} failed: {e}")
            if os.path.exists(filepath):
                os.remove(filepath)
            if attempt < max_retries - 1:
                backoff = 2 ** attempt
                time.sleep(backoff)

    return None
