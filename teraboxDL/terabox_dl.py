import os
import re
import time
import random
import logging
import threading
import requests
from urllib.parse import unquote, urlparse, urlunparse, urlencode, parse_qs

from network import get_session, USER_AGENTS
from .errors import TeraBoxDirectError, TeraBoxRateLimited, TeraBoxMultipleChoice

log = logging.getLogger(__name__)

# Cookie validation cache — avoid re-validating on every download
_cookie_cache: dict = {}  # cookies_str -> (is_valid, expiry)
_cookie_cache_lock = threading.Lock()
_COOKIE_CACHE_TTL = 300  # 5 minutes
_COOKIE_CACHE_MAX = 50

__all__ = [
    "TeraBoxDirectError",
    "TeraBoxRateLimited",
    "TeraBoxMultipleChoice",
    "get_video_info",
    "list_share_files",
    "cookie_pool_health",
]


# ── TeraBox Error Codes ─────────────────────────────────────────────────────────—————
# Maps errno values to user-friendly messages
TERABOX_ERRNO_MAP = {
    -1: "TeraBox server error (internal). Try again later.",
    -2: "Invalid parameters. The share link may be malformed.",
    -3: "Share link expired or doesn't exist. Ask the sender to re-share.",
    -4: "Share link has been revoked by the owner.",
    -5: "Account limit exceeded. Ask the sender to delete some files first.",
    -6: "Share link expired. Ask the sender to generate a new one.",
    -7: "Too many files in this share. Try a different link.",
    -8: "File not found in this share. The file may have been deleted.",
    -9: "This share is password-protected. Password entry is not supported yet.",
    -10: "Rate limited by TeraBox. Wait a few minutes and try again.",
    -11: "Bad request. The link format is incorrect.",
    -12: "Session expired. Try again in a moment.",
    -14: "Share link not found. Check the URL and try again.",
    -21: "Storage quota exceeded on TeraBox.",
    -22: "Download limit reached for this share.",
}


def _classify_errno(errno: int) -> str:
    """Return a user-friendly message for a TeraBox errno code."""
    if errno in TERABOX_ERRNO_MAP:
        return TERABOX_ERRNO_MAP[errno]
    if errno < -100:
        return f"TeraBox server error (errno={errno}). Try again later."
    return f"TeraBox error (errno={errno}). Try a different download mode."

# ── Config ─────────────────────────────────────────────────────────────────────────———
BASE_DOMAIN = "dm.1024tera.com"
BASE_URL = f"https://{BASE_DOMAIN}"


def _logid() -> str:
    return str(random.randint(400_000_000_000_000_000, 999_999_999_999_999_999))


def _extract_surl(terabox_url: str) -> str:
    """Extract the share URL ID (surl) from a TeraBox share link."""
    # Path form: /s/1ABC...
    m = re.search(r'/s/1([A-Za-z0-9_-]+)', terabox_url)
    if m:
        return m.group(1)
    # Query param form: ?surl=1ABC...
    m = re.search(r'[?&]surl=1?([A-Za-z0-9_-]+)', terabox_url)
    if m:
        return m.group(1)
    raise TeraBoxDirectError(f"Could not extract share ID from URL: {terabox_url}")


def _load_session(cookies_str: str = ""):
    """Return the global session. Cookies are passed via headers, not mutated."""
    return get_session()


