"""
StreamTape resolver — video hosting with JS-obfuscated download links.

Flow:
  1. GET page → extract JS code that builds download URL
  2. Parse the URL construction pattern (usually string concat or array join)
  3. Return direct download URL

No auth needed. No CAPTCHA.
"""
import re
import logging
import requests
from network import get_session

logger = logging.getLogger(__name__)

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0',
}


class StreamTapeError(Exception):
    """Base exception for StreamTape resolver."""


class StreamTapeNotFound(StreamTapeError):
    """Video not found or deleted."""


# https://streamtape.com/v/<code> or /e/<code> or /video/<code> or /get/<code>
_STREAMTAPE_RE = re.compile(
    r'https?://(?:www\.)?(?:streamtape\.com|sowhy\.xyz|sstrema\.com|streamta\.pe|'
    r'streamtape\.cc|streamtape\.to|streamtape\.xyz|tape\.noobloli\.buzz|'
    r'streamtaped\.com|tapewood\.ch|turbovio\.com)'
    r'/(?:v|e|video|get|embed)/([A-Za-z0-9]+)',
    re.I,
)

# URL patterns in page source
_DL_URL_RE = re.compile(r'(?:href|src|url)\s*[:=]\s*["\']?(https?://[^"\'>\s]+(?:/download|/stream|\.mp4|\.mp3)[^"\']*)', re.I)

# StreamTape uses string concatenation:  "/get_video?id=..." + "xxx" + "&..."`
# Pattern: var C = ["...","...",...] then C.join("") or similar
_JS_ARRAY_RE = re.compile(r'var\s+\w+\s*=\s*\[(["\'][^"\']+["\'](?:\s*,\s*["\'][^"\']+["\'])*)\]')
_JS_CONCAT_RE = re.compile(r'["\']([^"\']+)["\'](?:\s*\+\s*["\']([^"\']+)["\'])+')
_NOTHING_RE = re.compile(r'streamtape\.com/nothing', re.I)

# The characteristic StreamTape download pattern
_ST_DL_RE = re.compile(
    r'(?:href|src)=["\']?'
    r'((?:https?://[^"\'>\s]*streamtape[^"\'>\s]*)'
    r'/(?:get_video|download|stream)'
    r'[^"\'>\s]*)',
    re.I,
)

# Video ID from page
_VID_RE = re.compile(r'(?:video_id|vid|file_id|v)\s*[=:]\s*["\']([A-Za-z0-9]+)["\']', re.I)

# The actual StreamTape JS decode: there's usually a hidden div with an iframe or script
# that builds the URL from pieces. The most reliable approach is to find the
# /get_video?id=XXXX pattern in the page source.
_GET_VIDEO_RE = re.compile(r'/get_video\?id=([A-Za-z0-9]+)(?:&[^"\'>\s]*)?', re.I)
_IFRAME_SRC_RE = re.compile(r'<iframe[^>]*src=["\']?(https?://[^"\'>\s]+)["\']?', re.I)


def is_streamtape_url(url: str) -> bool:
    return bool(_STREAMTAPE_RE.search(url))


def extract_streamtape_url(text: str) -> list[str]:
    urls = []
    seen = set()
    for m in _STREAMTAPE_RE.finditer(text):
        u = m.group(0)
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def _extract_url_from_js(html: str, page_url: str) -> str | None:
    """Try to extract the download URL from obfuscated JS in the page."""

    # Method 1: Direct /get_video link in page
    m = _GET_VIDEO_RE.search(html)
    if m:
        vid = m.group(1)
        # Build the full URL
        parsed = requests.compat.urlparse(page_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        return f"{base}/get_video?id={vid}"

    # Method 2: iframe src pointing to streamtape
    for m in _IFRAME_SRC_RE.finditer(html):
        iframe_url = m.group(1)
        parsed_iframe = requests.compat.urlparse(iframe_url)
        # AND semantics: the old `A or not B` was effectively always-true and
        # fetched arbitrary attacker-supplied URLs (SSRF probing vector).
        # Only same-brand http(s) iframes are followed, via pooled session.
        if (
            parsed_iframe.scheme in ("http", "https")
            and "streamtape" in parsed_iframe.netloc
        ):
            # Fetch the iframe page to find the real URL
            try:
                r = get_session().get(iframe_url, headers={
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131',
                    'Referer': page_url,
                }, timeout=15)
                inner = r.text
                inner_m = _GET_VIDEO_RE.search(inner)
                if inner_m:
                    vid = inner_m.group(1)
                    parsed = requests.compat.urlparse(iframe_url)
                    base = f"{parsed.scheme}://{parsed.netloc}"
                    return f"{base}/get_video?id={vid}"
            except Exception:
                pass

    # Method 3: Reconstruct from JS string concatenation
    # StreamTape obfuscates like: var link = "/get_video" + "?id=" + "xxx" + "&token=..."
    # Look for concatenation chains that form a URL
    concat_pattern = re.compile(
        r'(?:"/get_video\?id="|"/get_video\?id=\\\\x69\\\\x64\\\\x3d")'
        r'(\s*\+\s*["\'][^"\']+["\'])+',
        re.I,
    )
    m = concat_pattern.search(html)
    if m:
        chain = m.group(0)
        # Extract all string literals
        parts = re.findall(r'["\']([^"\']+)["\']', chain)
        url = ''.join(parts)
        if url.startswith('/'):
            parsed = requests.compat.urlparse(page_url)
            url = f"{parsed.scheme}://{parsed.netloc}{url}"
        return url

    # Method 4: Look for the character-level decode pattern
    # streamtape.com/v/XXXX → page has script that decodes to /get_video?id=...
    # Most reliable fallback: just look for any URL with /get_video or /download
    for regex in (_ST_DL_RE, _DL_URL_RE):
        m = regex.search(html)
        if m:
            link = m.group(1)
            if link.startswith('/'):
                parsed = requests.compat.urlparse(page_url)
                link = f"{parsed.scheme}://{parsed.netloc}{link}"
            return link

    return None


def resolve_streamtape(url: str, session: requests.Session | None = None) -> dict:
    """
    Resolve a StreamTape URL.
    Returns: {"filename": str, "size": int, "download_url": str}
    """
    sess = session or get_session()

    # Extract video ID from URL
    m = _STREAMTAPE_RE.search(url)
    if not m:
        raise StreamTapeError(f"Not a StreamTape URL: {url}")
    video_id = m.group(1)

    # Fetch the page
    try:
        resp = sess.get(url, headers=_HEADERS, timeout=20, allow_redirects=True)
    except requests.RequestException as e:
        raise StreamTapeError(f"Failed to fetch page: {e}") from e

    if resp.status_code == 404 or 'not found' in resp.text.lower()[:500]:
        raise StreamTapeNotFound(f"Video not found: {url}")

    html = resp.text

    # Try to extract download URL
    download_url = _extract_url_from_js(html, resp.url)
    if not download_url:
        raise StreamTapeError(f"Could not extract download URL from StreamTape page: {url}")

    # Filename is usually not in the page — use video_id
    filename = f"streamtape_{video_id}.mp4"

    # Size unknown — will be determined during download
    return {"filename": filename, "size": 0, "download_url": download_url}
