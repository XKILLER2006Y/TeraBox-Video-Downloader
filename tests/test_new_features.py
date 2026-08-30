"""
Offline unit tests for the reliability/feature additions.

No network, no Telegram, no Firebase writes — everything is mocked or
operates on in-memory state. Run:

    python tests/test_new_features.py

Exits 0 when all checks pass; prints PASS/FAIL per group.
"""
import os
import sys
import time
import threading
import asyncio

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Minimal env so telegram_logic.bot constructs its Telethon client on import
os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("APP_ID", "12345")
os.environ.setdefault("API_HASH", "test-hash")

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


# ── 1. Error hierarchy ─────────────────────────────────────────────────────────———————
group("Error hierarchy")
from teraboxDL.errors import (
    DownloadError, CancelledError,
    TeraBoxError, TeraBoxDirectError, TeraBoxRateLimited,
    DiskwalaError, DiskwalaDirectError,
)

check("TeraBoxDirectError < TeraBoxError", issubclass(TeraBoxDirectError, TeraBoxError))
check("TeraBoxRateLimited < TeraBoxDirectError", issubclass(TeraBoxRateLimited, TeraBoxDirectError))
check("DiskwalaDirectError < DiskwalaError", issubclass(DiskwalaDirectError, DiskwalaError))
check("both branches share DownloadError base",
      issubclass(TeraBoxError, DownloadError) and issubclass(DiskwalaError, DownloadError))
check("CancelledError is NOT a DownloadError", not issubclass(CancelledError, DownloadError))

# legacy import paths still resolve
from teraboxDL.public_api import TeraBoxError as PA_TeraBoxError
from diskwalaDL.diskwala_dl import DiskwalaDirectError as DL_Direct
from diskwalaDL.public_api import DiskwalaError as PA_DiskwalaError
check("teraboxDL.public_api re-export", PA_TeraBoxError is TeraBoxError)
check("diskwalaDL re-exports identity", DL_Direct is DiskwalaDirectError and PA_DiskwalaError is DiskwalaError)


# ── 2. Quality parsing ─────────────────────────────────────────────────———————————————
group("Quality parsing")
from telegram_logic.helpers import parse_quality, DEFAULT_QUALITY

t, q = parse_quality("/exp https://terabox.com/s/1abc123 720p")
check("trailing 720p parsed", q == "M3U8_AUTO_720" and "/s/1abc123" in t and "720p" not in t, f"({t!r}, {q})")

t, q = parse_quality("https://terabox.com/s/1abc123 1080")
check("bare 1080 accepted", q == "M3U8_AUTO_1080")

t, q = parse_quality("https://terabox.com/s/1abc123 480P")
check("uppercase 480P accepted", q == "M3U8_AUTO_480")

t, q = parse_quality("https://terabox.com/s/1abc123")
check("no quality → default", q == DEFAULT_QUALITY == "M3U8_AUTO_1080")

t, q = parse_quality("https://terabox.com/s/1x720p")
check("URL ending in 720p NOT misparsed", q == DEFAULT_QUALITY, f"q={q}")

t, q = parse_quality("https://terabox.com/s/1abc\n720p")
check("newline-separated quality", q == "M3U8_AUTO_720")

t, q = parse_quality("720p")
check("quality-only input stripped", q == "M3U8_AUTO_720" and t.strip() == "")

t, q = parse_quality("https://terabox.com/s/1abc 240p extra")
check("non-trailing token ignored", q == DEFAULT_QUALITY)


# ── 3. Size limit & batch cap ─────────────────────────────────————————————————
group("Size limit & batch cap")
import telegram_logic.helpers as H

H.MAX_FILE_SIZE_MB = 100
check("under limit allowed", H.check_size_limit(50 * 1024 * 1024) is None)
check("over limit rejected", H.check_size_limit(150 * 1024 * 1024) is not None)
check("unknown size (0) allowed", H.check_size_limit(0) is None)
H.MAX_FILE_SIZE_MB = 0
check("disabled when 0", H.check_size_limit(10**12) is None)

H.MAX_LINKS_PER_MESSAGE = 3
kept, dropped = H.cap_links(["a", "b"])
check("under cap untouched", (kept, dropped) == (["a", "b"], 0))
kept, dropped = H.cap_links(["a", "b", "c", "d", "e"])
check("over cap truncated", kept == ["a", "b", "c"] and dropped == 2)
H.MAX_LINKS_PER_MESSAGE = 5