def _validate_cookies(session: requests.Session, cookies_str: str = "") -> str:
    """
    Validate that cookies are still working by making a test request.

    Returns:
        "valid"   — TeraBox confirmed the session works
        "invalid" — TeraBox answered and rejected the session
        "unknown" — network/API error; treat the cookie as usable (fail-open)
                    so a transient blip doesn't poison the whole pool
    """
    if not cookies_str:
        # Try to extract from session
        cookie_str = "; ".join(
            f"{c.name}={c.value}" for c in session.cookies
            if "1024tera" in (c.domain or "")
        )
        if not cookie_str:
            return "valid"  # No cookies to validate
        cookies_str = cookie_str

    # Test request to check if cookies are valid
    try:
        test_url = f"{BASE_URL}/api/user/info"
        headers = _headers(session, "", cookies_str)
        resp = session.get(test_url, headers=headers, timeout=10)

        # Check if response indicates valid session
        if resp.status_code == 200:
            try:
                data = resp.json()
                # If errno is 0, cookies are valid
                return "valid" if data.get("errno", -1) == 0 else "invalid"
            except ValueError:
                return "unknown"
        if resp.status_code >= 500:
            return "unknown"  # server-side issue, not the cookie's fault
        return "invalid"
    except Exception as e:
        log.warning(f"Cookie validation error (fail-open): {e}")
        return "unknown"


class _CookiePool:
    """
    Round-robin pool over COOKIES1..N with validity caching and
    rate-limit invalidation.

    - `acquire()` returns the next cookie string ("" when none are configured,
      None when all configured cookies have been exhausted this cycle).
    - `mark_rate_limited()` invalidates a cookie immediately and advances the
      pointer so the next acquire() skips it.
    - Validity results (from _validate_cookies) are cached for 5 minutes.
    """

    def __init__(self, max_cookies: int = 10) -> None:
        self._cookies: list[tuple[int, str]] = []   # [(index, cookies_str)]
        self._pointer = 0
        self._lock = threading.Lock()

        # Preferred: numbered COOKIES1..N (docker env_file silently keeps
        # only the LAST of duplicate `COOKIES=` lines — a config that looked
        # like 3 cookies was really zero reaching this pool).
        for i in range(1, max_cookies + 1):
            c = os.getenv(f"COOKIES{i}", "")
            if c:
                self._cookies.append((i, c))

        # Fallback: single COOKIES var holding multiple cookie strings
        # separated by newlines or '||' (works with quoted multiline values).
        if not self._cookies:
            raw = os.getenv("COOKIES", "")
            if raw:
                parts = [p.strip() for p in
                         raw.replace("||", "\n").splitlines() if p.strip()]
                self._cookies = list(enumerate(parts, start=1))
                if len(self._cookies) > 1:
                    log.warning(
                        "Single COOKIES var held %d cookies — switch to "
                        "COOKIES1..%d in .env so docker does not drop them",
                        len(self._cookies), len(self._cookies),
                    )

    def __len__(self) -> int:
        return len(self._cookies)

    def _is_cached_valid(self, cookies_str: str) -> bool | None:
        """True/False if a fresh validation result exists, else None."""
        now = time.time()
        with _cookie_cache_lock:
            cached = _cookie_cache.get(cookies_str)
            if cached and cached[1] > now:
                return cached[0]
        return None

    def _cache_validity(self, cookies_str: str, is_valid: bool) -> None:
        with _cookie_cache_lock:
            _cookie_cache[cookies_str] = (is_valid, time.time() + _COOKIE_CACHE_TTL)
            # Prune expired entries to prevent unbounded growth
            if len(_cookie_cache) > _COOKIE_CACHE_MAX:
                now = time.time()
                expired = [k for k, v in _cookie_cache.items() if v[1] < now]
                for k in expired:
                    _cookie_cache.pop(k, None)

    def invalidate(self, cookies_str: str) -> None:
        """Mark a cookie invalid (rate-limited/expired) and skip it next time."""
        with _cookie_cache_lock:
            _cookie_cache[cookies_str] = (False, time.time() + _COOKIE_CACHE_TTL)

    def acquire(self) -> str | None:
        """
        Return the next usable cookie string.

        Returns:
            ""    — no cookies configured at all (anonymous mode)
            str   — a cookie string to try
            None  — all configured cookies are currently unusable

        Locking note: self._lock is only held for pointer bookkeeping.
        Live validation happens OUTSIDE the lock — it's a network call
        (up to 10s) and holding the lock there would serialize every
        concurrent download behind one HTTP request.
        """
        if not self._cookies:
            return ""
        n = len(self._cookies)
        for _ in range(n):
            with self._lock:
                idx, cookies_str = self._cookies[self._pointer % n]
                self._pointer = (self._pointer + 1) % n
            cached = self._is_cached_valid(cookies_str)
            if cached is True:
                return cookies_str
            if cached is False:
                continue  # known-bad → skip
            # Not cached → validate live once (outside the lock!)
            verdict = _validate_cookies(get_session(), cookies_str)
            if verdict == "valid":
                self._cache_validity(cookies_str, True)
                log.info(f"Using valid cookies from COOKIES{idx}")
                return cookies_str
            if verdict == "invalid":
                self._cache_validity(cookies_str, False)
                log.warning(f"COOKIES{idx} is invalid or expired")
                continue
            # "unknown" (network blip) → don't cache, fail open and use it
            log.warning(f"COOKIES{idx} validation inconclusive — using anyway")
            return cookies_str
        return None

    def health(self) -> list[dict]:
        """Per-cookie health snapshot for /status."""
        out = []
        now = time.time()
        for idx, cookies_str in self._cookies:
            with _cookie_cache_lock:
                cached = _cookie_cache.get(cookies_str)
            if cached and cached[1] > now:
                state = "ok" if cached[0] else "bad"
            else:
                state = "unknown"
            out.append({"index": idx, "state": state})
        return out


