"""
firebase_db/cache.py
~~~~~~~~~~~~~~~~~~~~
Firestore-backed video cache, replacing the old GitHub Gist approach.

Firestore layout
----------------
Collection : cache
Documents  : "get" | "exp" | "exphd" | "dw"
Fields     : { <surl>: <message_id (int)>, ... }

Why one document per bucket instead of one massive document?
  - Each bucket is updated independently — no cross-bucket contention.
  - A single-field update (dot-notation path) writes exactly one surl→msg_id
    without touching the rest of the document.
  - Firestore document size limit is 1 MiB; at ~50-100 bytes per entry that
    gives headroom for ~10k-20k cached surls per bucket — plenty.

Search priority (same as old Gist implementation):
  get   → exphd → exp → get
  exp   → exphd → exp
  exphd → exphd only
  dw    → dw only   (Diskwala is a separate source, no cross-lookup)
"""

import base64
import logging
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from .db import get_db

log = logging.getLogger(__name__)

# ── Types & constants ──────────────────────────────────────────────────────────────────────────

MODE = Literal["get", "exp", "exphd", "dw", "dl", "social", "flare", "flezen"]
_CACHE_COLLECTION = "cache"
_BUCKETS = ("get", "exp", "exphd", "dw", "dl", "social", "flare", "flezen")

# ── In-memory snapshot for /random ────────────────────────────────────────────
# Avoids a Firestore read on every /random call.
# { "data": {surl: msg_id, ...}, "timestamp": float }
_RANDOM_SNAPSHOT: dict = {"data": {}, "timestamp": 0.0}
_RANDOM_TTL_SECONDS = 15 * 60  # 15 minutes
_RANDOM_SNAPSHOT_LOCK = threading.Lock()

# Module-level executor for parallel Firestore reads — avoids creating/destroying
# a ThreadPoolExecutor on every cache search.
_FIRESTORE_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="firestore")


# ── Helpers ────────────────────────────────────────────────────────────────────────────────────

def _bucket_ref(bucket: str):
    return get_db().collection(_CACHE_COLLECTION).document(bucket)


# ── Public API ─────────────────────────────────────────────────────────────────────────────────

def add_to_cache(surl: str, message_id: int, user_mode: MODE) -> bool:
    """
    Store surl → message_id in the bucket matching user_mode.

    Uses a single-field set with merge=True so we touch ONLY the new key —
    no read-modify-write cycle required.

    Returns True on success, False on DB error. Never raises.
    """
    safe_key = _encode_key(surl)
    try:
        _bucket_ref(user_mode).set({safe_key: message_id}, merge=True)
        log.debug(f"Cache add: bucket={user_mode}, surl={surl}, msg_id={message_id}")

        # Invalidate /random snapshot so it refreshes on next call.
        # Under the lock: an unlocked write could be erased by a concurrent
        # refresh finishing right after us (stale entry for a full TTL).
        with _RANDOM_SNAPSHOT_LOCK:
            _RANDOM_SNAPSHOT["timestamp"] = 0.0
        return True
    except Exception as e:
        log.error(f"[DB] add_to_cache failed for surl={surl}, mode={user_mode}: {e}")
        return False


