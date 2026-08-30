"""
universalDL/pixeldrainDL.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~
Direct metadata & download resolver for Pixeldrain files.
"""

import re
import logging
import requests
from typing import Optional, List

log = logging.getLogger(__name__)

PIXELDRAIN_URL_RE = re.compile(
    r"https?://(?:[\w.-]+\.)?pixeldrain\.com/(?:u|api/file)/([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)


class PixeldrainError(Exception):
    pass


def is_pixeldrain_url(url: str) -> bool:
    return bool(PIXELDRAIN_URL_RE.search(url or ""))


def extract_pixeldrain_url(text: str) -> List[str]:
    if not text:
        return []
    return [m.group(0) for m in PIXELDRAIN_URL_RE.finditer(text)]


def extract_pixeldrain_id(url: str) -> Optional[str]:
    m = PIXELDRAIN_URL_RE.search(url or "")
    return m.group(1) if m else None


def resolve_pixeldrain(url: str, session: Optional[requests.Session] = None) -> dict:
    file_id = extract_pixeldrain_id(url)
    if not file_id:
        raise PixeldrainError(f"Invalid Pixeldrain link: {url}")

    sess = session or requests.Session()
    info_api = f"https://pixeldrain.com/api/file/{file_id}/info"
    dl_url = f"https://pixeldrain.com/api/file/{file_id}?download=1"

    try:
        r = sess.get(info_api, timeout=15)
        if r.status_code == 404:
            raise PixeldrainError("Pixeldrain file not found or deleted.")
        r.raise_for_status()
        data = r.json()
        filename = data.get("name") or f"pixeldrain_{file_id}.mp4"
        size = int(data.get("size", 0))
    except PixeldrainError:
        raise
    except Exception as e:
        log.warning(f"Pixeldrain info API failed ({e}), using HEAD fallback")
        filename = f"pixeldrain_{file_id}.mp4"
        size = 0

    return {
        "filename": filename,
        "size": size,
        "download_url": dl_url,
    }