# Module-level singleton — resolvers and /status share one pool.
cookie_pool = _CookiePool()


def cookie_pool_health() -> list[dict]:
    """Public accessor for /status (avoids importing the private class)."""
    return cookie_pool.health()


# Hook fired when every configured cookie is exhausted. The app layer
# (telegram_logic) sets this at startup to DM the admin — keeps teraboxDL
# free of reverse imports.
_pool_exhausted_hook = None


def set_pool_exhausted_hook(fn) -> None:
    global _pool_exhausted_hook
    _pool_exhausted_hook = fn


def _notify_pool_exhausted() -> None:
    if _pool_exhausted_hook is not None:
        try:
            _pool_exhausted_hook()
        except Exception as e:  # never let alerting break downloads
            log.warning(f"pool_exhausted hook failed: {e}")


def _extract_thumb_url(info: dict, file_info: dict) -> str:
    """
    Pull the first usable thumbnail URL from share/file metadata.

    TeraBox shapes vary across API versions: thumbs may be a dict with a
    'url' list, a bare list of strings, or absent entirely.
    """
    candidates = []
    for source in (file_info.get("thumbs"), info.get("thumbs")):
        if isinstance(source, dict):
            candidates.extend(source.get("url") or [])
        elif isinstance(source, list):
            candidates.extend(source)
    for c in candidates:
        if isinstance(c, str) and c.startswith("http"):
            return c
    return ""


def _headers(session: requests.Session, surl: str = "", cookies_str: str = "") -> dict:
    hdrs = {
        "User-Agent": random.choice(USER_AGENTS),
        "Referer": f"{BASE_URL}/wap/share/filelist?surl={surl}" if surl else f"{BASE_URL}/wap/share/filelist",
    }
    if cookies_str:
        hdrs["Cookie"] = cookies_str
    else:
        cookie_str = "; ".join(
            f"{c.name}={c.value}" for c in session.cookies
            if "1024tera" in (c.domain or "")
        )
        if cookie_str:
            hdrs["Cookie"] = cookie_str
    return hdrs


