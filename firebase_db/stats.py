"""
firebase_db/stats.py
~~~~~~~~~~~~~~~~~~~~
Persistent download statistics for /status.

Firestore layout
----------------
Collection : stats
Document   : "YYYY-MM-DD"   → { ok: int, fail: int, bytes: int }  (per-day)
Document   : "totals"       → { ok: int, fail: int, bytes: int }  (all-time)

Uses field transforms (Increment) so concurrent workers never clobber each
other and each write touches only the affected counters.

All functions are best-effort: failures are logged, never raised — a broken
stats backend must not break downloads.
"""
import logging
from datetime import datetime, timezone

try:
    from google.cloud.firestore_v1 import Increment
except ImportError:
    Increment = None

from .db import get_db

log = logging.getLogger(__name__)

_STATS_COLLECTION = "stats"


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _apply(doc_id: str, ok: int = 0, fail: int = 0, byte_delta: int = 0) -> None:
    payload: dict = {}
    if ok:
        payload["ok"] = Increment(ok)
    if fail:
        payload["fail"] = Increment(fail)
    if byte_delta:
        payload["bytes"] = Increment(byte_delta)
    if not payload:
        return
    get_db().collection(_STATS_COLLECTION).document(doc_id).set(payload, merge=True)


def record_success(size_bytes: int = 0) -> None:
    """Count one completed download (+ optional delivered bytes). Best-effort."""
    try:
        _apply(_today(), ok=1, byte_delta=max(0, size_bytes))
        _apply("totals", ok=1, byte_delta=max(0, size_bytes))
    except Exception as e:
        log.warning(f"[DB] record_success failed: {e}")


def record_failure() -> None:
    """Count one failed download. Best-effort."""
    try:
        _apply(_today(), fail=1)
        _apply("totals", fail=1)
    except Exception as e:
        log.warning(f"[DB] record_failure failed: {e}")


def get_stats() -> dict:
    """
    Return {"today": {...}, "totals": {...}} for /status.
    Missing docs read as zeros. Never raises.
    """
    result = {
        "today": {"ok": 0, "fail": 0, "bytes": 0},
        "totals": {"ok": 0, "fail": 0, "bytes": 0},
    }
    try:
        col = get_db().collection(_STATS_COLLECTION)
        today_doc = col.document(_today()).get()
        if today_doc.exists:
            data = today_doc.to_dict() or {}
            result["today"] = {
                "ok": int(data.get("ok", 0)),
                "fail": int(data.get("fail", 0)),
                "bytes": int(data.get("bytes", 0)),
            }
        totals_doc = col.document("totals").get()
        if totals_doc.exists:
            data = totals_doc.to_dict() or {}
            result["totals"] = {
                "ok": int(data.get("ok", 0)),
                "fail": int(data.get("fail", 0)),
                "bytes": int(data.get("bytes", 0)),
            }
    except Exception as e:
        log.warning(f"[DB] get_stats failed: {e}")
    return result
