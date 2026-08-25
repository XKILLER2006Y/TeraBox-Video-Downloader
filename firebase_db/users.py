"""
firebase_db/users.py
~~~~~~~~~~~~~~~~~~~~
Firestore-backed user tracking, replacing the old GitHub Gist approach.

Firestore layout
----------------
Collection : users
Document   : <chat_id>          (string)
Fields     :
    username    : str | None
    last_active : float          (Unix timestamp)
    mode        : "get" | "exp" | "exphd"

Why per-document instead of one giant document?
  - Partial updates: update a single user's field without touching anyone else.
  - No read-modify-write cycle needed for most operations.
  - Scales naturally with user growth.
"""

import logging
import time
from typing import Literal

from google.cloud.firestore_v1 import DELETE_FIELD  # noqa: F401 — available if needed

from .db import get_db

log = logging.getLogger(__name__)

# ── Types ─────────────────────────────────────────────────────────────────────────—————

MODE = Literal["exp", "exphd", "dw"]

# Legacy modes mapped to their modern equivalents on read.
_MODE_MIGRATION = {"get": "exp"}  # /get (legacy proxy pipeline) was removed

# ── In-memory cache (reduces Firestore reads) ─────────────────────────—————————
# Structure: { str(chat_id): {"username": ..., "last_active": float, "mode": ...} }
# Populated on first read; kept in sync on every write.
# Capped at 1000 entries — evicts oldest by last_active when full.
_USERS_CACHE: dict[str, dict] = {}
_USERS_CACHE_MAX = 1000

_USERS_COLLECTION = "users"
_WRITE_DEBOUNCE_SECONDS = 900  # 15 minutes — same throttle as the old Gist impl


def _evict_if_needed():
    """Evict oldest entry by last_active if cache exceeds max size."""
    if len(_USERS_CACHE) <= _USERS_CACHE_MAX:
        return
    oldest_uid = min(_USERS_CACHE, key=lambda k: _USERS_CACHE[k].get("last_active", 0))
    del _USERS_CACHE[oldest_uid]


# ── Public API ─────────────────────────────────────────────────────────────────

def track_user(chat_id: int, username: str | None) -> None:
    """
    Record / refresh a user's activity in Firestore.

    Writes are debounced to AT MOST once every 15 minutes per user to stay
    within Firestore free-tier write quotas (and because last_active precision
    below 15 min is irrelevant for /recent).

    New users are always written immediately.

    Errors are caught and logged — never propagated to callers.
    """
    uid = str(chat_id)
    current_time = time.time()

    cached = _USERS_CACHE.get(uid, {})
    last_saved = cached.get("last_active", 0.0)

    is_new_user = not cached  # nothing in local cache → unknown → check Firestore
    is_stale    = (current_time - last_saved) >= _WRITE_DEBOUNCE_SECONDS

    if not is_new_user and not is_stale:
        return  # Skip — within debounce window

    try:
        ref = get_db().collection(_USERS_COLLECTION).document(uid)

        if is_new_user:
            # Cold-start: check Firestore to avoid overwriting an existing user
            snap = ref.get()
            if snap.exists:
                existing = snap.to_dict()
                _USERS_CACHE[uid] = existing
                last_saved = existing.get("last_active", 0.0)
                if (current_time - last_saved) < _WRITE_DEBOUNCE_SECONDS:
                    return  # Already updated recently — no write needed
                # Update only last_active for returning user
                ref.update({"last_active": current_time})
                _USERS_CACHE[uid] = {**existing, "last_active": current_time}
                _evict_if_needed()
                log.debug(f"Updated last_active for existing user {uid} ({username})")
                return
            else:
                # Brand-new user
                user_data = {
                    "username":    username,
                    "last_active": current_time,
                    "mode":        "exp",
                }
                ref.set(user_data)
                _USERS_CACHE[uid] = user_data
                _evict_if_needed()
                log.info(f"Registered new user {uid} ({username})")
                return

        # Returning user past debounce window — partial update
        ref.update({"last_active": current_time})
        _USERS_CACHE[uid]["last_active"] = current_time
        _evict_if_needed()
        log.debug(f"Refreshed last_active for user {uid}")

    except Exception as e:
        log.error(f"[DB] track_user failed for uid={uid}: {e}")
        # Non-fatal — tracking is best-effort, do not crash the bot


def _normalize_mode(mode: str | None) -> MODE:
    """Map legacy mode values to their modern equivalents."""
    if not mode:
        return "exp"
    return _MODE_MIGRATION.get(mode, mode)  # type: ignore[return-value]


def get_user_mode(chat_id: int) -> MODE:
    """
    Return the user's current download mode.
    Reads from in-memory cache first; falls back to Firestore on cold-start.
    Default: "exp"

    Legacy modes (e.g. "get") are transparently migrated to their modern
    equivalents ("exp") so removed pipelines never break existing users.

    Returns "exp" on any DB error so the bot stays functional.
    """
    uid = str(chat_id)

    if uid in _USERS_CACHE:
        return _normalize_mode(_USERS_CACHE[uid].get("mode", "exp"))

    try:
        # Cold-start: fetch from Firestore once, then cache
        snap = get_db().collection(_USERS_COLLECTION).document(uid).get()
        if snap.exists:
            data = snap.to_dict()
            _USERS_CACHE[uid] = data
            _evict_if_needed()
            return _normalize_mode(data.get("mode", "exp"))
    except Exception as e:
        log.error(f"[DB] get_user_mode failed for uid={uid}: {e}")

    return "exp"  # Unknown user or DB error → default mode