def _get_js_token(session: requests.Session, surl: str, cookies_str: str = "") -> str:
    """Extract jsToken from the TeraBox share page HTML."""
    url = f"{BASE_URL}/wap/share/filelist?surl={surl}&clearCache=1"
    last_err = "Unknown error"
    for attempt in range(3):
        try:
            html = session.get(url, headers=_headers(session, surl, cookies_str), timeout=60).text
            m = re.search(r'fn%28%22([A-Fa-f0-9]+)%22%29', html)
            if m:
                return m.group(1)
            m = re.search(r'eval\(decodeURIComponent\(`([^`]+)`\)\)', html)
            if m:
                m2 = re.search(r'fn\("([A-Fa-f0-9]+)"\)', unquote(m.group(1)))
                if m2:
                    return m2.group(1)
            last_err = "Token patterns not found in HTML (link may be expired or password-protected)"
        except requests.ConnectionError:
            last_err = "Cannot reach TeraBox servers. Check your internet connection."
        except requests.Timeout:
            last_err = "TeraBox page timed out. The server may be overloaded."
        except requests.RequestException as e:
            last_err = f"Network error: {e}"
        if attempt < 2:
            time.sleep(0.5)
    raise TeraBoxDirectError(f"Could not extract jsToken: {last_err}")


def _get_share_info(session: requests.Session, js_token: str, surl: str, cookies_str: str = "") -> dict:
    """Fetch file metadata from TeraBox API."""
    params = {
        "app_id": "250528", "shorturl": f"1{surl}", "root": "1",
        "web": "1", "channel": "dubox", "clienttype": "0",
        "jsToken": js_token, "t": str(int(time.time())), "dp-logid": _logid(),
    }
    hdrs = _headers(session, surl, cookies_str)
    hdrs.update({"Accept": "application/json, text/plain, */*", "Origin": BASE_URL})

    try:
        resp = session.get(f"{BASE_URL}/api/shorturlinfo", params=params, headers=hdrs, timeout=60)
    except requests.ConnectionError:
        raise TeraBoxDirectError("Cannot reach TeraBox servers. Check your internet connection.")
    except requests.Timeout:
        raise TeraBoxDirectError("TeraBox API timed out. The server may be overloaded. Try again later.")
    except requests.RequestException as e:
        raise TeraBoxDirectError(f"Network error while fetching metadata: {e}")

    # Rate limit detection (HTTP 429) — triggers cookie rotation upstream
    if resp.status_code == 429:
        raise TeraBoxRateLimited("Rate limited by TeraBox. Wait a few minutes and try again.")

    # Geo-block / cookie-level 403 — rotating cookies can help here too
    if resp.status_code == 403:
        raise TeraBoxRateLimited("Access denied by TeraBox. This content may be geo-blocked in your region.")

    # Server error
    if resp.status_code >= 500:
        raise TeraBoxDirectError(f"TeraBox server error (HTTP {resp.status_code}). Try again later.")

    try:
        data = resp.json()
    except ValueError:
        raise TeraBoxDirectError("Invalid response from TeraBox. The service may be temporarily unavailable.")

    errno = data.get("errno", 0)
    if errno != 0:
        if errno == -10:
            # Rate limited by TeraBox → rotate cookies upstream
            raise TeraBoxRateLimited(_classify_errno(errno))
        msg = _classify_errno(errno)
        raise TeraBoxDirectError(msg)

    return data


def _build_streaming_url(shareid, uk, sign, timestamp, fs_id, quality: str) -> str:
    """Construct the TeraBox HLS streaming URL."""
    return f"{BASE_URL}/share/streaming?" + urlencode({
        "uk": str(uk), "shareid": str(shareid), "type": quality,
        "fid": str(fs_id), "sign": sign, "timestamp": str(timestamp),
        "jsToken": "", "esl": "1", "isplayer": "1", "ehps": "1",
        "clienttype": "0", "app_id": "250528", "web": "1",
        "channel": "dubox", "dp-logid": _logid(),
    })


def _probe_quality(session, shareid, uk, sign, timestamp, fs_id, surl, cookies_str: str, quality: str) -> bool:
    """
    Single cheap request to check whether a quality tier is available.

    Without this probe, an unavailable requested quality would burn the
    entire discovery budget (up to 100 requests / 2 min) before the
    AUTO_1080 fallback kicked in.
    """
    try:
        url = _build_streaming_url(shareid, uk, sign, timestamp, fs_id, quality)
        text = session.get(url, headers=_headers(session, surl, cookies_str), timeout=15).text.strip()
        return text.startswith("#EXTM3U")
    except Exception as e:
        log.warning(f"Quality probe {quality} failed (will still attempt discovery): {e}")
        return False


