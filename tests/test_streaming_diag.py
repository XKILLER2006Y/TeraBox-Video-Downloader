"""
TeraBox Streaming Pipeline — Deep Diagnostic
==============================================
Probes every step that leads to the "Could not find any video chunks" error.

Usage:
    python tests/test_streaming_diag.py
    python tests/test_streaming_diag.py "https://1024terabox.com/s/1mKoEsoPWtrnXZ_rNXbHZoA"

The script does NOT download any video.  It only makes lightweight HTTP calls
and prints detailed diagnostics for each stage.
"""

import os
import re
import sys
import json
import time
import random
import logging
import textwrap
import requests
from urllib.parse import unquote, urlparse, urlencode, parse_qs, urlunparse
from dotenv import load_dotenv

load_dotenv()

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("diag")

# ── Colour helpers ───────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
DIM    = "\033[2m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):      print(f"  {GREEN}✓ PASS{RESET}  {msg}")
def fail(msg):    print(f"  {RED}✗ FAIL{RESET}  {msg}")
def warn(msg):    print(f"  {YELLOW}⚠ WARN{RESET}  {msg}")
def info(msg):    print(f"  {CYAN}· INFO{RESET}  {msg}")
def header(msg):  print(f"\n{BOLD}{'━'*64}\n  {msg}\n{'━'*64}{RESET}")
def subhdr(msg):  print(f"\n  {BOLD}{msg}{RESET}")
def dump(label, data, max_len=300):
    """Pretty-print a value, truncated."""
    s = str(data)
    if len(s) > max_len:
        s = s[:max_len] + f"  …({len(s)} chars total)"
    print(f"  {DIM}{label}: {s}{RESET}")


# ── Constants (mirrored from terabox module) ─────────────────────────────────
BASE_DOMAIN_CURRENT = "dm.1024tera.com"         # what the code uses today
BASE_URL_CURRENT    = f"https://{BASE_DOMAIN_CURRENT}"

# Candidate domains the site may have migrated to
CANDIDATE_DOMAINS = [
    "dm.1024tera.com",
    "www.1024tera.com",
    "1024tera.com",
    "dm.1024terabox.com",
    "www.1024terabox.com",
    "1024terabox.com",
    "dm.terabox.com",
    "www.terabox.com",
    "terabox.com",
    "d.1024terabox.com",
    "d.terabox.com",
]

QUALITIES = [
    "M3U8_AUTO_1080",
    "M3U8_AUTO_720",
    "M3U8_AUTO_480",
    "M3U8_AUTO_360",
    "M3U8_720P",
    "M3U8_480P",
    "M3U8_360P",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
]

# ── Cookie loading ───────────────────────────────────────────────────────────
def load_all_cookies() -> list[str]:
    cookies = []
    for idx in range(1, 20):
        c = os.getenv(f"COOKIES{idx}")
        if c:
            cookies.append(c)
    return cookies

def build_session(cookie_str: str, domain: str = ".1024tera.com") -> requests.Session:
    s = requests.Session()
    count = 0
    for c in cookie_str.split(";"):
        if "=" in c:
            k, v = c.strip().split("=", 1)
            s.cookies.set(k.strip(), v.strip(), domain=domain, path="/")
            count += 1
    return s, count

def cookie_header(session: requests.Session) -> str:
    return "; ".join(
        f"{c.name}={c.value}" for c in session.cookies
    )

def make_headers(session: requests.Session, surl: str, base_url: str) -> dict:
    hdrs = {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": f"{base_url}/wap/share/filelist?surl={surl}",
    }
    ck = cookie_header(session)
    if ck:
        hdrs["Cookie"] = ck
    return hdrs

def logid() -> str:
    return str(random.randint(400_000_000_000_000_000, 999_999_999_999_999_999))


# ── URL helpers ──────────────────────────────────────────────────────────────
def extract_surl(url_or_surl: str) -> str:
    """Accept full URL or bare surl."""
    url_or_surl = url_or_surl.strip().rstrip("/")
    if url_or_surl.startswith("http"):
        return url_or_surl.split("/")[-1].lstrip("1")
    return url_or_surl.lstrip("1")


# ═════════════════════════════════════════════════════════════════════════════
# DIAGNOSTIC STEPS
# ═════════════════════════════════════════════════════════════════════════════

class DiagResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.warnings = 0
        self.findings = []

    def check(self, cond, pass_msg, fail_msg):
        if cond:
            ok(pass_msg)
            self.passed += 1
        else:
            fail(fail_msg)
            self.failed += 1
        return cond

    def finding(self, msg):
        self.findings.append(msg)
        warn(f"FINDING: {msg}")


def step1_domain_reachability(diag: DiagResult):
    """Probe every candidate domain with a simple GET to see which are alive."""
    header("Step 1 · Domain Reachability")
    info(f"Current code uses: {BASE_DOMAIN_CURRENT}")
    info("Probing candidate domains…\n")

    reachable = []
    for domain in CANDIDATE_DOMAINS:
        url = f"https://{domain}/"
        try:
            r = requests.get(url, timeout=8, allow_redirects=True,
                             headers={"User-Agent": USER_AGENTS[0]})
            final = r.url
            status = r.status_code
            is_ok = status < 400
            tag = f"{GREEN}OK{RESET}" if is_ok else f"{RED}{status}{RESET}"
            redirect_note = f"  → {final}" if final.rstrip("/") != url.rstrip("/") else ""
            print(f"    [{tag}] {domain:30s}  HTTP {status}{redirect_note}")
            if is_ok:
                reachable.append((domain, final, status))
        except Exception as e:
            print(f"    [{RED}ERR{RESET}] {domain:30s}  {e}")

    if not reachable:
        diag.check(False, "", "No candidate domain is reachable!")
    else:
        diag.check(True, f"{len(reachable)} domain(s) reachable", "")

    # Flag if code's domain is dead
    code_alive = any(d == BASE_DOMAIN_CURRENT for d, _, _ in reachable)
    if not code_alive and reachable:
        best = reachable[0]
        diag.finding(
            f"Code's domain '{BASE_DOMAIN_CURRENT}' is DEAD.  "
            f"Best candidate: '{best[0]}' (redirects to {best[1]})"
        )

    return reachable


def step2_cookies(diag: DiagResult):
    """Validate that cookies are loaded from .env."""
    header("Step 2 · Cookie Validation")
    cookies = load_all_cookies()
    diag.check(len(cookies) > 0, f"Loaded {len(cookies)} cookie set(s) from .env",
               "No COOKIES* found in .env — all API calls will be unauthenticated")
    if cookies:
        # Spot-check: does the first cookie contain the required keys?
        required_keys = {"ndut_fmt", "browserid", "csrfToken"}
        first = cookies[0]
        found_keys = set()
        for part in first.split(";"):
            if "=" in part:
                k = part.strip().split("=", 1)[0].strip()
                found_keys.add(k)
        missing = required_keys - found_keys
        if missing:
            warn(f"COOKIES1 is missing expected keys: {missing}")
        else:
            ok(f"COOKIES1 contains all expected keys: {required_keys}")
        info(f"Keys in COOKIES1: {sorted(found_keys)}")
    return cookies


