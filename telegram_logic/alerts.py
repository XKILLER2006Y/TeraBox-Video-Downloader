"""
Admin alerts — proactive Telegram DMs to ADMIN_ID for operational problems:
  - TeraBox cookie pool exhausted
  - Diskwala user-session failures
  - Users hitting the retry budget (abuse / upstream trouble)

Thread-safe: pipeline code runs in worker threads, the Telethon client lives
on the asyncio loop. dispatch() schedules the send on the stored loop and
never blocks or raises — alerting must never break a download.

Every alert type is cooldown-throttled so a flapping problem can't spam.
"""
import time
import logging
import asyncio
import threading

log = logging.getLogger(__name__)

try:
    from .helpers import env_int
    ADMIN_ID = env_int("ADMIN_ID")
except Exception:  # pragma: no cover — helpers always importable, belt & suspenders
    import os
    ADMIN_ID = int(os.environ.get("ADMIN_ID", "0") or 0)

_lock = threading.Lock()
_last_sent: dict[str, float] = {}
_loop: asyncio.AbstractEventLoop | None = None

DEFAULT_COOLDOWN = 30 * 60  # same alert key max once per 30 min


def set_alert_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Called once at startup so dispatch() can schedule sends."""
    global _loop
    _loop = loop


def _send_now(text: str) -> None:
    """Fire-and-forget DM to ADMIN_ID on the bot's loop."""
    if _loop is None or _loop.is_closed():
        log.warning(f"Alert dropped (no loop): {text[:80]}")
        return

    async def _deliver():
        try:
            # Late import — avoids circulars at module load
            from .bot import bot
            await bot.send_message(ADMIN_ID, text)
            log.info(f"Alert sent to admin: {text[:60]}")
        except Exception as e:
            log.warning(f"Alert delivery failed: {e}")

    try:
        asyncio.run_coroutine_threadsafe(_deliver(), _loop)
    except RuntimeError as e:
        log.warning(f"Alert scheduling failed: {e}")


def dispatch(alert_text: str, key: str | None = None, cooldown: int = DEFAULT_COOLDOWN) -> None:
    """
    Send alert_text to ADMIN_ID, throttled per key.

    Safe to call from any thread. Never raises.
    """
    if not ADMIN_ID:
        return  # no admin configured — nothing to do
    key = key or alert_text[:40]
    now = time.monotonic()
    with _lock:
        last = _last_sent.get(key, 0.0)
        if now - last < cooldown:
            return
        _last_sent[key] = now
    _send_now(alert_text)
