"""
FileLions (filelions.to/filelions.me) resolver — video hosting with API-like patterns.

Flow:
  1. GET page → extract token/code from JS
  2. POST or GET the download endpoint
  3. Return direct download URL

No auth needed.
"""
import re
import logging
import requests
from network import get_session
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0',
}


class FileLionsError(Exception):
    """Base exception for FileLions resolver."""


class FileLionsNotFound(FileLionsError):
    """Video not found or deleted."""


_FILELIONS_RE = re.compile(
    r'https?://(?:[\w.-]+\.)?(?:filelions\.[a-z]{2,}|filelion\.[a-z]{2,}|lionfile\.[a-z]{2,}|'
    r'ahvsh\.[a-z]{2,}|streamhide\.[a-z]{2,}|wolfstream\.[a-z]{2,}|wootly\.[a-z]{2,}|letsupload\.[a-z]{2,})'
    r'/(?:v|e|embed|f|d)/([A-Za-z0-9_-]+)',
    re.I,
)

_FL_DOMAIN_RE = re.compile(
    r'https?://(?:www\.)?(?:filelions\.to|filelions\.me|filelions\.ai|filelions\.dev)',
    re.I,
)

# Download URL patterns
_FL_DL_RE = re.compile(
    r'(?:href|src)=["\']?(https?://[^"\'>\s]*(?:\.mp4|\.m3u8|/download|/get_video|/stream)[^"\'>\s]*)',
    re.I,
)

_TITLE_RE = re.compile(r'<title>\s*(.+?)\s*(?:\||-|—)', re.I)


def is_filelions_url(url: str) -> bool:
    return bool(_FILELIONS_RE.search(url) or _FL_DOMAIN_RE.search(url))


def extract_filelions_url(text: str) -> list[str]:
    urls = []
    seen = set()
    for m in _FILELIONS_RE.finditer(text):
        u = m.group(0)
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def resolve_filelions(url: str, session: requests.Session | None = None) -> dict:
    """
    Resolve a FileLions URL.
    Returns: {"filename": str, "size": int, "download_url": str, "headers": dict}
    """
    sess = session or get_session()

    try:
        resp = sess.get(url, headers=_HEADERS, timeout=20, allow_redirects=True)
    except requests.RequestException as e:
        raise FileLionsError(f"Failed to fetch page: {e}") from e

    if resp.status_code == 404:
        raise FileLionsNotFound(f"Video not found: {url}")

    html = resp.text

    # Try direct download link
    m = _FL_DL_RE.search(html)
    if m:
        dl_url = m.group(1)
        if not dl_url.startswith('http'):
            dl_url = urljoin(resp.url, dl_url)
        fname_m = _TITLE_RE.search(html)
        fname = fname_m.group(1).strip() if fname_m else "filelions_video"
        return {"filename": fname, "size": 0, "download_url": dl_url, "headers": {'Referer': resp.url}}

    # Look for any video source in script tags
    src_m = re.search(r'source\s*:\s*["\']([^"\']+(?:\.mp4|m3u8)[^"\']*)["\']', html, re.I)
    if src_m:
        dl_url = urljoin(resp.url, src_m.group(1))
        fname_m = _TITLE_RE.search(html)
        fname = fname_m.group(1).strip() if fname_m else "filelions_video"
        return {"filename": fname, "size": 0, "download_url": dl_url, "headers": {'Referer': resp.url}}

    raise FileLionsError(f"Could not extract download URL from FileLions: {url}")