def step3_jstoken(diag: DiagResult, session: requests.Session, surl: str, base_url: str):
    """Try to extract jsToken from the share page HTML."""
    header("Step 3 · jsToken Extraction")
    filelist_url = f"{base_url}/wap/share/filelist?surl={surl}&clearCache=1"
    info(f"GET {filelist_url}")

    try:
        r = session.get(filelist_url, headers=make_headers(session, surl, base_url), timeout=60)
    except Exception as e:
        diag.check(False, "", f"HTTP request failed: {e}")
        return None

    diag.check(r.status_code == 200, f"HTTP {r.status_code}",
               f"HTTP {r.status_code} (expected 200)")

    html = r.text
    info(f"Response length: {len(html)} chars")

    # Log a snippet of the HTML for manual inspection
    subhdr("HTML head (first 500 chars)")
    print(textwrap.indent(html[:500], "    "))
    subhdr("HTML tail (last 300 chars)")
    print(textwrap.indent(html[-300:], "    "))

    # Pattern 1: encoded fn call
    m = re.search(r'fn%28%22([A-Fa-f0-9]+)%22%29', html)
    if m:
        tok = m.group(1)
        ok(f"jsToken found via pattern 1 (fn%28%22…): {tok[:40]}…")
        return tok

    # Pattern 2: eval + decodeURIComponent
    m = re.search(r'eval\(decodeURIComponent\(`([^`]+)`\)\)', html)
    if m:
        decoded = unquote(m.group(1))
        m2 = re.search(r'fn\("([A-Fa-f0-9]+)"\)', decoded)
        if m2:
            tok = m2.group(1)
            ok(f"jsToken found via pattern 2 (eval+decode): {tok[:40]}…")
            return tok
        else:
            warn("Pattern 2 matched eval block but no fn() inside.")
            dump("Decoded block", decoded)

    # Pattern 3: Try broader search for any hex token ≥ 32 chars
    hex_matches = re.findall(r'["\']([A-Fa-f0-9]{32,})["\']', html)
    if hex_matches:
        warn(f"No standard jsToken pattern matched, but found {len(hex_matches)} hex string(s) ≥32 chars:")
        for i, hm in enumerate(hex_matches[:5]):
            info(f"  candidate[{i}]: {hm[:50]}… ({len(hm)} chars)")
        diag.finding("jsToken extraction patterns are stale — found hex candidates but standard regex failed")
        return hex_matches[0]  # use first as best guess

    # Pattern 4: Search for jsToken in JSON embedded in page
    m = re.search(r'"jsToken"\s*:\s*"([^"]+)"', html)
    if m:
        tok = m.group(1)
        ok(f"jsToken found in embedded JSON: {tok[:40]}…")
        return tok

    # Pattern 5: Search for fn(...) with any encoding
    m = re.search(r'fn\s*\(\s*["\']([A-Fa-f0-9]+)["\']\s*\)', html)
    if m:
        tok = m.group(1)
        ok(f"jsToken found via loose fn() pattern: {tok[:40]}…")
        return tok

    diag.check(False, "", "Could NOT extract jsToken from page (all patterns failed)")
    diag.finding("jsToken extraction is broken — the page HTML structure has changed")

    # Dump interesting-looking script blocks for analysis
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
    if scripts:
        subhdr(f"Found {len(scripts)} <script> blocks — dumping first 3:")
        for i, s in enumerate(scripts[:3]):
            print(f"\n  {DIM}--- script[{i}] ({len(s)} chars) ---{RESET}")
            print(textwrap.indent(s[:400], "    "))
            if len(s) > 400:
                print(f"    {DIM}… truncated …{RESET}")

    return None


def step4_share_info(diag: DiagResult, session: requests.Session, js_token: str, surl: str, base_url: str):
    """Call the shorturlinfo API and validate the response."""
    header("Step 4 · Share Info API (/api/shorturlinfo)")

    params = {
        "app_id": "250528", "shorturl": f"1{surl}", "root": "1",
        "web": "1", "channel": "dubox", "clienttype": "0",
        "jsToken": js_token, "t": str(int(time.time())), "dp-logid": logid(),
    }
    hdrs = make_headers(session, surl, base_url)
    hdrs.update({"Accept": "application/json, text/plain, */*", "Origin": base_url})

    url = f"{base_url}/api/shorturlinfo"
    info(f"GET {url}")
    dump("Params", params)

    try:
        r = session.get(url, params=params, headers=hdrs, timeout=60)
    except Exception as e:
        diag.check(False, "", f"HTTP request failed: {e}")
        return None

    diag.check(r.status_code == 200, f"HTTP {r.status_code}",
               f"HTTP {r.status_code} (expected 200)")

    # Try to parse JSON
    try:
        data = r.json()
    except Exception:
        diag.check(False, "", "Response is NOT valid JSON")
        dump("Raw body", r.text)
        return None

    ok("Response is valid JSON")
    info(f"Top-level keys: {list(data.keys())}")

    errno = data.get("errno")
    diag.check(errno == 0, f"errno = {errno}", f"errno = {errno} (expected 0)")
    if errno != 0:
        dump("Full response", json.dumps(data, indent=2, default=str))
        diag.finding(f"shorturlinfo returned errno={errno} — the share link may be invalid or expired")
        return None

    # Validate file list
    files = data.get("list", [])
    diag.check(len(files) > 0, f"Found {len(files)} file(s)", "File list is empty!")

    if files:
        f0 = files[0]
        info(f"Filename: {f0.get('server_filename', 'N/A')}")
        info(f"Size: {f0.get('size', 'N/A')} bytes")
        info(f"fs_id: {f0.get('fs_id', 'N/A')}")
        info(f"isdir: {f0.get('isdir', 'N/A')}")

    # Critical metadata
    for key in ["shareid", "uk", "sign", "timestamp"]:
        val = data.get(key)
        if val is not None:
            ok(f"{key} = {str(val)[:60]}")
        else:
            diag.check(False, "", f"'{key}' is MISSING from response")

    # Dump full response for reference
    subhdr("Full shorturlinfo response (truncated)")
    print(textwrap.indent(json.dumps(data, indent=2, default=str)[:2000], "    "))

    return data


