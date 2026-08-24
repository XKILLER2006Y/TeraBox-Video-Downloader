"""
Offline unit tests — part 3 (stats, history, multi-file picker, thumbnails).

Run: python tests/test_features2_appendix.py
Exits 0 when all checks pass.
"""
import os
import sys

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
        stored.setdefault(self.uid, {}).update(payload)

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


print(f"\n{'=' * 54}")
print(f"Results: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
