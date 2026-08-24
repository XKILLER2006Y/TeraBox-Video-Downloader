"""
Per-user retry budget — sliding-window failure tracking.

Users who repeatedly hit failures are put on a cooldown instead of being able
to hammer the bot (and upstream services) with doomed requests. Successes
clear a user's history.

All state is in-memory and thread-safe; counters reset on restart, which is
fine for an abuse-mitigation mechanism.
"""
import threading
import time
import logging
from collections import deque

from .helpers import env_int

log = logging.getLogger(__name__)

# ── Tunables (env-configurable) ────────────────────────────────────────────────────────────────
MAX_FAILURES = env_int("MAX_FAILURES_PER_WINDOW", 5)     # failures allowed…
FAILURE_WINDOW = env_int("FAILURE_WINDOW_SECONDS", 600)  # …within this window (s)
COOLDOWN_SECONDS = env_int("FAILURE_COOLDOWN_SECONDS", 600)  # block duration

_lock = threading.Lock()
_failures: dict[int, deque[float]] = {}
_blocked_until: dict[int, float] = {}


def check_rate_limit(chat_id: int) -> str | None:
    """
    Return a user-facing warning string if chat_id is over budget or in
    cooldown, else None (allowed to proceed).
    """
    if MAX_FAILURES <= 0:
        return None  # feature disabled
    now = time.time()
    with _lock:
        until = _blocked_until.get(chat_id, 0.0)
        if until > now:
            remaining = int(until - now)
            minutes = max(1, remaining // 60)
            return (
                f"⏳ Too many failed downloads recently.\n\n"
                f"Please wait **~{minutes} min** before trying again."
            )
    return None


def register_failure(chat_id: int) -> None:
    """Record a failed download for chat_id; block them when over budget."""
    now = time.time()
    with _lock:
        dq = _failures.setdefault(chat_id, deque())
        dq.append(now)
        # Trim entries outside the sliding window
        while dq and dq[0] < now - FAILURE_WINDOW:
            dq.popleft()
        if len(dq) >= MAX_FAILURES:
            _blocked_until[chat_id] = now + COOLDOWN_SECONDS
            dq.clear()
            log.warning(f"User {chat_id} blocked for {COOLDOWN_SECONDS}s after {MAX_FAILURES} failures")


def register_success(chat_id: int) -> None:
    """A completed download clears the user's failure history."""
    with _lock:
        _failures.pop(chat_id, None)
        _blocked_until.pop(chat_id, None)


def stats() -> dict:
    """Snapshot for /status."""
    now = time.time()
    with _lock:
        blocked = sum(1 for until in _blocked_until.values() if until > now)
    return {
        "tracked_users_with_failures": len(_failures),
        "currently_blocked": blocked,
    }