def _discover_all_hls_chunks(
    session,
    shareid,
    uk,
    sign,
    timestamp,
    fs_id,
    surl,
    cookies_str="",
    quality: str = "M3U8_AUTO_1080",
) -> str:
    """
    Poll the TeraBox streaming endpoint to collect all HLS chunks,
    then build an M3U8 playlist and return it as a string (not a file).

    Returns the M3U8 manifest text directly — avoids disk write/read.
    Uses request collapsing to avoid duplicate requests.
    """
    known = {}
    req_count = 0
    max_known_idx = -1
    no_new_max_streak = 0
    max_retries = 100
    # Consecutive discovery polls with nothing new before we declare the
    # playlist complete. Small + constant so it is always reachable.
    _DISCOVERY_STREAK = 4
    deadline = time.monotonic() + 120  # 2-minute hard limit
    
    # Request collapsing: cache responses to avoid duplicate requests
    _response_cache = {}
    _cache_ttl = 0.5  # Cache valid for 0.5 seconds
    
    while req_count < max_retries and time.monotonic() < deadline:
        req_count += 1
        url = _build_streaming_url(shareid, uk, sign, timestamp, fs_id, quality)
        
        # Request collapsing: check if we recently fetched this URL
        now = time.time()
        # dp-logid is randomized per build — strip it or the cache never hits
        cache_key = url.split("dp-logid=")[0]
        cached = _response_cache.get(cache_key)
        if cached and now - cached[0] < _cache_ttl:
            log.debug(f"HLS chunk discovery: using cached response for request {req_count}")
            text = cached[1]
        else:
            try:
                text = session.get(url, headers=_headers(session, surl, cookies_str), timeout=60).text.strip()
                _response_cache[cache_key] = (now, text)
            except Exception as e:
                log.warning(f"HLS chunk discovery: error on request {req_count}: {e}")
                time.sleep(0.3)
                continue
        
        # Clean old cache entries
        if len(_response_cache) > 10:
            _response_cache.update({k: v for k, v in _response_cache.items() if now - v[0] < _cache_ttl})
        
        if not text.startswith("#EXTM3U"):
            time.sleep(0.05)
            continue
        
        segs = [l.strip() for l in text.split("\n") if l.strip() and not l.startswith("#")]
        for seg_url in segs:
            parsed = urlparse(seg_url)
            p = parse_qs(parsed.query, keep_blank_values=True)
            ts_size = int(p.get("ts_size", ["0"])[0])
            if ts_size <= 0:
                continue
            m = re.search(r'_(\d+)_ts/', parsed.path)
            if not m:
                continue
            chunk_idx = int(m.group(1))
            if chunk_idx not in known:
                p["range"] = [f"0-{ts_size - 1}"]
                p["len"] = [str(ts_size)]
                full_url = urlunparse(parsed._replace(query=urlencode({k: v[0] for k, v in p.items()})))
                known[chunk_idx] = (full_url, ts_size)
        
        current_max = max(known.keys()) if known else -1
        if current_max > max_known_idx:
            max_known_idx = current_max
            no_new_max_streak = 0
        else:
            no_new_max_streak += 1
        
        # Check if complete (contiguous range starting at 0/1). A CONSTANT
        # streak threshold keeps the exit reachable for any video length —
        # the old `confidence = max(10, max_known_idx)` scaled past the
        # 100-attempt cap, forcing every download to burn the full budget.
        if known and min(known) <= 1 and len(known) == max(known) - min(known) + 1:  # contiguous from head
            if no_new_max_streak >= _DISCOVERY_STREAK:
                break

        budget = min(max_retries, max(30, max_known_idx * 3))
        if req_count >= budget:
            break
    
    if not known:
        raise TeraBoxDirectError(
            "Could not discover any video chunks. "
            "The link may be expired, geo-blocked, or the video is no longer available."
        )
    
    log.info(f"Discovered {len(known)}/{max_known_idx + 1} chunks in {req_count} requests")
    
    # Build M3U8 playlist as string (no disk I/O)
    lines = ["#EXTM3U\n#EXT-X-VERSION:3\n"]
    for idx in sorted(known):
        chunk_url, ts_size = known[idx]
        duration = ts_size / (256 * 1024)
        lines.append(f"#EXTINF:{duration:.3f},\n{chunk_url}\n")
    lines.append("#EXT-X-ENDLIST\n")
    return "".join(lines)