def step5_streaming_endpoint(
    diag: DiagResult,
    session: requests.Session,
    share_info: dict,
    surl: str,
    base_url: str,
):
    """Probe the /share/streaming endpoint with every quality level."""
    header("Step 5 · Streaming Endpoint (/share/streaming)")

    files = share_info.get("list", [])
    if not files:
        fail("No files — cannot test streaming.")
        return

    f0 = files[0]
    fs_id    = f0["fs_id"]
    shareid  = share_info["shareid"]
    uk       = share_info["uk"]
    sign     = share_info["sign"]
    timestamp = share_info["timestamp"]

    info(f"Testing fs_id={fs_id}, shareid={shareid}, uk={uk}")
    info(f"sign={str(sign)[:30]}…, timestamp={timestamp}")

    any_m3u8 = False

    for quality in QUALITIES:
        subhdr(f"Quality: {quality}")

        params = {
            "uk": str(uk), "shareid": str(shareid), "type": quality,
            "fid": str(fs_id), "sign": sign, "timestamp": str(timestamp),
            "jsToken": "", "esl": "1", "isplayer": "1", "ehps": "1",
            "clienttype": "0", "app_id": "250528", "web": "1",
            "channel": "dubox", "dp-logid": logid(),
        }
        url = f"{base_url}/share/streaming?{urlencode(params)}"
        info(f"GET {url[:120]}…")

        try:
            r = session.get(url, headers=make_headers(session, surl, base_url), timeout=60)
        except Exception as e:
            fail(f"Request failed: {e}")
            continue

        info(f"HTTP {r.status_code}, Content-Type: {r.headers.get('Content-Type', 'N/A')}")
        info(f"Response length: {len(r.text)} chars")

        body = r.text.strip()

        # Check for redirect
        if r.history:
            info(f"Redirect chain: {[resp.status_code for resp in r.history]}")
            info(f"Final URL: {r.url[:120]}")

        # Is it M3U8?
        if body.startswith("#EXTM3U"):
            ok("Response is a valid M3U8 playlist!")
            any_m3u8 = True

            # Parse segments
            lines = [l.strip() for l in body.split("\n") if l.strip()]
            seg_urls = [l for l in lines if not l.startswith("#")]
            info(f"Total lines: {len(lines)}, Segment URLs: {len(seg_urls)}")

            if seg_urls:
                # Analyze first segment URL
                seg0 = seg_urls[0]
                info(f"First segment: {seg0[:120]}…")
                parsed = urlparse(seg0)
                qs = parse_qs(parsed.query, keep_blank_values=True)
                info(f"Segment host: {parsed.hostname}")
                info(f"Segment path: {parsed.path}")
                info(f"Segment query keys: {list(qs.keys())}")

                ts_size = qs.get("ts_size", ["N/A"])[0]
                info(f"ts_size: {ts_size}")

                # Check if the chunk-index regex matches
                m = re.search(r'_(\d+)_ts/', parsed.path)
                if m:
                    ok(f"Chunk index pattern '_N_ts/' matched: index={m.group(1)}")
                else:
                    fail("Chunk index pattern '_N_ts/' did NOT match the segment path!")
                    diag.finding(
                        f"Segment URL path format changed — regex r'_(\\d+)_ts/' "
                        f"no longer matches: {parsed.path}"
                    )
                    # Try to find alternative patterns
                    alt_patterns = [
                        (r'/(\d+)\.ts', "N.ts"),
                        (r'chunk[_-]?(\d+)', "chunkN"),
                        (r'seg[_-]?(\d+)', "segN"),
                        (r'index[_-]?(\d+)', "indexN"),
                        (r'/(\d+)/', "bare /N/"),
                    ]
                    for pat, desc in alt_patterns:
                        m2 = re.search(pat, parsed.path)
                        if m2:
                            warn(f"  Alternative pattern '{desc}' matched: {m2.group(0)}")

                # Test-fetch the first segment
                subhdr("Probing first segment (HEAD request)")
                try:
                    sr = requests.head(seg0, headers=make_headers(session, surl, base_url),
                                       timeout=15, allow_redirects=True)
                    info(f"Segment HEAD → HTTP {sr.status_code}")
                    info(f"Content-Length: {sr.headers.get('Content-Length', 'N/A')}")
                    info(f"Content-Type: {sr.headers.get('Content-Type', 'N/A')}")
                except Exception as e:
                    warn(f"Segment HEAD failed: {e}")

            else:
                warn("M3U8 has no segment URLs (empty playlist)")

            # Dump full M3U8 (truncated)
            subhdr("Full M3U8 content")
            print(textwrap.indent(body[:1500], "    "))
            if len(body) > 1500:
                print(f"    {DIM}… truncated ({len(body)} total chars) …{RESET}")

        elif body.startswith("{"):
            # JSON error response
            try:
                jdata = json.loads(body)
                errno = jdata.get("errno", "?")
                errmsg = jdata.get("errmsg", jdata.get("error_msg", ""))
                fail(f"Got JSON error: errno={errno}, errmsg='{errmsg}'")
                dump("JSON response", json.dumps(jdata, indent=2, default=str))

                if errno == -20:
                    diag.finding("errno=-20 typically means authentication failure or expired cookies")
                elif errno == -6:
                    diag.finding("errno=-6 means rate limiting")
                elif errno == 2:
                    diag.finding("errno=2 means invalid share parameters")
                else:
                    diag.finding(f"Streaming returned errno={errno} for quality {quality}")
            except json.JSONDecodeError:
                fail("Response starts with '{' but is not valid JSON")
                dump("Raw body", body)
        else:
            fail(f"Response is neither M3U8 nor JSON — first 200 chars: {body[:200]}")
            diag.finding(f"Unexpected streaming response format for quality {quality}")

    if not any_m3u8:
        diag.check(False, "", "No quality level returned a valid M3U8 playlist!")
        diag.finding("The streaming endpoint is broken for ALL quality levels")


