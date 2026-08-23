import os
import re
import time
import random
import logging
import requests
from urllib.parse import unquote, urlparse, urlunparse, urlencode, parse_qs

from network import get_session, USER_AGENTS

log = logging.getLogger(__name__)


class TeraBoxDirectError(Exception):
    """Raised when TeraBox direct resolution fails with a known error."""
    pass


# ── TeraBox Error Codes ──────────────────────────────────────────────────────
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

# ── Config ────────────────────────────────────────────────────────────────────
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
    raise Exception(f"Could not extract surl from URL: {terabox_url}")


def _load_session(cookies_str: str = ""):
    """Return the global session. Cookies are passed via headers, not mutated."""
    return get_session()


def _validate_cookies(session: requests.Session, cookies_str: str = "") -> bool:
    """
    Validate that cookies are still working by making a test request.
    
    Returns True if cookies are valid, False otherwise.
    """
    if not cookies_str:
        # Try to extract from session
        cookie_str = "; ".join(
            f"{c.name}={c.value}" for c in session.cookies
            if "1024tera" in (c.domain or "")
        )
        if not cookie_str:
            return True  # No cookies to validate
        cookies_str = cookie_str
    
    # Test request to check if cookies are valid
    try:
        test_url = f"{BASE_URL}/api/user/info"
        headers = _headers(session, "", cookies_str)
        resp = session.get(test_url, headers=headers, timeout=10)
        
        # Check if response indicates valid session
        if resp.status_code == 200:
            data = resp.json()
            # If errno is 0, cookies are valid
            return data.get("errno", -1) == 0
        return False
    except Exception as e:
        log.warning(f"Cookie validation failed: {e}")
        return False


def _get_valid_cookies() -> str:
    """
    Get valid cookies from environment, checking all COOKIES1..N variables.
    
    Returns the first valid cookie string, or empty string if none found.
    """
    import os
    
    # Try COOKIES1 through COOKIES5
    for i in range(1, 6):
        cookies_str = os.getenv(f"COOKIES{i}", "")
        if cookies_str:
            session = get_session()
            if _validate_cookies(session, cookies_str):
                log.info(f"Using valid cookies from COOKIES{i}")
                return cookies_str
            else:
                log.warning(f"COOKIES{i} is invalid or expired")
    
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

    # Rate limit detection (HTTP 429)
    if resp.status_code == 429:
        raise TeraBoxDirectError("Rate limited by TeraBox. Wait a few minutes and try again.")

    # Geo-block detection (HTTP 403)
    if resp.status_code == 403:
        raise TeraBoxDirectError("Access denied by TeraBox. This content may be geo-blocked in your region.")

    # Server error
    if resp.status_code >= 500:
        raise TeraBoxDirectError(f"TeraBox server error (HTTP {resp.status_code}). Try again later.")

    try:
        data = resp.json()
    except ValueError:
        raise TeraBoxDirectError("Invalid response from TeraBox. The service may be temporarily unavailable.")

    errno = data.get("errno", 0)
    if errno != 0:
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


def _discover_all_hls_chunks(session, shareid, uk, sign, timestamp, fs_id, surl, cookies_str="") -> str:
    """
    Poll the TeraBox streaming endpoint to collect all HLS chunks,
    then build an M3U8 playlist and return it as a string (not a file).
    
    Returns the M3U8 manifest text directly — avoids disk write/read.
    Uses request collapsing to avoid duplicate requests.
    """
    quality = "M3U8_AUTO_1080"
    known = {}
    req_count = 0
    max_known_idx = -1
    no_new_max_streak = 0
    max_retries = 100
    deadline = time.monotonic() + 120  # 2-minute hard limit
    
    # Request collapsing: cache responses to avoid duplicate requests
    _response_cache = {}
    _cache_ttl = 0.5  # Cache valid for 0.5 seconds
    
    while req_count < max_retries and time.monotonic() < deadline:
        req_count += 1
        url = _build_streaming_url(shareid, uk, sign, timestamp, fs_id, quality)
        
        # Request collapsing: check if we recently fetched this URL
        now = time.time()
        cache_key = url
        if cache_key in _response_cache:
            cached_time, cached_text = _response_cache[cache_key]
            if now - cached_time < _cache_ttl:
                log.debug(f"HLS chunk discovery: using cached response for request {req_count}")
                text = cached_text
            else:
                try:
                    text = session.get(url, headers=_headers(session, surl, cookies_str), timeout=60).text.strip()
                    _response_cache[cache_key] = (now, text)
                except Exception as e:
                    log.warning(f"HLS chunk discovery: error on request {req_count}: {e}")
                    time.sleep(0.3)
                    continue
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
            _response_cache = {k: v for k, v in _response_cache.items() if now - v[0] < _cache_ttl}
        
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
        
        # Check if complete (contiguous range starting near 0)
        if known and min(known) <= 1 and len(known) == max(known) - min(known) + 1:
            confidence = max(10, max_known_idx)
            if no_new_max_streak >= confidence:
                break
        
        budget = min(100, max(30, max_known_idx * 3))
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


def _get_video_metadata(terabox_url: str) -> dict:
    """
    Resolve TeraBox metadata directly (no proxy needed).
    
    For non-HD mode: discovers all HLS chunks and returns M3U8 manifest text.
    For HD mode: returns the direct download link.
    """
    surl = _extract_surl(terabox_url)
    log.info(f"Resolving TeraBox metadata for surl={surl}")
    
    session = _load_session()
    
    # Get valid cookies with auto-rotation
    cookies_str = _get_valid_cookies()
    
    # Step 1: Get jsToken from share page
    js_token = _get_js_token(session, surl, cookies_str)
    log.info(f"Got jsToken: {js_token[:16]}...")
    
    # Step 2: Get share info (file metadata)
    info = _get_share_info(session, js_token, surl, cookies_str)
    files = info.get("list", [])
    if not files:
        raise TeraBoxDirectError("No files found in this share. The link may be expired or the files were deleted.")
    
    shareid = info["shareid"]
    uk = info["uk"]
    sign = info["sign"]
    timestamp = info["timestamp"]
    fs_id = files[0]["fs_id"]
    filename = files[0].get("server_filename", "unknown")
    
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
    
    # Step 3: Build streaming URL and discover all HLS chunks
    m3u8_text = _discover_all_hls_chunks(session, shareid, uk, sign, timestamp, fs_id, surl, cookies_str)
    
    # Return metadata with M3U8 text (no file path — avoids disk I/O)
    return {
        "list": [{
            "server_filename": files[0].get("server_filename", "video.mp4"),
            "size": int(files[0].get("size", 0)),
            "stream_url": m3u8_text,  # M3U8 manifest text (not a file path)
            "direct_link": "",  # HD direct link (not available without premium)
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

def get_video_info(terabox_url: str, is_hd: bool) -> dict:
    try:
        data = _get_video_metadata(terabox_url)
    except TeraBoxDirectError:
        raise  # already has user-friendly message
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
        }
    
# if __name__ == "__main__":
#     data = get_video_metadata("https://1024terabox.com/s/1gvhn4oF65BbRvrA_fSsuWA")

#     with open("example_response.json", "w") as f:
#         json.dump(data, f, indent=2)