def _resolve_share(session, surl: str) -> tuple[dict, str]:
    """
    Resolve jsToken + share info for a surl, rotating cookies on rate limits.

    Returns (info_dict, cookies_str_used).
    Raises TeraBoxDirectError when nothing works.
    """
    info = None
    rate_err: TeraBoxRateLimited | None = None
    max_attempts = len(cookie_pool) + 1 if cookie_pool else 2
    for _attempt in range(max_attempts):
        cookies_str = cookie_pool.acquire()
        if cookies_str is None:
            # Every configured cookie is exhausted → alert + surface error
            if rate_err is not None:
                _notify_pool_exhausted()
                raise TeraBoxDirectError(
                    "All TeraBox cookies are rate-limited or expired. "
                    "Try again in a few minutes or update COOKIES1..N."
                )
            break
        try:
            js_token = _get_js_token(session, surl, cookies_str)
            log.info(f"Got jsToken: {js_token[:16]}...")
            info = _get_share_info(session, js_token, surl, cookies_str)
            return info, cookies_str
        except TeraBoxRateLimited as e:
            log.warning(f"Cookie rate-limited for surl={surl}, rotating… ({e})")
            if not cookies_str:
                raise  # anonymous mode — nothing to rotate
            cookie_pool.invalidate(cookies_str)
            rate_err = e
            continue

    # No cookies configured — anonymous resolution with empty cookie header
    js_token = _get_js_token(session, surl, "")
    log.info(f"Got jsToken: {js_token[:16]}...")
    info = _get_share_info(session, js_token, surl, "")
    return info, ""


def list_share_files(terabox_url: str) -> dict:
    """
    Cheap metadata-only resolution: NO chunk discovery, just the file list.

    Used by the multi-file picker so users can choose a file before we pay
    for HLS discovery. Returns:
        {
            "files": [ {"fs_id": int, "name": str, "size": int,
                        "is_video": bool, "thumb_url": str}, ... ],
        }
    """
    surl = _extract_surl(terabox_url)
    session = _load_session()

    info, cookies_str = _resolve_share(session, surl)
    files = info.get("list", [])
    if not files:
        raise TeraBoxDirectError("No files found in this share. The link may be expired or the files were deleted.")

    VIDEO_EXTS = (
        ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
        ".m4v", ".ts", ".mpg", ".mpeg", ".3gp", ".vob", ".ogv",
    )
    out = []
    for f in files[:20]:  # hard cap — buttons can't show more anyway
        name = f.get("server_filename", "unknown")
        ext = os.path.splitext(name)[1].lower()
        out.append({
            "fs_id": int(f.get("fs_id", 0)),
            "name": name,
            "size": int(f.get("size", 0)),
            "is_video": (not ext) or (ext in VIDEO_EXTS),
            "thumb_url": _extract_thumb_url(info, f),
        })

    log.info(f"Share listing: {len(out)} file(s) for surl={surl}")
    return {"files": out}


