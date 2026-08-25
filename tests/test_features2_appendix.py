"""
Offline unit tests — part 3 (stats, history, multi-file picker, thumbnails).

Run: python tests/test_features2_appendix.py
Exits 0 when all checks pass.
"""
import asyncio
import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("APP_ID", "12345")
os.environ.setdefault("API_HASH", "test-hash")

import teraboxDL.terabox_dl as TD  # noqa: E402
from teraboxDL.errors import TeraBoxError, TeraBoxDirectError  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}" + (f" — {detail}" if detail else ""))


def group(title: str) -> None:
    print(f"\n── {title} " + "─" * max(0, 50 - len(title)))


group("Persistent stats")
import firebase_db.stats as ST

writes: list[tuple[str, dict]] = []

class _FakeDoc:
    def __init__(self, doc_id): self.id = doc_id
    def set(self, payload, merge=False):
        writes.append((self.id, {k: v.value for k, v in payload.items()}))

class _FakeCol:
    def document(self, doc_id): return _FakeDoc(doc_id)

class _FakeDB:
    def collection(self, _): return _FakeCol()

ST.get_db = lambda: _FakeDB()

ST.record_success(123 * 1024 * 1024)
ids = [w[0] for w in writes]
check("success writes today + totals", ids.count("totals") == 1 and len(ids) == 2)
payload = dict(writes[-1][1])
check("bytes recorded as increment value", payload.get("bytes") == 123 * 1024 * 1024)

writes.clear()
ST.record_failure()
check("failure counted on both docs", len(writes) == 2)

check("get_stats callable", callable(ST.get_stats))


group("Download history")
import firebase_db.users as U2

stored: dict[str, dict] = {}

class _Snap2:
    def __init__(self, data, exists=True): self._d, self.exists = data, exists
    def to_dict(self): return self._d

class _Doc2:
    def __init__(self, uid): self.uid = uid
    def get(self, fields=None):
        return _Snap2(stored.get(self.uid, {}), self.uid in stored)
    def set(self, payload, merge=False):
        # emulate Firestore: Increment(x) applied on merge
        doc = stored.setdefault(self.uid, {})
        def _apply(old, v):
            if hasattr(v, "value") and not isinstance(v, (str, bytes)):
                return (old or 0) + v.value
            if isinstance(v, dict):
                base = old if isinstance(old, dict) else {}
                return {k2: _apply(base.get(k2, 0), v2) for k2, v2 in v.items()}
            return v
        for k, v in payload.items():
            doc[k] = _apply(doc.get(k), v)

class _Inc:
    def __init__(self, n): self.value = n

class _Col2:
    def document(self, uid): return _Doc2(uid)

class _DB2:
    def collection(self, _): return _Col2()

U2.get_db = lambda: _DB2()
U2._USERS_CACHE.clear()

for i in range(25):
    U2.record_history(4001, f"video{i}.mp4", f"surl{i}")
h = U2.get_history(4001)
check("history capped at 20", len(h) == 20, f"len={len(h)}")
check("newest kept after cap", h[-1]["t"] == "video24.mp4" and h[0]["t"] == "video5.mp4")

U2.record_history(4002, "single.mp4", "s")
check("second user isolated", U2.get_history(4002)[0]["t"] == "single.mp4")


group("Multi-file picker")
from teraboxDL.errors import TeraBoxMultipleChoice
from telegram_logic.terabox_exp import _b64e, _b64d, _build_file_picker

check("MultipleChoice is control-flow not DirectError",
      issubclass(TeraBoxMultipleChoice, TeraBoxError) and not issubclass(TeraBoxMultipleChoice, TeraBoxDirectError))

round_trip = _b64d(_b64e("AbCdEfGh123456789012"))
check("b64 roundtrip", round_trip == "AbCdEfGh123456789012")

files = [
    {"fs_id": 111, "name": "movie1.mp4", "size": 700 * 1024 * 1024, "is_video": True},
    {"fs_id": 222, "name": "movie2.mkv", "size": 1400 * 1024 * 1024, "is_video": True},
    {"fs_id": 333, "name": "notes.pdf", "size": 4096, "is_video": False},
]
buttons = _build_file_picker(files, "AbCdEfGh123456789012")
check("picker shows only videos + download-all", buttons is not None and len(buttons) == 3)
all_data_ok = all(len(btn.data) <= 64 for row in buttons for btn in row)
check("button payloads within Telegram 64-byte limit", all_data_ok)
check("non-video excluded from buttons", all(b"pdf" not in row[0].data for row in buttons[:2]))

big = [{"fs_id": i, "name": f"v{i}.mp4", "size": 1, "is_video": True} for i in range(30)]
buttons_big = _build_file_picker(big, "s")
check("listing capped at 10 rows (+all)", len(buttons_big) <= 11)

import inspect
sig = inspect.signature(TD.get_video_info)
check("get_video_info accepts fs_id", "fs_id" in sig.parameters)
check("list_share_files exported", hasattr(TD, "list_share_files"))


group("Thumbnail extraction")
_extract = TD._extract_thumb_url

check("dict-with-url-list form",
      _extract({}, {"thumbs": {"url": ["https://thumb.example/a.jpg"]}}) == "https://thumb.example/a.jpg")
check("bare-list form",
      _extract({"thumbs": ["https://t.example/b.jpg"]}, {}) == "https://t.example/b.jpg")
check("file-level preferred over share-level",
      _extract({"thumbs": ["https://share.jpg"]}, {"thumbs": ["https://file.jpg"]}) == "https://file.jpg")
check("missing thumbs → empty string", _extract({}, {}) == "")
check("junk ignored safely", _extract({}, {"thumbs": {"url": [None, 42]}}) == "")