def step6_alternative_endpoints(
    diag: DiagResult,
    session: requests.Session,
    share_info: dict,
    surl: str,
    base_url: str,
):
    """Probe alternative/newer API endpoints that TeraBox may have introduced."""
    header("Step 6 · Probing Alternative Endpoints")

    files = share_info.get("list", [])
    if not files:
        fail("No files — cannot probe endpoints.")
        return

    f0 = files[0]
    fs_id    = f0["fs_id"]
    shareid  = share_info["shareid"]
    uk       = share_info["uk"]
    sign     = share_info["sign"]
    timestamp = share_info["timestamp"]

    # Candidate endpoints observed in TeraBox web player updates
    endpoints = [
        {
            "name": "/api/streaming",
            "path": "/api/streaming",
            "params": {
                "uk": str(uk), "shareid": str(shareid), "type": "M3U8_AUTO_1080",
                "fid": str(fs_id), "sign": sign, "timestamp": str(timestamp),
                "app_id": "250528", "web": "1", "channel": "dubox",
                "clienttype": "0", "dp-logid": logid(),
            },
        },
        {
            "name": "/share/streaming (with jsToken populated)",
            "path": "/share/streaming",
            "params": {
                "uk": str(uk), "shareid": str(shareid), "type": "M3U8_AUTO_1080",
                "fid": str(fs_id), "sign": sign, "timestamp": str(timestamp),
                "jsToken": share_info.get("jsToken", ""),  # if available in share_info
                "esl": "1", "isplayer": "1", "ehps": "1",
                "clienttype": "0", "app_id": "250528", "web": "1",
                "channel": "dubox", "dp-logid": logid(),
            },
        },
        {
            "name": "/share/videostream",
            "path": "/share/videostream",
            "params": {
                "uk": str(uk), "shareid": str(shareid), "type": "M3U8_AUTO_1080",
                "fid": str(fs_id), "sign": sign, "timestamp": str(timestamp),
                "app_id": "250528", "web": "1",
            },
        },
        {
            "name": "/api/filemetas (download link via dlink)",
            "path": "/api/filemetas",
            "params": {
                "fsids": json.dumps([int(fs_id)]),
                "uk": str(uk), "shareid": str(shareid),
                "sign": sign, "timestamp": str(timestamp),
                "app_id": "250528", "web": "1", "channel": "dubox",
                "clienttype": "0",
            },
        },
        {
            "name": "/share/download (direct link)",
            "path": "/share/download",
            "params": {
                "uk": str(uk), "shareid": str(shareid),
                "fid": str(fs_id), "sign": sign, "timestamp": str(timestamp),
                "app_id": "250528", "web": "1", "channel": "dubox",
            },
        },
        {
            "name": "/share/pclouddownload",
            "path": "/share/pclouddownload",
            "params": {
                "uk": str(uk), "shareid": str(shareid),
                "fid_list": json.dumps([int(fs_id)]),
                "sign": sign, "timestamp": str(timestamp),
                "app_id": "250528", "web": "1",
            },
        },
    ]

    for ep in endpoints:
        subhdr(ep["name"])
        url = f"{base_url}{ep['path']}"
        info(f"GET {url}")

        try:
            r = session.get(url, params=ep["params"],
                            headers=make_headers(session, surl, base_url), timeout=30)
            info(f"HTTP {r.status_code}, Content-Type: {r.headers.get('Content-Type', 'N/A')}")
            info(f"Response length: {len(r.text)} chars")

            body = r.text.strip()

            if body.startswith("#EXTM3U"):
                ok("Got M3U8 playlist!")
                seg_urls = [l.strip() for l in body.split("\n") if l.strip() and not l.startswith("#")]
                info(f"Segments: {len(seg_urls)}")
                diag.finding(f"WORKING ENDPOINT FOUND: {ep['name']} returns M3U8 with {len(seg_urls)} segments!")
                print(textwrap.indent(body[:800], "    "))

            elif body.startswith("{"):
                try:
                    jdata = json.loads(body)
                    errno = jdata.get("errno", "?")
                    info(f"JSON errno={errno}")

                    # Look for dlink or download URLs in the response
                    dlink = None
                    if "info" in jdata and isinstance(jdata["info"], list):
                        for item in jdata["info"]:
                            if isinstance(item, dict) and "dlink" in item:
                                dlink = item["dlink"]
                    elif "dlink" in jdata:
                        dlink = jdata["dlink"]
                    elif "list" in jdata and isinstance(jdata["list"], list):
                        for item in jdata["list"]:
                            if isinstance(item, dict) and "dlink" in item:
                                dlink = item["dlink"]

                    if dlink:
                        ok(f"Found dlink! ({len(dlink)} chars)")
                        info(f"dlink: {dlink[:120]}…")
                        diag.finding(f"WORKING ENDPOINT: {ep['name']} returns dlink (direct download URL)")

                    dump("Response", json.dumps(jdata, indent=2, default=str)[:600])

                except json.JSONDecodeError:
                    info(f"Starts with '{{' but not valid JSON: {body[:200]}")
            else:
                info(f"Response: {body[:200]}")

        except Exception as e:
            info(f"Request failed: {e}")