def _get_video_metadata(terabox_url: str, quality: str = "M3U8_AUTO_1080", fs_id: int | None = None) -> dict:
    """
    Resolve TeraBox metadata directly (no proxy needed).

    For non-HD mode: discovers all HLS chunks and returns M3U8 manifest text.
    For HD mode: returns the direct download link (requires premium cookies).

    On 429/403/rate-limit errnos, the current cookie is invalidated and the
    next cookie in the pool is tried automatically.

    fs_id: pick a specific file from a multi-file share (default: first).
    """
    surl = _extract_surl(terabox_url)
    log.info(f"Resolving TeraBox metadata for surl={surl}")

    session = _load_session()

    # ── Resolve jsToken + share info, rotating cookies on rate limits ──
    info, cookies_str = _resolve_share(session, surl)

    files = info.get("list", [])
    if not files:
        raise TeraBoxDirectError("No files found in this share. The link may be expired or the files were deleted.")

    shareid = info["shareid"]
    uk = info["uk"]
    sign = info["sign"]
    timestamp = info["timestamp"]

    # ── Multi-file share without explicit choice → hand the list to the UI ──
    if fs_id is None and len(files) > 1:
        VIDEO_EXTS_MC = (
            ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
            ".m4v", ".ts", ".mpg", ".mpeg", ".3gp", ".vob", ".ogv",
        )
        brief = []
        for f in files[:20]:
            name = f.get("server_filename", "unknown")
            ext = os.path.splitext(name)[1].lower()
            brief.append({
                "fs_id": int(f.get("fs_id", 0)),
                "name": name,
                "size": int(f.get("size", 0)),
                "is_video": (not ext) or (ext in VIDEO_EXTS_MC),
            })
        raise TeraBoxMultipleChoice(brief)

    # ── Select requested file (or first) ──
    chosen = files[0]
    if fs_id is not None:
        match = next((f for f in files if int(f.get("fs_id", -1)) == int(fs_id)), None)
        if match is None:
            raise TeraBoxDirectError("Selected file is no longer available in this share.")
        chosen = match

    fs_id_val = chosen["fs_id"]
    filename = chosen.get("server_filename", "unknown")

    log.info(f"Share: {filename} ({len(files)} file(s))")

    # Check if file is a video before attempting HLS chunk discovery
    VIDEO_EXTS = (
        ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
        ".m4v", ".ts", ".mpg", ".mpeg", ".3gp", ".vob", ".ogv",
    )
    ext = os.path.splitext(filename)[1].lower()
    if ext and ext not in VIDEO_EXTS:
        raise TeraBoxDirectError(
            f"This file is not a video ({filename}). "
            f"Only video files (mp4, mkv, etc.) can be downloaded. "
            f"Non-video files like PDFs, documents, and archives are not supported."
        )

    # Step 3: Build streaming URL and discover all HLS chunks.
    # If the user-requested quality yields nothing, fall back to AUTO_1080 once.
    # Cheap single-request probe: skip straight to AUTO_1080 if the
    # requested tier isn't available, instead of burning the discovery
    # budget (100 requests / 2 min) on a dead quality.
    #
    # errno 130 = "quality not available". Small/low-res uploads often ONLY
    # have a 480p transcode, so the fallback must descend ALL the way
    # (1080 -> 720 -> 480) — the old chain stopped at 1080 and every
    # low-quality share failed with 'Could not discover any video chunks'.
    _TIER_ORDER = ["M3U8_AUTO_1080", "M3U8_AUTO_720", "M3U8_AUTO_480"]
    quality_to_use = quality
    # Always probe the requested quality first — saves the 100-request
    # discovery budget if the tier is unavailable.
    if not _probe_quality(
        session, shareid, uk, sign, timestamp, fs_id_val, surl, cookies_str or "", quality
    ):
        log.warning(f"Quality {quality} probe failed for surl={surl} — using AUTO_1080")
        quality_to_use = "M3U8_AUTO_1080"

    m3u8_text = None
    try:
        m3u8_text = _discover_all_hls_chunks(
            session, shareid, uk, sign, timestamp, fs_id_val, surl,
            cookies_str or "", quality=quality_to_use,
        )
    except TeraBoxDirectError:
        # descend tiers BELOW the one we tried (dedup, keep order)
        tried_idx = _TIER_ORDER.index(quality_to_use) if quality_to_use in _TIER_ORDER else 0
        for tier in _TIER_ORDER[tried_idx + 1:]:
            log.warning(f"Quality {quality_to_use} unavailable (errno 130?) for surl={surl} — trying {tier}")
            try:
                m3u8_text = _discover_all_hls_chunks(
                    session, shareid, uk, sign, timestamp, fs_id_val, surl,
                    cookies_str or "", quality=tier,
                )
                quality_to_use = tier
                break
            except TeraBoxDirectError:
                continue
        if m3u8_text is None:
            raise

    # Return metadata with M3U8 text (no file path — avoids disk I/O)
    return {
        "list": [{
            "fs_id": int(fs_id_val),
            "server_filename": chosen.get("server_filename", "video.mp4"),
            "size": int(chosen.get("size", 0)),
            "stream_url": m3u8_text,  # M3U8 manifest text (not a file path)
            "direct_link": "",  # HD direct link (not available without premium)
            "thumb_url": _extract_thumb_url(info, chosen),
        }]
    }

