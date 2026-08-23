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

from telegram_logic.bot import (
    _safe_send, STORAGE_GROUP_ID,
)
from universalDL import resolve_universal, UniversalDL

logger = logging.getLogger(__name__)

# Active tasks for duplicate-request rejection
_active_tasks: dict[tuple[int, str], threading.Event] = {}
_lock = threading.Lock()

# Size limit: 2 GB
MAX_SIZE = 2 * 1024 * 1024 * 1024


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
    chat_id = event.chat_id
    task_key = (chat_id, url)

    # ── Duplicate rejection ───────────────────────────────────────────────
    with _lock:
        if task_key in _active_tasks and not _active_tasks[task_key].is_set():
            await _safe_send(event.reply, "⚠️ This link is already being processed.")
            return
        cancel_event = threading.Event()
        _active_tasks[task_key] = cancel_event

    status_msg = None
    filepath = None

    try:
        # ── Phase 1: Cache lookup (placeholder for future) ────────────────

        # ── Phase 2: Metadata fetch ───────────────────────────────────────
        status_msg = await _safe_send(event.respond, "🔍 Resolving link...")

        try:
            info = await asyncio.to_thread(resolve_universal, url)
        except UniversalDL as e:
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
            await _safe_send(status_msg.edit, f"❌ File too large: {size_mb:.1f} MB (limit 2 GB)")
            return

        # Status update with file info
        size_str = f"{filesize / (1024*1024):.1f} MB" if filesize else "unknown size"
        await _safe_send(
            status_msg.edit,
            f"📦 {filename}\n📐 Size: {size_str}\n\n⬇️ Downloading... 0%"
        )

        # ── Phase 3: Download ─────────────────────────────────────────────
        download_start = time.time()

        filepath = await asyncio.to_thread(
            _download_file, download_url, filename, extra_headers, cancel_event
        )

        if not filepath:
            await _safe_send(status_msg.edit, "❌ Download failed — no file produced.")
            return

        dl_time = time.time() - download_start
        actual_size = os.path.getsize(filepath) if os.path.exists(filepath) else 0
        logger.info(f"Downloaded {filename} ({actual_size / (1024*1024):.1f} MB) in {dl_time:.1f}s")

        await _safe_send(
            status_msg.edit,
            f"📦 {filename}\n📐 Size: {actual_size / (1024*1024):.1f} MB\n\n⬆️ Uploading..."
        )

        # ── Phase 4: Upload to storage group (cache for future) ───────────
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

        # ── Phase 5: Deliver to user ──────────────────────────────────────
        if storage_msg:
            await bot.send_file(chat_id, storage_msg.media, caption=f"✅ {filename}")
        else:
            await asyncio.wait_for(
                bot.send_file(chat_id, filepath, caption=f"✅ {filename}", force_document=True),
                timeout=300,
            )

        await _safe_send(status_msg.edit, f"✅ {filename} — delivered!")
        logger.info(f"Delivered {filename} to {chat_id} in {time.time() - download_start:.1f}s")

    except asyncio.CancelledError:
        logger.info(f"Task cancelled: {task_key}")
    except Exception as e:
        logger.error(f"Universal DL error for {url}: {e}", exc_info=True)
        if status_msg:
            await _safe_send(status_msg.edit, f"❌ Error: {e}")
    finally:
        with _lock:
            _active_tasks.pop(task_key, None)
        if filepath and os.path.exists(filepath):
            asyncio.get_event_loop().run_in_executor(None, _cleanup_files, filepath)


def _download_file(url: str, filename: str, headers: dict, cancel_event: threading.Event) -> str | None:
    """Download a file via HTTP with progress tracking."""
    import requests as req

    dl_dir = os.path.join(os.path.dirname(__file__), '..', 'storage')
    os.makedirs(dl_dir, exist_ok=True)

    # Sanitize filename
    safe_name = "".join(c for c in filename if c.isalnum() or c in "._- ").strip()
    if not safe_name:
        safe_name = "download"
    filepath = os.path.join(dl_dir, safe_name)

    dl_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131',
        **headers,
    }

    try:
        resp = req.get(url, headers=dl_headers, stream=True, timeout=300)
        resp.raise_for_status()

        with open(filepath, 'wb') as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if cancel_event.is_set():
                    logger.info("Download cancelled")
                    return None
                if chunk:
                    f.write(chunk)

        return filepath if os.path.getsize(filepath) > 0 else None

    except req.RequestException as e:
        logger.error(f"Download failed: {e}")
        if os.path.exists(filepath):
            os.remove(filepath)
        return None
