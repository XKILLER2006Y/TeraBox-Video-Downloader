import html
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional
import requests

from .errors import FlezenDirectError, FlezenError

log = logging.getLogger(__name__)

FLEZEN_URL_PATTERN = re.compile(
    r"https?://(?:[\w.-]+\.)?flezen\.[a-z]{2,}/(?:s|share|f|v|d)/([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://flezen.com/",
}


def _get_flezen_session() -> Optional[requests.Session]:
    """
    Return a requests.Session pre-configured with the Flezen account cookie if available.
    """
    cookie = os.getenv("FLEZEN_COOKIE") or os.getenv("FLEZEN_ACCOUNT_COOKIE")
    if not cookie:
        return None

    session = requests.Session()
    session.headers.update(_HEADERS)
    session.headers["Cookie"] = cookie.strip()
    return session


def extract_flezen_id(url: str) -> Optional[str]:
    """
    Extract the Flezen share ID from a URL.
    """
    match = FLEZEN_URL_PATTERN.search(url)
    if match:
        return match.group(1)
    return None


def extract_all_flezen_urls(text: str) -> List[str]:
    """
    Extract all Flezen URLs from an arbitrary block of text.
    """
    if not text:
        return []
    matches = FLEZEN_URL_PATTERN.findall(text)
    urls = []
    for share_id in matches:
        full_url = f"https://flezen.com/s/{share_id}"
        if full_url not in urls:
            urls.append(full_url)
    return urls


def _try_save_and_resolve_stream(share_id: str, session: requests.Session) -> Optional[str]:
    """
    Save file to logged-in user account via /user/save?id=<share_id>
    and retrieve the direct download/stream link from the dashboard.
    """
    try:
        save_url = f"https://flezen.com/user/save?id={share_id}"
        r = session.get(save_url, allow_redirects=True, timeout=15)
        if r.status_code == 200:
            files_page = session.get("https://flezen.com/user/files", timeout=15)
            if files_page.status_code == 200:
                m = re.search(r"href=['\"](https?://[^'\"]*(?:download|stream|file)[^'\"]*)['\"]", files_page.text)
                if m:
                    return m.group(1)
    except Exception as e:
        log.warning(f"Failed to auto-save file {share_id} to Flezen account: {e}")
    return None


def get_flezen_info(url: str) -> Dict[str, Any]:
    """
    Fetch file metadata for a Flezen share link.
    """
    share_id = extract_flezen_id(url)
    if not share_id:
        raise FlezenError(f"Invalid Flezen URL: {url}")

    page_url = f"https://flezen.com/s/{share_id}"

    session = _get_flezen_session() or requests.Session()
    session.headers.update(_HEADERS)

    try:
        r = session.get(page_url, timeout=15)
    except Exception as e:
        log.error(f"Failed to connect to Flezen: {e}")
        raise FlezenError(f"Could not reach Flezen: {e}") from e

    if r.status_code == 404:
        raise FlezenDirectError("This Flezen link does not exist or has been deleted by the uploader.")

    if r.status_code != 200:
        raise FlezenError(f"Flezen returned HTTP {r.status_code}")

    page_html = r.text

    # Extract Title / Filename
    title_match = re.search(r"<h1[^>]*>(.*?)</h1>", page_html, re.DOTALL)
    if not title_match:
        # Fallback to File Information section
        title_match = re.search(
            r'<p[^>]*class=["\'][^"\']*text-gray-600 break-all[^"\']*["\'][^>]*>(.*?)</p>',
            page_html,
            re.DOTALL,
        )

    # Extract File Size
    bytes_match = re.search(r'data-bytes=["\'](\d+)["\']', page_html)
    size = int(bytes_match.group(1)) if bytes_match else 0

    if (not title_match and not bytes_match) or (bytes_match is None and not size):
        raise FlezenDirectError("This Flezen link does not exist or has been deleted by the uploader.")

    if title_match:
        raw_title = title_match.group(1).strip()
        raw_title = re.sub(r"<[^>]+>", "", raw_title)
        filename = html.unescape(raw_title).strip()
    else:
        filename = f"flezen_{share_id}.mp4"

    if "can't find this file" in filename.lower() or "file not found" in filename.lower() or (size == 0 and not bytes_match):
        raise FlezenDirectError("This Flezen link does not exist or has been deleted by the uploader.")

    if not filename.lower().endswith((".mp4", ".mkv", ".webm", ".mov", ".avi", ".zip", ".rar")):
        filename += ".mp4"

    # Extract Upload Date
    datetime_match = re.search(r'data-datetime=["\']([^"\']+)["\']', page_html)
    upload_date = datetime_match.group(1).strip() if datetime_match else ""

    # Extract Views
    views_match = re.search(r'<i class=["\']ri-eye-line["\'][^>]*>.*?<p class=["\']text-gray-600["\']>(\d+)</p>', page_html, re.DOTALL)
    views = int(views_match.group(1)) if views_match else 0

    # If an authenticated session is active, try to resolve download link
    download_url = None
    if _get_flezen_session():
        download_url = _try_save_and_resolve_stream(share_id, session)

    return {
        "share_id": share_id,
        "filename": filename,
        "size": size,
        "upload_date": upload_date,
        "views": views,
        "url": page_url,
        "download_url": download_url,
    }


def download_flezen_file(
    info: Dict[str, Any],
    output_path: str,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> str:
    """
    Download Flezen file stream.
    """
    download_url = info.get("download_url")
    if not download_url:
        raise FlezenDirectError(
            "Flezen protected file streaming requires FLEZEN_COOKIE in bot config.\n"
            "File details: " + info.get("filename", "Unknown") + " (" + str(info.get("size", 0)) + " bytes)"
        )

    from teraboxDL.stream_downloader import download_from_stream_url
    return download_from_stream_url(download_url, output_path, progress_callback)
