"""
CatBox (catbox.moe) resolver — simple direct download links.

CatBox files are served directly — no auth, no timers, no JS.
URLs are already download links:
  https://files.catbox.moe/<filename>
  https://catbox.moe/user/<id>/<filename>

Flow:
  1. Validate URL
  2. HEAD request to get filename + size
  3. Return URL directly
"""
import re
import logging
import requests
from network import get_session

logger = logging.getLogger(__name__)

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131',
}


class CatBoxError(Exception):
    """Base exception for CatBox resolver."""


# https://files.catbox.moe/<filename> or https://litterbox.catbox.moe/... or https://catbox.moe/user/...
_CATBOX_RE = re.compile(
    r'https?://(?:(?:files|litterbox|litter)\.catbox\.moe|catbox\.moe/(?:user|c))/[^\s"\']+',
    re.I,
)


def is_catbox_url(url: str) -> bool:
    return bool(_CATBOX_RE.search(url))


def extract_catbox_url(text: str) -> list[str]:
    urls = []
    seen = set()
    for m in _CATBOX_RE.finditer(text):
        u = m.group(0)
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def resolve_catbox(url: str, session: requests.Session | None = None) -> dict:
    """
    Resolve a CatBox URL.
    Returns: {"filename": str, "size": int, "download_url": str}
    """
    sess = session or get_session()

    # HEAD to get metadata
    try:
        resp = sess.head(url, headers=_HEADERS, timeout=15, allow_redirects=True)
    except requests.RequestException as e:
        raise CatBoxError(f"Failed to check CatBox URL: {e}") from e

    if resp.status_code == 404:
        raise CatBoxError(f"File not found: {url}")

    ct = resp.headers.get('Content-Type', '')
    if 'text/html' in ct:
        raise CatBoxError(f"URL returns HTML, not a file: {url}")

    # Extract filename from URL
    filename = url.rstrip('/').split('/')[-1] or 'catbox_download'
    size = int(resp.headers.get('Content-Length', 0))

    return {"filename": filename, "size": size, "download_url": url}