# ── 4. Rate limiter (retry budget) ─────────────────────────────────────———
group("Retry budget")
from telegram_logic import rate_limit as RL

RL._failures.clear()
RL._blocked_until.clear()
RL.MAX_FAILURES = 3
RL.FAILURE_WINDOW = 60
RL.COOLDOWN_SECONDS = 120

check("fresh user allowed", RL.check_rate_limit(1001) is None)
for i in range(3):
    RL.register_failure(1001)
msg = RL.check_rate_limit(1001)
check("blocked after N failures", msg is not None and "wait" in msg.lower())
check("other users unaffected", RL.check_rate_limit(1002) is None)

RL.register_success(1001)
check("success clears block", RL.check_rate_limit(1001) is None)

# sliding-window expiry: old failures fall out of the window
RL.MAX_FAILURES = 2
dq = RL._failures.setdefault(1003, __import__("collections").deque())
dq.append(time.time() - 9999)  # ancient failure outside window
RL.FAILURE_WINDOW = 600
RL.register_failure(1003)
check("old failures expire from window", RL.check_rate_limit(1003) is None)

RL.MAX_FAILURES = 0
check("budget disabled at 0", RL.check_rate_limit(1004) is None)
RL.MAX_FAILURES = 5
st = RL.stats()
check("stats snapshot shape", {"tracked_users_with_failures", "currently_blocked"} <= set(st))


# ── 5. Cookie pool ─────────────────────────────────———————————————————————
group("Cookie pool rotation")
import teraboxDL.terabox_dl as TD

TD._cookie_cache.clear()
os.environ["COOKIES1"] = "a=1"
os.environ["COOKIES2"] = "b=2"
os.environ.pop("COOKIES3", None)

TD._validate_cookies = lambda s, c: "valid"  # mock: no network
pool = TD._CookiePool()

empty_pool = TD._CookiePool()
empty_pool._cookies.clear()
check("no cookies → ''", empty_pool.acquire() == "")

c1, c2, c3 = pool.acquire(), pool.acquire(), pool.acquire()
check("round-robin rotation", (c1, c2, c3) == ("a=1", "b=2", "a=1"), f"{(c1, c2, c3)}")

pool.invalidate("a=1")
got = {pool.acquire() for _ in range(4)}
check("invalidated cookie skipped", got == {"b=2"}, f"{got}")

TD._cookie_cache.clear()
TD._validate_cookies = lambda s, c: "invalid"
check("all-invalid → None", TD._CookiePool().acquire() is None)

TD._cookie_cache.clear()
TD._validate_cookies = lambda s, c: "unknown"
check("fail-open on unknown verdict", TD._CookiePool().acquire() in ("a=1", "b=2"))

# concurrency: validation is a slow network call OUTSIDE the lock —
# 5 threads × slow validate must complete in ~1 validation time, not 5×.
TD._cookie_cache.clear()
def slow_validate(s, c):
    time.sleep(0.3)
    return "valid"
TD._validate_cookies = slow_validate
slow_pool = TD._CookiePool()
results = []
def worker():
    results.append(slow_pool.acquire())
threads = [threading.Thread(target=worker) for _ in range(6)]
t0 = time.monotonic()
for th in threads: th.start()
for th in threads: th.join()
elapsed = time.monotonic() - t0
check("concurrent acquire not serialized behind validation",
      len(results) == 6 and elapsed < 1.2, f"elapsed={elapsed:.2f}s")
check("all workers got a usable cookie", all(r in ("a=1", "b=2") for r in results))

h = TD._CookiePool()
TD._cookie_cache["zzz"] = (True, time.time() + 300)
h._cookies = [(9, "zzz")]
states = h.health()
check("health reports ok state", states == [{"index": 9, "state": "ok"}])


# ── 6. Quality plumbing ─────────────────────────———————————————————————
group("Quality plumbing")
import inspect
sig = inspect.signature(TD.get_video_info)
check("get_video_info(url, is_hd, quality, fs_id)", list(sig.parameters) == ["terabox_url", "is_hd", "quality", "fs_id"])
sig = inspect.signature(TD._get_video_metadata)
check("_get_video_metadata takes quality", sig.parameters.get("quality") is not None)
sig = inspect.signature(TD._discover_all_hls_chunks)
check("discovery defaults AUTO_1080",
      sig.parameters["quality"].default == "M3U8_AUTO_1080")
check("_probe_quality exists", callable(TD._probe_quality))


# ── 7. Mode migration ─────────────────────────────────——————————————————————
group("Legacy mode migration")
from firebase_db import users as U

