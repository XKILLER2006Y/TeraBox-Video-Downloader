import time
import asyncio
from collections.abc import Callable
from .helpers import format_size

# — Progress callback for Telethon uploads ———————————————————————————————————————————————————————

def make_download_progress_cb(
    status_msg,
    filename: str,
    size_str: str,
    loop: asyncio.AbstractEventLoop,
    cancel_btn=None,
    expected_total: int = 0,
) -> Callable[[int, int], None]:
    """
    Create a progress callback for the download phase.

    HLS pipelines report total=0 (unknown until manifest parse). Without an
    expected_total fallback the throttle predicate (`current < total`) is
    always False and EVERY chunk from EVERY worker schedules a Telegram
    edit — an edit flood that burns API budget. expected_total (from
    metadata) restores both the percentage display and the 5s gate.
    """
    last_update = [0.0]

    async def _update(text):
        try:
            await status_msg.edit(text, buttons=cancel_btn)
        except Exception:
            pass

    def callback(current, total):
        effective_total = total or expected_total
        now = time.time()
        # Throttle to one edit / 5s; always allow the final update through.
        done = bool(effective_total) and current >= effective_total
        if (now - last_update[0] < 5) and not done:
            return
        last_update[0] = now
        pct = current / effective_total * 100 if effective_total else 0
        downloaded = format_size(current)
        total_str = format_size(effective_total) if effective_total else size_str
        text = (
            f"📦 **{filename}**\n"
            f"📐 Size: **{total_str}**\n\n"
            f"⬇️ Downloading… **{pct:.0f}%** ({downloaded} / {total_str})"
        )
        asyncio.run_coroutine_threadsafe(_update(text), loop)

    return callback


def make_upload_progress_cb(
    status_msg,
    filename: str,
    size_str: str,
    loop: asyncio.AbstractEventLoop,
    cancel_btn=None,
) -> Callable[[int, int], None]:
    """Create a progress callback for Telethon file upload."""
    last_update = [0.0]  # track last update time to avoid flooding

    async def _update(text):
        try:
            await status_msg.edit(text, buttons=cancel_btn)
        except Exception:
            pass

    def callback(current, total):
        now = time.time()
        # Update every 5 seconds, or when the transfer is complete (current == total)
        if (now - last_update[0] < 5) and (current < total):
            return
        last_update[0] = now
        pct = current / total * 100 if total else 0
        uploaded = format_size(current)
        text = (
            f"📦 **{filename}**\n"
            f"📐 Size: **{size_str}**\n\n"
            f"📤 Uploading… **{pct:.0f}%** ({uploaded} / {size_str})"
        )
        asyncio.run_coroutine_threadsafe(_update(text), loop)

    return callback