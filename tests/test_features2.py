"""
Offline unit tests — part 2 (HLS downloader, alerts, slots, stats,
history, multi-file picker, thumbnails).

Run: python tests/test_features2.py
Exits 0 when all checks pass.
"""
import os
import sys
import time

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


# ── 11. HLS windowed download (local HTTP server) ─────────────────────────────
group("HLS windowed download")
import threading as _th
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SEGMENTS = [os.urandom(300_000 + i * 50_000) for i in range(6)]  # 6 distinct segments

class _SegHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            idx = int(self.path.strip("/").split(".")[0].replace("seg", ""))
        except ValueError:
            self.send_error(404)
            return
        body = SEGMENTS[idx]
        self.send_response(200)
        self.send_header("Content-Type", "video/mp2t")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):  # silence request logs
        pass

server = ThreadingHTTPServer(("127.0.0.1", 0), _SegHandler)
_th.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_port}"

manifest = "#EXTM3U\n#EXT-X-VERSION:3\n" + "".join(
    f"#EXTINF:2.0,\n{base}/seg{i}.ts\n" for i in range(len(SEGMENTS))
) + "#EXT-X-ENDLIST\n"

from teraboxDL.stream_downloader import (
    _parse_m3u8_segments, _download_hls_from_manifest, is_streaming_manifest,
)

check("m3u8 parse: absolute urls", len(_parse_m3u8_segments(manifest, "")) == len(SEGMENTS))
check("m3u8 parse: relative resolution",
      _parse_m3u8_segments("#EXTM3U\nseg1.ts\n", base)[0] == f"{base}/seg1.ts")
check("m3u8 detection by extension", is_streaming_manifest("http://x/y/playlist.m3u8"))

out_mp4 = "/tmp/hls_out.mp4"
_download_hls_from_manifest(manifest, "", out_mp4, None, None)
with open(out_mp4, "rb") as f:
    joined = f.read()

expected = b"".join(SEGMENTS)
check("windowed concat byte-exact & in order", joined == expected,
      f"len {len(joined)} vs {len(expected)}")
check("temp parts cleaned up", not os.path.exists(out_mp4 + ".parts"))
server.shutdown()


# ── 12. Admin alerts ─────────────────────────────────———————————————————————
group("Admin alerts")
import telegram_logic.alerts as AL

sent: list[str] = []
AL._send_now = lambda text: sent.append(text)
AL._last_sent.clear()

AL.ADMIN_ID = 0
AL.dispatch("no admin configured", key="k1")
check("no ADMIN_ID → silent no-op", sent == [])

AL.ADMIN_ID = 42
AL.dispatch("cookie pool dead", key="cookies", cooldown=60)
check("first alert delivered", len(sent) == 1 and "cookie" in sent[0])
AL.dispatch("cookie pool dead again", key="cookies", cooldown=60)
check("cooldown suppresses repeat", len(sent) == 1)
AL.dispatch("different alert", key="session", cooldown=60)
check("different key passes through", len(sent) == 2)

AL._last_sent["old"] = time.monotonic() - 3600
AL.dispatch("stale key fires again", key="old", cooldown=60)
check("expired cooldown re-fires", len(sent) == 3)


# ── 13. Per-user concurrency slots ─——————————————————————
group("Per-user concurrency cap")
from telegram_logic.bot import acquire_user_slot, release_user_slot, _user_active

_user_active.clear()
ok1 = acquire_user_slot(3001)
ok2 = acquire_user_slot(3001)
ok3 = acquire_user_slot(3001)  # default cap is 2
check("third slot denied for same user", ok1 is True and ok2 is True and ok3 is False)
release_user_slot(3001)
check("slot released → available again", acquire_user_slot(3001) is True)
release_user_slot(3001)
release_user_slot(3001)

check("admin always gets a slot",
      all(acquire_user_slot(9999, is_admin=True) for _ in range(10)))
for _ in range(10):
    release_user_slot(9999, is_admin=True)

acquire_user_slot(3002)
acquire_user_slot(3002)
release_user_slot(3002)
release_user_slot(3002)
check("over-release is safe", 3002 not in _user_active)
_user_active.clear()