U._USERS_CACHE.clear()
U._USERS_CACHE["2001"] = {"mode": "get"}
check("'get' migrates to 'exp'", U.get_user_mode(2001) == "exp")
U._USERS_CACHE["2002"] = {"mode": "dw"}
check("'dw' preserved", U.get_user_mode(2002) == "dw")
U._USERS_CACHE["2003"] = {}
check("missing mode → 'exp'", U.get_user_mode(2003) == "exp")

from telegram_logic.commands.settings import AVAILABLE_MODES
check("'get' removed from settings modes", "get" not in AVAILABLE_MODES)


# ── 8. Status builder & queue accessor ─────────———————————————————————
group("Status & queue")
from telegram_logic.commands.status import build_status_text

public_txt = build_status_text(is_admin=False)
admin_txt = build_status_text(is_admin=True)
check("public has core stats", "Active downloads" in public_txt and "Uptime" in public_txt)
check("admin adds cookie health", "Cookie pool" in admin_txt and "Rate-limit" in admin_txt)
check("cookie health hidden from public", "Cookie pool" not in public_txt)

from telegram_logic.queue import MessageQueue
mq = MessageQueue(concurrency_limit=2)
check("pending=0 before queue starts", mq.pending == 0)
check("flood_remaining starts 0", mq.flood_remaining() == 0)
mq.update_flood_until(30)
check("flood cooldown tracked", 0 < mq.flood_remaining() <= 30)


# ── 9. Graceful shutdown drain ─────────———————————————————————
group("Graceful shutdown drain")
from telegram_logic import bot as botmod

async def _drain_scenario():
    # two fake tasks; one finishes fast, one hangs until cancelled
    ev_fast = threading.Event()
    ev_slow = threading.Event()
    botmod.active_tasks[("c1", "fast")] = ev_fast
    botmod.active_tasks[("c1", "slow")] = ev_slow

    async def finisher():
        await asyncio.sleep(0.3)
        ev_fast.set()

    task = asyncio.create_task(botmod.drain_active_tasks(timeout=2.0))
    await finisher()
    # still one left → drain should trip cancel events at half-timeout then exit
    remaining = await task
    check("drain tripped hanging event", ev_slow.is_set())
    check("drain reported leftover count", remaining >= 1 or not botmod.active_tasks)
    botmod.active_tasks.clear()

asyncio.run(_drain_scenario())

async def _drain_immediate():
    botmod.active_tasks.clear()
    n = await botmod.drain_active_tasks(timeout=1.0)
    check("drain returns instantly when idle", n == 0)

asyncio.run(_drain_immediate())


# ── 10. FastTelethon upload wiring (regression) ─——————————————————————
# Regression: _send_partial was called with chunk_data where the part INDEX
# belongs, so every >10MB fast upload crashed and silently fell back to the
# slow single-stream path.
group("Fast upload part-index wiring")
from telethon.tl import functions as tl_functions
import telegram_logic.fast_upload as FU

captured_parts: list[int] = []
real_save = tl_functions.upload.SaveBigFilePartRequest

class _FakeSender:
    async def send(self, req):
        captured_parts.append(req.file_part)

class _FakeClient:
    _sender = _FakeSender()

tl_functions.upload.SaveBigFilePartRequest = lambda **kw: type("R", (), kw)()

async def _fake_upload():
    payload = os.urandom(FU.CHUNK_SIZE * 3 + 1234)  # 4 parts, last partial
    tmp = "/tmp/fastup_test.bin"
    with open(tmp, "wb") as f:
        f.write(payload)
    captured_parts.clear()
    result = await FU.upload_file_fast(_FakeClient(), tmp)
    return result, len(payload)

loop = asyncio.new_event_loop()
res, total_size = loop.run_until_complete(_fake_upload())
expected_parts = -(-total_size // FU.CHUNK_SIZE)
# Parts arrive in COMPLETION order (parallel senders) — that's fine, each
# request carries its own file_part index. Assert each index sent exactly once.
check("all parts sent exactly once",
      sorted(captured_parts) == list(range(expected_parts)),
      f"got {sorted(captured_parts)}, expected {list(range(expected_parts))}")
check("InputFileBig parts count", res.parts == expected_parts)
tl_functions.upload.SaveBigFilePartRequest = real_save

# ── Summary ─────────────────────────────────———————————————————————
if __name__ == "__main__":
    print(f"\n{'=' * 54}")
    print(f"Results: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)
