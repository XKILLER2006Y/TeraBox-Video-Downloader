"""
GoFile.io resolver — free file hosting with clean API.

Flow:
  1. GET api.gofile.io/servers → pick best server
  2. GET <server>.gofile.io/contents/<contentId> → file metadata + download URL
  3. Download directly

No auth required for public content.
"""
import re
import logging
import requests
from network import get_session

logger = logging.getLogger(__name__)


class GoFileError(Exception):
    """Base exception for GoFile resolver."""


class GoFileNotFound(GoFileError):
    """Content not found or deleted."""


# https://gofile.io/d/<code> or https://gofile.io/download/...
_GOFILE_RE = re.compile(r'https?://(?:www\.)?gofile\.io/(?:download|d)/([A-Za-z0-9]+)', re.I)


def is_gofile_url(url: str) -> bool:
    return bool(_GOFILE_RE.search(url))


def extract_gofile_url(text: str) -> list[str]:
    urls = []
    seen = set()
    for m in _GOFILE_RE.finditer(text):
        u = m.group(0)
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def _get_content_id(url: str) -> str:
    m = _GOFILE_RE.search(url)
    if not m:
        raise GoFileError(f"Not a GoFile URL: {url}")
    return m.group(1)


def resolve_gofile(url: str, session: requests.Session | None = None) -> dict:
    """
    Resolve a GoFile URL.
    Returns: {"filename": str, "size": int, "download_url": str}
    """
    sess = session or get_session()
    sess.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0',
    })

    content_id = _get_content_id(url)

    # Step 1: Get best server
    try:
        srv_resp = sess.get('https://api.gofile.io/servers', timeout=15)
        srv_data = srv_resp.json()
        if srv_data.get('status') != 'ok':
            raise GoFileError(f"GoFile server list failed: {srv_data}")
        server = srv_data['data']['servers'][0]['name']
    except (requests.RequestException, KeyError, IndexError) as e:
        raise GoFileError(f"Failed to get GoFile server: {e}") from e

    # Step 2: Get content info (no token needed for public content)
    try:
        content_url = f'https://{server}.gofile.io/contents/{content_id}'
        resp = sess.get(content_url, timeout=20)
        data = resp.json()
    except requests.RequestException as e:
        raise GoFileError(f"GoFile content fetch failed: {e}") from e
    except ValueError:
        raise GoFileError("GoFile returned non-JSON response")

    if data.get('status') != 'ok':
        status = data.get('status', '')
        if 'not-found' in status or 'error-not-found' in status:
            raise GoFileNotFound(f"GoFile content not found: {url}")
        raise GoFileError(f"GoFile error: {data}")

    # Step 3: Extract file info
    contents = data.get('data', {}).get('contents', {})
    if not contents:
        raise GoFileNotFound(f"No files found in GoFile content: {url}")

    # Pick the first (or largest) file
    files = list(contents.values())
    # Prefer video files
    video_exts = {'.mp4', '.mkv', '.avi', '.mov', '.webm', '.flv', '.wmv'}
    files.sort(key=lambda f: 1 if any(f.get('name', '').lower().endswith(e) for e in video_exts) else 0, reverse=True)

    best = files[0]
    filename = best.get('name', 'download')
    size = best.get('size', 0)
    download_url = best.get('link', '')

    if not download_url:
        raise GoFileError("No download URL in GoFile response")

    return {"filename": filename, "size": size, "download_url": download_url}