def set_user_mode(chat_id: int, mode: MODE) -> bool:
    """
    Persist the user's chosen download mode.
    Single-field update — no read-modify-write needed.

    Returns True on success, False on DB error.
    Raises no exceptions.
    """
    uid = str(chat_id)
    try:
        get_db().collection(_USERS_COLLECTION).document(uid).set(
            {"mode": mode},
            merge=True,  # Creates doc if absent; only touches "mode" field
        )
        # Keep local cache in sync
        if uid in _USERS_CACHE:
            _USERS_CACHE[uid]["mode"] = mode
        else:
            _USERS_CACHE[uid] = {"mode": mode}
            _evict_if_needed()
        log.info(f"Set mode={mode} for user {uid}")
        return True
    except Exception as e:
        log.error(f"[DB] set_user_mode failed for uid={uid}: {e}")
        return False


def get_all_users() -> dict[str, dict]:
    """
    Return all users as { str(chat_id): {username, last_active, mode} }.
    Used by /recent and /broadcast — these are infrequent admin commands,
    so a full collection scan is acceptable.

    Returns an empty dict on DB error.
    """
    try:
        docs = get_db().collection(_USERS_COLLECTION).stream()
        result: dict[str, dict] = {}
        for doc in docs:
            result[doc.id] = doc.to_dict()
        return result
    except Exception as e:
        log.error(f"[DB] get_all_users failed: {e}")
        return {}


# ── Download history ─────────────────────────────────────────────────────────——
_HISTORY_LIMIT = 20  # entries kept per user


def record_history(chat_id: int, title: str, key: str, size: int = 0) -> None:
    """
    Append a completed download to the user's history (kept to last 20)
    and bump the user's lifetime counters (dl_count / dl_bytes).

    Stored on the user document as:
        history: [ {"t": <title>, "k": <surl/link-id>, "at": <unix>}, ... ]
        dl_count: int   (lifetime completed downloads)
        dl_bytes: int   (lifetime bytes downloaded)

    Best-effort: failures are logged, never raised.
    """
    uid = str(chat_id)
    entry = {"t": title[:120], "k": key[:80], "at": time.time()}
    try:
        # Prefer cached copy when fresh; otherwise read once from Firestore
        existing = None
        if uid in _USERS_CACHE and isinstance(_USERS_CACHE[uid].get("history"), list):
            existing = _USERS_CACHE[uid]["history"]
        if existing is None:
            snap = get_db().collection(_USERS_COLLECTION).document(uid).get(["history"])
            existing = (snap.to_dict() or {}).get("history") or [] if snap.exists else []

        updated = (list(existing) + [entry])[-_HISTORY_LIMIT:]

        payload = {"history": updated}
        try:
            from google.cloud.firestore_v1 import Increment  # noqa: PLC0415
            payload["dl_count"] = Increment(1)
            if size and size > 0:
                payload["dl_bytes"] = Increment(int(size))
        except ImportError:
            pass

        get_db().collection(_USERS_COLLECTION).document(uid).set(
            payload, merge=True
        )
        # Keep cache coherent
        _USERS_CACHE.setdefault(uid, {})["history"] = updated
        log.debug(f"History recorded for {uid}: {entry['t']}")
    except Exception as e:
        log.warning(f"[DB] record_history failed for {uid}: {e}")


def get_user_stats(chat_id: int) -> dict:
    """
    Return the user's lifetime counters: {"dl_count": int, "dl_bytes": int}.
    Zeros on error/absence. Never raises.
    """
    uid = str(chat_id)
    result = {"dl_count": 0, "dl_bytes": 0}
    try:
        snap = get_db().collection(_USERS_COLLECTION).document(uid).get(
            ["dl_count", "dl_bytes"]
        )
        if snap.exists:
            data = snap.to_dict() or {}
            result["dl_count"] = int(data.get("dl_count", 0))
            result["dl_bytes"] = int(data.get("dl_bytes", 0))
    except Exception as e:
        log.warning(f"[DB] get_user_stats failed for {uid}: {e}")
    return result


def get_history(chat_id: int) -> list[dict]:
    """
    Return the user's download history, newest last. [] on error/absence.
    """
    uid = str(chat_id)
    try:
        if uid in _USERS_CACHE and isinstance(_USERS_CACHE[uid].get("history"), list):
            return _USERS_CACHE[uid]["history"]
        snap = get_db().collection(_USERS_COLLECTION).document(uid).get(["history"])
        if snap.exists:
            return (snap.to_dict() or {}).get("history") or []
    except Exception as e:
        log.warning(f"[DB] get_history failed for {uid}: {e}")
    return []