# ── 18. Structured logging ────────────────────────────────────────────────────
group("Structured logging")
import json as _json
import tempfile as _tempfile
from telegram_logic.structured_log import (
    setup_logging, ctx_logger, bind_context, new_request_id, get_context,
)

check("request id is 8 hex chars",
      len(new_request_id()) == 8 and all(c in "0123456789abcdef" for c in new_request_id()))

bind_context(request_id="testrid1", user_id=777)
check("get_context returns bound values", get_context() == {"request_id": "testrid1", "user_id": 777})

with _tempfile.NamedTemporaryFile(suffix=".log", delete=False) as tf:
    tf_path = tf.name
setup_logging(log_file=tf_path, file_level=logging.DEBUG)

slog = ctx_logger("sl-test")
slog.info("plain message")
slog.info("with extras", extra={"bytes": 999, "ok": True})
rid = new_request_id()
bind_context(request_id=rid, user_id=5555, download_id="dl1")
slog.warning("in context")

entries = [_json.loads(l) for l in open(tf_path).read().strip().split("\n")]
check("every line is valid JSON", len(entries) >= 3)
check("extra fields serialized", entries[1].get("bytes") == 999 and entries[1].get("ok") is True)
check("context fields injected", entries[2].get("request_id") == rid
      and entries[2].get("user_id") == 5555 and entries[2].get("download_id") == "dl1")
check("level + logger + src present", all(k in entries[0] for k in ("ts", "level", "logger", "src")))


async def _child():
    slog.info("from child")


asyncio.get_event_loop_policy()
asyncio.run(_child())
entries = [_json.loads(l) for l in open(tf_path).read().strip().split("\n")]
check("context propagates to child tasks", any(
    e.get("msg") == "from child" and e.get("user_id") == 5555 for e in entries))

# restore default logging so later output stays clean
setup_logging(log_file="/tmp/opencode/sl_default.log")


# ── 19. Inline mode & user stats ──────────────────────────────────────────────
group("Inline mode & user stats")
from telegram_logic.commands.inline import first_url

check("inline url extract: terabox link",
      first_url("check this https://1024tera.com/s/1AbCdEf example") == "https://1024tera.com/s/1AbCdEf")
check("inline url extract: none", first_url("just chatting") is None)
check("inline url extract: empty", first_url("") is None)

# per-user counters in users layer (reuse fake db from history group)
U2.get_db = lambda: _DB2()
U2._USERS_CACHE.clear()
for i in range(3):
    U2.record_history(5001, f"v{i}.mp4", f"k{i}", size=1000 * (i + 1))
st = U2.get_user_stats(5001)
check("dl_count increments per download", st["dl_count"] == 3)
check("dl_bytes accumulates sizes", st["dl_bytes"] == 6000)
missing = U2.get_user_stats(999999)
check("unknown user reads zeros", missing == {"dl_count": 0, "dl_bytes": 0})

from telegram_logic.commands.stats import build_stats_text
txt = build_stats_text({"dl_count": 7, "dl_bytes": 1536}, 5, "exp")
check("user stats text renders", "Downloads:** 7" in txt and "1.5 KB" in txt and "TeraBox" in txt)
admin_txt = build_stats_text(
    {"dl_count": 1, "dl_bytes": 10}, 1, "dw",
    {"today": {"ok": 3, "fail": 1, "bytes": 0}, "totals": {"ok": 30, "fail": 2, "bytes": 2048}},
)
check("admin stats include global block", "Global**" in admin_txt and "Today: 3 ✓ · 1 ✗" in admin_txt)


# ── 20. MP3, daily quota, deep-link start ─────────────────────────────────────
group("MP3 / quota / deep-link")
import subprocess as _sp
from telegram_logic.commands.mp3 import _convert_to_mp3, _strip_ext
from telegram_logic.commands.start import WELCOME_MESSAGE

check("welcome no longer mentions removed /get", "/get" not in WELCOME_MESSAGE)
check("welcome mentions /mp3 and /stats", "/mp3" in WELCOME_MESSAGE and "/stats" in WELCOME_MESSAGE)

check("strip_ext removes extension", _strip_ext("movie.hd.mp4") == "movie.hd")
check("strip_ext safe on bare name", _strip_ext("noext") == "noext")

# real ffmpeg round-trip: generate 1s tone -> mp4 container via lavfi, then convert
with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
    tone_path = tf.name
gen = _sp.run(
    ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
     "-i", "sine=frequency=440:duration=1", "-acodec", "aac", tone_path],
    capture_output=True, text=True,
)
if gen.returncode == 0:
    mp3_out = _convert_to_mp3(tone_path)
    check("ffmpeg converts aac -> mp3", os.path.exists(mp3_out) and os.path.getsize(mp3_out) > 1000)
    bad = _sp.run(["ffprobe", "-v", "quiet", "-show_entries",
                   "stream=codec_name", "-of", "csv=p=0", mp3_out],
                  capture_output=True, text=True)
    check("output really is mp3 codec", bad.stdout.strip() == "mp3")
    os.remove(tone_path)
    os.remove(mp3_out)
else:
    check("ffmpeg available in test env", False, detail=gen.stderr[:120])

# daily quota helpers against the fake db
U2.get_db = lambda: _DB2()
U2._USERS_CACHE.clear()
check("today count starts at zero", U2.get_today_count(6001) == 0)
for _ in range(4):
    U2.bump_today(6001)
check("bump_today accumulates", U2.get_today_count(6001) == 4)
check("users isolated in quota", U2.get_today_count(6002) == 0)
# stale date resets
stored.setdefault("6003", {})["daily"] = {"d": "1999-01-01", "n": 50}
check("stale date ignored", U2.get_today_count(6003) == 0)


print(f"\n{'=' * 54}")
print(f"Results: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
