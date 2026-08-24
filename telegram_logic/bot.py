import os
import time
import threading
import asyncio
import logging
from telethon import TelegramClient

from firebase_db.cache import search_in_cache

log = logging.getLogger(__name__)

from .queue import MessageQueue  # noqa: E402 — log setup first
from .helpers import env_int  # noqa: E402

# — Concurrency & Flood-Wait Queue ————————————————————————————————————————————————————————
# We still need a semaphore because:
# 1. Unbounded concurrency (e.g. 50 links) will instantly trigger FloodWait before any work gets done.
# 2. Downloading/Uploading 50 videos concurrently will crash a low-spec VPS (OOM or CPU exhaustion).
# 10 is a good high-capacity limit that balances speed with server stability.
terabox_queue = MessageQueue(concurrency_limit=10)

async def _safe_send(*args, **kwargs):
    return await terabox_queue.safe_send(*args, **kwargs)

# — Configuration —————————————————————————————————————————————————————————————————————————————
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
APP_ID = env_int("APP_ID")
API_HASH = os.environ.get("API_HASH", "")
STORAGE_GROUP_ID = env_int("STORAGE_GROUP_ID")

# — Active-task tracking (for cancel) ————————————————————————————————————————————
active_tasks: dict[tuple[int, str], threading.Event] = {}

# — Per-user concurrency cap ———————————————————————————————————————————————————————
# Prevents one spammy user from occupying every download slot.
# Admins are exempt. Slots are held only while a download actually runs.
USER_MAX_CONCURRENT = env_int("USER_MAX_CONCURRENT", 2)

_user_active: dict[int, int] = {}
_user_lock = threading.Lock()


def acquire_user_slot(chat_id: int, is_admin: bool = False) -> bool:
    """Try to reserve one download slot for chat_id. Admins always pass."""
    if is_admin or USER_MAX_CONCURRENT <= 0:
        return True
    with _user_lock:
        current = _user_active.get(chat_id, 0)
        if current >= USER_MAX_CONCURRENT:
            return False
        _user_active[chat_id] = current + 1
        return True


def release_user_slot(chat_id: int, is_admin: bool = False) -> None:
    """Return a reserved slot. Safe to call even if none was acquired."""
    if is_admin or USER_MAX_CONCURRENT <= 0:
        return
    with _user_lock:
        remaining = _user_active.get(chat_id, 0) - 1
        if remaining <= 0:
            _user_active.pop(chat_id, None)
        else:
            _user_active[chat_id] = remaining


# — Graceful-shutdown state ———————————————————————————————————————————————————————
# Set when the bot is shutting down: new downloads are refused, in-flight ones
# get a grace period to finish (see main.py lifespan).
shutting_down = threading.Event()

# Bot process start time (for /status uptime)
START_TIME = time.time()


async def drain_active_tasks(timeout: float = 60.0) -> int:
    """
    Wait for active downloads to finish during shutdown.

    After half the timeout, trip every task's cancel event so stragglers abort
    cleanly instead of being killed mid-write. Returns the number of tasks
    that were still running when we gave up.
    """
    deadline = time.monotonic() + timeout
    warned = False
    while active_tasks and time.monotonic() < deadline:
        if not warned and time.monotonic() > deadline - timeout / 2:
            warned = True
            log.info(f"Shutdown: {len(active_tasks)} download(s) still running — cancelling")
            for ev in list(active_tasks.values()):
                ev.set()
        await asyncio.sleep(0.5)
    return len(active_tasks)

# — Bot Setup ————————————————————————————————————————————————————————————————————————————— 

bot = TelegramClient(
    "terabox_bot",
    APP_ID,
    API_HASH,
    connection_retries=3,
    retry_delay=1,
    auto_reconnect=True,
    flood_sleep_threshold=0,
    request_retries=2,
    timeout=30,
)

# — Cache helpers ——————————————————————————————————————————————————————————————————————————————

async def _find_cached_video(surl: str, user_mode: str):
    """
    Look up surl in the cache buckets using the priority order for user_mode,
    then fetch the message directly by ID.
    Returns the Telethon Message object if found, otherwise None.
    """
    if not STORAGE_GROUP_ID:
        return None
    msg_id = await asyncio.to_thread(search_in_cache, surl, user_mode)
    if msg_id == -1:
        return None
    try:
        msg = await _safe_send(bot.get_messages, STORAGE_GROUP_ID, ids=msg_id)
        if msg and (msg.video or (
            msg.document
            and msg.document.mime_type
            and "video" in msg.document.mime_type
        )):
            return msg
        return None
    except Exception as e:
        log.warning(f"Cache fetch failed for surl={surl} msg_id={msg_id}: {e}")
        return None
    
async def _pre_upload_file(filepath: str, progress_cb=None):
    """
    Upload a file to Telegram's servers and return a reusable InputFile handle.
    This avoids reading from disk multiple times when sending to both
    storage group and user. The handle is valid for ~24h.
    
    For files > 10MB, uses FastTelethon parallel uploads for better performance.
    """
    import os
    from .fast_upload import upload_file_fast, is_large
    
    file_size = os.path.getsize(filepath)
    
    # Use fast parallel uploads for large files
    if is_large(file_size):
        return await upload_file_fast(bot, filepath, progress_cb)
    
    # Small files use standard upload
    return await _safe_send(
        bot.upload_file,
        filepath,
        progress_callback=progress_cb,
    )

async def _upload_to_storage(file, filename: str, progress_cb=None):
    """
    Upload a file to the storage group.
    `file` can be a filepath (str) or a pre-uploaded InputFile handle.
    Caption is set to the video filename.
    Returns the sent Message.
    """
    # If it's a raw filepath, upload normally (with progress).
    # If it's an InputFile handle, progress_callback is ignored (already uploaded).
    kwargs = {}
    if isinstance(file, str) and progress_cb:
        kwargs["progress_callback"] = progress_cb

    return await _safe_send(
        bot.send_file,
        STORAGE_GROUP_ID,
        file,
        caption=filename,
        supports_streaming=True,
        **kwargs,
    )


async def _cancellable(coro, cancel_event: threading.Event, poll_interval: float = 0.5):
    """
    Run `coro` as a task while polling `cancel_event` (threading.Event).
    If the event is set, cancel the task immediately.
    Raises asyncio.CancelledError on cancellation.
    """
    task = asyncio.ensure_future(coro)
    while not task.done():
        if cancel_event.is_set():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            raise asyncio.CancelledError("Upload cancelled by user")
        await asyncio.sleep(poll_interval)
    return task.result()
