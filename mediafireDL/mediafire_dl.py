"""
MediaFire resolver — classic file hosting.

Flow:
  1. GET page → extract download link from HTML
  2. Handle "slow download" page if needed (POST with token)
  3. Return direct download URL

No auth needed for public files.
"""
import re
import logging
import requests
from network import get_session

logger = logging.getLogger(__name__)

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}


class MediaFireError(Exception):
    """Base exception for MediaFire resolver."""


class MediaFireNotFound(MediaFireError):
    """File not found or removed."""


_MEDIAFIRE_RE = re.compile(
    r'https?://(?:www\.)?mediafire\.com/(?:file|view)/([A-Za-z0-9]+)',
    re.I,
)

_MEDIAFIRE_DL_RE = re.compile(
    r'href=["\']?(https?://download\d*\.mediafire\.com/[^"\'>\s]+)["\']?',
    re.I,
)

_TITLE_RE = re.compile(r'<title>\s*(.+?)\s*(?:\||-)', re.I)
_SIZE_RE = re.compile(r'Size\s*:\s*([\d.,]+)\s*(KB|MB|GB|TB|bytes)', re.I)


def is_mediafire_url(url: str) -> bool:
    return bool(_MEDIAFIRE_RE.search(url))


def extract_mediafire_url(text: str) -> list[str]:
    urls = []
    seen = set()
    for m in _MEDIAFIRE_RE.finditer(text):
        u = m.group(0)
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def resolve_mediafire(url: str, session: requests.Session | None = None) -> dict:
    """
    Resolve a MediaFire URL.
    Returns: {"filename": str, "size": int, "download_url": str}
    """
    sess = session or get_session()

    try:
        resp = sess.get(url, headers=_HEADERS, timeout=20, allow_redirects=True)
    except requests.RequestException as e:
        raise MediaFireError(f"Failed to fetch page: {e}") from e

    if resp.status_code == 404 or 'File Removed' in resp.text:
        raise MediaFireNotFound(f"File not found or removed: {url}")

    html = resp.text

    # Extract download link
    m = _MEDIAFIRE_DL_RE.search(html)
    if m:
        dl_url = m.group(1)
        fname_m = _TITLE_RE.search(html)
        fname = fname_m.group(1).strip() if fname_m else "mediafire_download"
        size_m = _SIZE_RE.search(html)
        size = 0
        if size_m:
            val = float(size_m.group(1).replace(',', ''))
            unit = size_m.group(2).upper()
            multipliers = {'B': 1, 'BYTES': 1, 'KB': 1024, 'MB': 1024**2, 'GB': 1024**3, 'TB': 1024**4}
            size = int(val * multipliers.get(unit, 1))
        return {"filename": fname, "size": size, "download_url": dl_url}

    # Sometimes there's a JS redirect
    js_m = re.search(r'location\s*=\s*["\']?(https?://download[^"\'>\s]+)', html, re.I)
    if js_m:
        return {"filename": "mediafire_download", "size": 0, "download_url": js_m.group(1)}

    raise MediaFireError(f"Could not extract download link from MediaFire: {url}")