def step7_dlink_from_file_info(
    diag: DiagResult,
    session: requests.Session,
    share_info: dict,
    surl: str,
    base_url: str,
):
    """Check if dlink is already present in the share info file metadata."""
    header("Step 7 · Check dlink in Share Info File Metadata")

    files = share_info.get("list", [])
    if not files:
        fail("No files to check.")
        return

    f0 = files[0]
    subhdr("File[0] keys and values")
    for k, v in f0.items():
        sv = str(v)
        if len(sv) > 100:
            sv = sv[:100] + "…"
        print(f"    {k}: {sv}")

    dlink = f0.get("dlink", "")
    if dlink:
        ok(f"dlink present in file metadata ({len(dlink)} chars)")
        info(f"dlink: {dlink[:120]}…")

        # Test the dlink
        subhdr("Probing dlink (HEAD)")
        try:
            r = requests.head(dlink, headers=make_headers(session, surl, base_url),
                              timeout=15, allow_redirects=True)
            info(f"HEAD → HTTP {r.status_code}")
            info(f"Content-Length: {r.headers.get('Content-Length', 'N/A')}")
            info(f"Content-Type: {r.headers.get('Content-Type', 'N/A')}")
            if r.status_code in (200, 206, 302):
                ok("dlink is reachable — could be used as direct download fallback!")
                diag.finding("dlink in file metadata is WORKING — consider using direct download instead of HLS")
        except Exception as e:
            warn(f"dlink probe failed: {e}")
    else:
        info("No dlink in file metadata (this is normal for the /wap/ path)")


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def main():
    test_url = sys.argv[1] if len(sys.argv) > 1 else "https://1024terabox.com/s/1mKoEsoPWtrnXZ_rNXbHZoA"
    surl = extract_surl(test_url)

    print(f"\n{BOLD}{'▓'*64}")
    print(f"  TeraBox Streaming Pipeline — Deep Diagnostic")
    print(f"{'▓'*64}{RESET}")
    print(f"  URL:  {test_url}")
    print(f"  surl: {surl}")
    print(f"  Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()

    diag = DiagResult()

    # ── Step 1: Domain reachability ───────────────────────────────────────
    reachable = step1_domain_reachability(diag)

    # Determine best domain to use for subsequent steps
    working_domain = BASE_DOMAIN_CURRENT
    if reachable:
        # Prefer the domain the code currently uses, else first reachable
        code_ok = any(d == BASE_DOMAIN_CURRENT for d, _, _ in reachable)
        if not code_ok:
            working_domain = reachable[0][0]
            warn(f"Falling back to domain: {working_domain}")
    base_url = f"https://{working_domain}"

    # ── Step 2: Cookies ──────────────────────────────────────────────────
    cookies = step2_cookies(diag)
    if not cookies:
        fail("No cookies available. Cannot proceed with authenticated requests.")
        return

    # Build session with first cookie set
    # Try setting cookies for both .1024tera.com and the working domain
    session, count = build_session(cookies[0], f".{working_domain.lstrip('dm.')}")
    info(f"Session loaded with {count} cookies (domain: .{working_domain.lstrip('dm.')})")

    # Also try setting for the original domain pattern
    for c in cookies[0].split(";"):
        if "=" in c:
            k, v = c.strip().split("=", 1)
            session.cookies.set(k.strip(), v.strip(), domain=f".{working_domain}", path="/")

    # ── Step 3: jsToken ──────────────────────────────────────────────────
    js_token = step3_jstoken(diag, session, surl, base_url)
    if not js_token:
        warn("Cannot proceed without jsToken. Trying with empty token…")
        js_token = ""

    # ── Step 4: Share info ───────────────────────────────────────────────
    share_info = step4_share_info(diag, session, js_token, surl, base_url)
    if not share_info:
        fail("Cannot proceed without share info.")
        # Print summary and exit
        _print_summary(diag)
        return

    # ── Step 5: Streaming endpoint ───────────────────────────────────────
    step5_streaming_endpoint(diag, session, share_info, surl, base_url)

    # ── Step 6: Alternative endpoints ────────────────────────────────────
    step6_alternative_endpoints(diag, session, share_info, surl, base_url)

    # ── Step 7: dlink check ──────────────────────────────────────────────
    step7_dlink_from_file_info(diag, session, share_info, surl, base_url)

    # ── Summary ──────────────────────────────────────────────────────────
    _print_summary(diag)


def _print_summary(diag: DiagResult):
    header("SUMMARY")
    print(f"\n  {GREEN}{diag.passed} passed{RESET}  |  {RED}{diag.failed} failed{RESET}\n")

    if diag.findings:
        print(f"  {BOLD}{YELLOW}Key Findings:{RESET}")
        for i, f in enumerate(diag.findings, 1):
            print(f"    {i}. {f}")
        print()

    if diag.failed == 0:
        print(f"  {GREEN}{BOLD}All checks passed!{RESET}\n")
    else:
        print(f"  {RED}{BOLD}Issues detected — see findings above.{RESET}")
        print(f"\n  {YELLOW}Suggested next steps:{RESET}")
        print(f"    1. If the domain changed → update BASE_DOMAIN in internal_helpers.py")
        print(f"    2. If streaming returns JSON errors → cookies may be expired or rate-limited")
        print(f"    3. If M3U8 segment URL format changed → update chunk-index regex in core_pipeline.py")
        print(f"    4. If alternative endpoints return dlinks → consider a direct-download fallback")
        print(f"    5. If jsToken patterns failed → update regex in get_js_token()")
        print()


if __name__ == "__main__":
    sys.exit(main() or 0)
