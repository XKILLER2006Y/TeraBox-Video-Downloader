#!/usr/bin/env python3
"""End-to-end test of the TeraBox bot's experimental pipeline."""
import os, sys, time, json, logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

from dotenv import load_dotenv
load_dotenv()

passed = 0
failed = 0

def check(name, condition, detail=""):
    global passed, failed
    if condition:
        print(f"  PASS  {name}")
        passed += 1
    else:
        print(f"  FAIL  {name} -- {detail}")
        failed += 1

print("=" * 60)
print("TEST A: Module imports")
print("=" * 60)
try:
    from teraboxDL.terabox_dl import (
        _extract_surl, _load_session, _get_js_token,
        _get_share_info, _get_video_metadata, get_video_info,
        _logid, _headers
    )
    check("All imports", True)
except Exception as e:
    check("All imports", False, str(e))
    sys.exit(1)

print()
print("=" * 60)
print("TEST B: URL parsing (_extract_surl)")
print("=" * 60)
cases = [
    ("https://terabox.com/s/1ABCdef123", "ABCdef123"),
    ("https://1024terabox.com/s/1XYZ789", "XYZ789"),
    ("https://terabox.com/s/1abc", "abc"),
    ("https://terabox.com/wap/share/filelist?surl=1HelloWorld", "HelloWorld"),
]
for url, expected in cases:
    try:
        result = _extract_surl(url)
        check(f"surl({url[:40]}...)", result == expected, f"got={result} want={expected}")
    except Exception as e:
        check(f"surl({url[:40]}...)", False, str(e))

# Bad URL
try:
    _extract_surl("https://google.com")
    check("surl(bad URL raises)", False, "should have raised")
except:
    check("surl(bad URL raises)", True)

print()
print("=" * 60)
print("TEST C: Session + jsToken extraction")
print("=" * 60)
surl = "H7Cy40dAq4eLQ_hKVxaWAA"
session = _load_session("")
check("Session created", session is not None)

try:
    js_token = _get_js_token(session, surl)
    check("jsToken extracted", len(js_token) > 10, f"token={js_token[:20]}...")
except Exception as e:
    check("jsToken extracted", False, str(e))
    js_token = None

print()
print("=" * 60)
print("TEST D: Share info API")
print("=" * 60)
if js_token:
    try:
        info = _get_share_info(session, js_token, surl)
        errno = info.get("errno", -999)
        files = info.get("list", [])
        if errno == 0:
            check("shorturlinfo errno=0", True)
            check(f"files found ({len(files)})", len(files) > 0)
            if files:
                print(f"    File: {files[0].get('server_filename', '?')}")
                print(f"    Size: {files[0].get('size', 0)} bytes")
        else:
            check("shorturlinfo errno=0", False, f"errno={errno} ({info.get('errmsg', '')})")
    except Exception as e:
        check("shorturlinfo API call", False, str(e))
else:
    print("  SKIP (no jsToken)")

print()
print("=" * 60)
print("TEST E: Streaming URL construction")
print("=" * 60)
from teraboxDL.terabox_dl import _build_streaming_url
try:
    stream_url = _build_streaming_url(
        shareid=12345, uk=67890, sign="abc",
        timestamp=int(time.time()), fs_id=111, quality="M3U8_AUTO_1080"
    )
    check("streaming URL built", "share/streaming" in stream_url, stream_url[:80])
except Exception as e:
    check("streaming URL built", False, str(e))

print()
print("=" * 60)
print("TEST F: Stream downloader imports")
print("=" * 60)
try:
    from teraboxDL.stream_downloader import (
        is_streaming_manifest, download_from_stream_url,
        _download_hls_segments_local, _download_hls_from_manifest,
        _parse_m3u8_segments
    )
    check("stream_downloader imports", True)
except Exception as e:
    check("stream_downloader imports", False, str(e))

print()
print("=" * 60)
print("TEST G: M3U8 manifest detection")
print("=" * 60)
check("m3u8 URL detected", is_streaming_manifest("https://example.com/video.m3u8"))
check("mp4 URL not detected", not is_streaming_manifest("https://example.com/video.mp4"))
check("hls path detected", is_streaming_manifest("https://cdn.com/hls/playlist"))
check("local m3u8 detected", is_streaming_manifest("/tmp/terabox_test.m3u8"))

print()
print("=" * 60)
print("TEST H: M3U8 segment parsing")
print("=" * 60)
fake_m3u8 = """#EXTM3U
#EXT-X-VERSION:3
#EXTINF:10.0,
https://cdn.example.com/chunk_0.ts?token=abc
#EXTINF:10.0,
https://cdn.example.com/chunk_1.ts?token=def
"""
segs = _parse_m3u8_segments(fake_m3u8, "https://example.com/base")
check("parsed 2 segments", len(segs) == 2, f"got {len(segs)}")
check("segment 1 URL", "chunk_0.ts" in segs[0])
check("segment 2 URL", "chunk_1.ts" in segs[1])

print()
print("=" * 60)
print("TEST I: get_video_info error handling")
print("=" * 60)
try:
    get_video_info("https://terabox.com/s/INVALID", False)
    check("invalid URL raises", False, "should have raised")
except Exception as e:
    check("invalid URL raises exception", True, str(e)[:80])

print()
print("=" * 60)
print("TEST J: Local M3U8 download (dry run)")
print("=" * 60)
import tempfile
m3u8_content = """#EXTM3U
#EXT-X-VERSION:3
#EXTINF:5.0,
https://httpbin.org/bytes/1024
#EXTINF:5.0,
https://httpbin.org/bytes/2048
#EXT-X-ENDLIST
"""
m3u8_path = os.path.join(tempfile.gettempdir(), "test_dry.m3u8")
with open(m3u8_path, "w") as f:
    f.write(m3u8_content)
check("local M3U8 file written", os.path.exists(m3u8_path))
check("M3U8 content correct", os.path.getsize(m3u8_path) > 50)
os.remove(m3u8_path)

print()
print("=" * 60)
print(f"RESULTS: {passed} passed, {failed} failed")
print("=" * 60)
if failed:
    sys.exit(1)