def _get_file_size_bytes(stream_download_url: str) -> int:
    try:
        response = get_session().head(
            stream_download_url,
            allow_redirects=True,
            timeout=15,
        )
        content_length = response.headers.get('Content-Length')
        if content_length is None:
            raise ValueError("Server did not provide Content-Length header.")

        return int(content_length)

    except Exception as e:
        log.warning(f"HEAD size probe failed for {stream_download_url[:80]}: {e}")
        return 0


#!--------PUBLIC API------------

def get_video_info(terabox_url: str, is_hd: bool, quality: str = "M3U8_AUTO_1080", fs_id: int | None = None) -> dict:
    try:
        data = _get_video_metadata(terabox_url, quality=quality, fs_id=fs_id)
    except TeraBoxDirectError:
        raise  # already has user-friendly message
    except TeraBoxMultipleChoice:
        raise  # control-flow signal for the picker UI — pass through untouched
    except requests.ConnectionError:
        raise TeraBoxDirectError("Cannot reach TeraBox servers. Check your internet connection.")
    except requests.Timeout:
        raise TeraBoxDirectError("TeraBox request timed out. The server may be overloaded.")
    except Exception as e:
        log.exception(f"Unexpected error resolving TeraBox URL: {terabox_url}")
        raise TeraBoxDirectError(f"Failed to resolve TeraBox link: {e}")

    if data.get("error"):
        raise TeraBoxDirectError(data.get("message", "Unknown error in getting video metadata"))
    if "list" not in data or not data["list"]:
        raise TeraBoxDirectError("Video list not found or empty in metadata response")

    file_info = data["list"][0]

    if is_hd:
        direct_link = file_info.get("direct_link", "")
        if not direct_link:
            raise TeraBoxDirectError("HD mode requires a direct download link (TeraBox premium). Try /exp instead.")
        return {
            "filename": file_info.get("server_filename", "unknown"),
            "size": int(file_info.get("size", 0)),
            "download_url": direct_link,
            "fs_id": int(file_info.get("fs_id", 0)),
            "thumb_url": file_info.get("thumb_url", ""),
        }
    else:
        download_url = file_info.get("stream_url", "")
        # If stream_url is M3U8 text (not a file path or URL), get size from metadata
        if download_url and not download_url.startswith("http") and not download_url.startswith("/") and "#EXTM3U" in download_url:
            file_size = int(file_info.get("size", 0))
        elif download_url:
            file_size = _get_file_size_bytes(download_url)
        else:
            file_size = int(file_info.get("size", 0))

        return {
            "filename": file_info.get("server_filename", "unknown"),
            "size": file_size,
            "download_url": download_url,
            "fs_id": int(file_info.get("fs_id", 0)),
            "thumb_url": file_info.get("thumb_url", ""),
        }
    
# if __name__ == "__main__":
#     data = get_video_metadata("https://1024terabox.com/s/1gvhn4oF65BbRvrA_fSsuWA")

#     with open("example_response.json", "w") as f:
#         json.dump(data, f, indent=2)