def search_in_cache(surl: str, user_mode: MODE) -> int:
    """
    Search for surl across buckets in priority order.

    Returns message_id (int) on hit, -1 on miss or DB error.

    Priority:
      exp   → exphd, exp   (HD cache serves SD requests)
      exphd → exphd only
      dw    → dw only
      dl    → dl only
      social→ social only
      flare → flare only
      flezen→ flezen only

    Parallelizes Firestore reads for lower latency on misses.
    """
    safe_key = _encode_key(surl)

    if user_mode == "exp":
        search_order = ["exphd", "exp"]
    elif user_mode == "dw":
        search_order = ["dw"]
    elif user_mode == "dl":
        search_order = ["dl"]
    elif user_mode == "social":
        search_order = ["social"]
    elif user_mode == "flare":
        search_order = ["flare"]
    elif user_mode == "flezen":
        search_order = ["flezen"]
    elif user_mode == "exphd":
        search_order = ["exphd"]
    else:
        search_order = [user_mode]

    def _read_bucket(bucket: str) -> tuple[str, int]:
        """Read a single bucket and return (bucket_name, msg_id)."""
        try:
            snap = _bucket_ref(bucket).get(field_paths=[safe_key])
            if snap.exists:
                data = snap.to_dict() or {}
                msg_id = data.get(safe_key, -1)
                return (bucket, int(msg_id))
        except Exception as e:
            log.error(f"[DB] search_in_cache failed for bucket={bucket}, surl={surl}: {e}")
        return (bucket, -1)

    # For single-bucket modes, skip threading overhead
    if len(search_order) == 1:
        _, msg_id = _read_bucket(search_order[0])
        if msg_id != -1:
            log.info(f"Cache hit: bucket={search_order[0]}, surl={surl}, msg_id={msg_id} (user_mode={user_mode})")
            return msg_id
        log.debug(f"Cache miss: surl={surl} (user_mode={user_mode})")
        return -1

    # Parallel reads for multi-bucket search — reuse module-level executor
    futures = [_FIRESTORE_POOL.submit(_read_bucket, b) for b in search_order]
    results = [f.result() for f in futures]

    for bucket, msg_id in results:
        if msg_id != -1:
            log.info(f"Cache hit: bucket={bucket}, surl={surl}, msg_id={msg_id} (user_mode={user_mode})")
            return msg_id

    log.debug(f"Cache miss: surl={surl} (user_mode={user_mode})")
    return -1


def get_cache_for_random() -> dict:
    """
    Return a merged flat dict of ALL 4 buckets for /random.

    Refreshes from Firestore at most every 15 minutes (TTL-based snapshot).
    Merge order: get → exp → exphd → dw  (exphd wins on key conflicts, highest quality).

    Returns an empty dict on DB error.
    """
    with _RANDOM_SNAPSHOT_LOCK:
        age = time.time() - _RANDOM_SNAPSHOT["timestamp"]
        if age < _RANDOM_TTL_SECONDS and _RANDOM_SNAPSHOT["data"]:
            log.debug(f"Serving /random from in-memory snapshot (age={age:.0f}s)")
            return _RANDOM_SNAPSHOT["data"]

        log.info("Refreshing /random snapshot from Firestore")

        def _read_bucket(bucket: str) -> tuple[str, dict]:
            try:
                snap = _bucket_ref(bucket).get()
                if snap.exists:
                    data = snap.to_dict() or {}
                    decoded = {_decode_key(k): v for k, v in data.items()}
                    return (bucket, decoded)
            except Exception as e:
                log.error(f"[DB] get_cache_for_random failed for bucket={bucket}: {e}")
            return (bucket, {})

        futures = [_FIRESTORE_POOL.submit(_read_bucket, b) for b in _BUCKETS]
        results = [f.result() for f in futures]

        merged: dict = {}
        for _, bucket_data in results:
            merged.update(bucket_data)

        _RANDOM_SNAPSHOT["data"]      = merged
        _RANDOM_SNAPSHOT["timestamp"] = time.time()
        return merged


# ── Key encoding ───────────────────────────────────────────────────────────────────────────────
# Firestore field names must match [a-zA-Z_][a-zA-Z_0-9]*-.
# TeraBox surls contain hyphens and may start with digits, both of which are
# rejected by Firestore's unquoted property-path parser.
#
# Strategy: URL-safe Base64-encode the UTF-8 bytes of the surl, then:
#   • replace '-' → '_'  (base64url minus sign is invalid in field names)
#   • prefix with 'k_'   (ensures the name starts with a letter, not a digit)
#   • strip trailing '=' padding (not needed; length is implicit)
# Example: "W5BZoNNJzQdm0gOa-uiU2g" → "k_VzVCWm9OTkp6UWRtMGdPYS11aVUyZw"

def _encode_key(surl: str) -> str:
    """Encode a surl so it is safe as a Firestore field name."""
    b64 = base64.urlsafe_b64encode(surl.encode()).decode().rstrip("=")
    return "k_" + b64.replace("-", "_")


def _decode_key(field: str) -> str:
    """Reverse of _encode_key."""
    # Strip the 'k_' prefix, restore '-', re-add padding
    b64 = field[2:].replace("_", "-")
    padding = (-len(b64)) % 4
    return base64.urlsafe_b64decode(b64 + "=" * padding).decode()
