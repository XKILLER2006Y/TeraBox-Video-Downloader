"""
flareDL/flare_dl.py
~~~~~~~~~~~~~~~~~~~
Core extractor and downloader for Flare / CashSnap / HugeBox links.

Flow:
1. Extract share_id from URL (e.g., https://flareobhx.com/s/2092601086832676866).
2. Fetch file metadata via POST https://api.cshsnpcwio.com/v1/h5_open_data.
3. Fetch encrypted stream token via POST https://api.cshsnpcwio.com/v1/h5/download_file_url.
4. Decrypt stream URL with AES-256-CBC (Key: CMrhmcd9oFUjWBBleiMfS0BiBfupaVsG, IV: 2Xk4dLo38c9Z2Q2a).
5. Download and remux stream into MP4 via stream_downloader.
"""

import base64
import logging
import os
import re
import threading
from typing import Any, Callable, Dict, List, Optional
import requests
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from flareDL.errors import FlareDirectError, FlareError
from teraboxDL.stream_downloader import download_from_stream_url

log = logging.getLogger(__name__)

# Pattern matching Flare / CashSnap / HugeBox share URLs
FLARE_URL_RE = re.compile(
    r"https?://(?:[\w.-]+\.)?(?:flare[\w.-]*|hugebox[\w.-]*|cshsnp[\w.-]*)/s/([a-zA-Z0-9_-]+)",
    re.IGNORECASE,
)

# API constants
API_BASE = "https://api.cshsnpcwio.com"
AES_KEY = b"CMrhmcd9oFUjWBBleiMfS0BiBfupaVsG"
AES_IV = b"2Xk4dLo38c9Z2Q2a"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Origin": "https://www.flarekkox.com",
    "Referer": "https://www.flarekkox.com/",
    "Content-Type": "application/json",
}


def extract_flare_id(url: str) -> Optional[str]:
    """Extract share ID from a Flare/CashSnap URL."""
    match = FLARE_URL_RE.search(url)
    return match.group(1) if match else None


def extract_all_flare_urls(text: str) -> List[str]:
    """Find all Flare/CashSnap URLs inside a text block."""
    if not text:
        return []
    return [m.group(0) for m in FLARE_URL_RE.finditer(text)]


def decrypt_flare_stream_url(ciphertext_b64: str) -> str:
    """
    Decrypt the stream URL returned by /v1/h5/download_file_url using AES-256-CBC.
    """
    try:
        raw_ct = base64.b64decode(ciphertext_b64.strip())
        cipher = Cipher(algorithms.AES(AES_KEY), modes.CBC(AES_IV), backend=default_backend())
        decryptor = cipher.decryptor()
        pt = decryptor.update(raw_ct) + decryptor.finalize()
        pad_len = pt[-1]
        if 1 <= pad_len <= 16:
            pt = pt[:-pad_len]
        return pt.decode("utf-8").strip()
    except Exception as e:
        log.error(f"Failed to decrypt Flare stream token: {e}")
        raise FlareError(f"Failed to decrypt Flare stream URL: {e}") from e


def get_flare_info(url: str) -> Dict[str, Any]:
    """
    Fetch file metadata and decrypted stream URL for a Flare share link.
    """
    share_id = extract_flare_id(url)
    if not share_id:
        raise FlareError(f"Invalid Flare URL: {url}")

    # 1. Fetch file metadata
    open_data_payload = {
        "uid": "",
        "dir_id": "",
        "link_id": share_id,
        "open_link": True,
        "page_size": 50,
        "current_page": 1,
    }

    try:
        r = requests.post(
            f"{API_BASE}/v1/h5_open_data",
            json=open_data_payload,
            headers=_HEADERS,
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.error(f"Failed to connect to Flare API: {e}")
        raise FlareError(f"Could not reach Flare API: {e}") from e

    # Check for link status messages
    msg = data.get("msg")
    if msg in ("REMOVED", "NO_DATA", "LINK_EXPIRE", "CONTENT_VIOLATE"):
        reasons = {
            "REMOVED": "This Flare link has been removed or deleted.",
            "NO_DATA": "No data found for this Flare link.",
            "LINK_EXPIRE": "This Flare link has expired.",
            "CONTENT_VIOLATE": "This content is unavailable due to policy violation.",
        }
        raise FlareDirectError(reasons.get(msg, f"Flare link unavailable ({msg})"))

    files = data.get("files") or []
    if not files:
        raise FlareDirectError("No downloadable files found in this Flare link.")

    file_entry = files[0]
    file_id = file_entry.get("file_id") or file_entry.get("id")
    file_meta = file_entry.get("file_meta") or {}
    user_info = data.get("user") or {}
    uid = user_info.get("id", "")

    filename = file_meta.get("display_name") or f"flare_{share_id}.mp4"
    if not filename.lower().endswith((".mp4", ".mkv", ".webm")):
        filename += ".mp4"

    size = file_meta.get("size") or 0
    thumb_url = file_meta.get("thumbnail") or ""

    # 2. Fetch encrypted download URL
    if not uid or not file_id:
        raise FlareError("Missing user ID or file ID from Flare metadata.")

    dl_payload = {
        "uid": str(uid),
        "file_id": str(file_id),
    }

    try:
        dl_resp = requests.post(
            f"{API_BASE}/v1/h5/download_file_url",
            json=dl_payload,
            headers=_HEADERS,
            timeout=15,
        )
        dl_resp.raise_for_status()
        encrypted_token = dl_resp.text.strip().strip('"')
    except Exception as e:
        log.error(f"Failed to fetch Flare download token: {e}")
        raise FlareError(f"Failed to fetch Flare download URL: {e}") from e

    # 3. Decrypt the streaming/download URL
    stream_url = decrypt_flare_stream_url(encrypted_token)
    if not stream_url or not stream_url.startswith("http"):
        raise FlareError(f"Decrypted Flare URL is invalid: {stream_url}")

    return {
        "download_url": stream_url,
        "filename": filename,
        "size": size,
        "thumb_url": thumb_url,
        "share_id": share_id,
        "file_id": file_id,
    }


def download_flare_file(
    download_url: str,
    output_path: str,
    cancel_event: Optional[threading.Event] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> str:
    """
    Download and remux a Flare stream into the target MP4 file path.
    """
    return download_from_stream_url(
        stream_url=download_url,
        output_file=output_path,
        cancel_event=cancel_event,
        progress_callback=progress_callback,
    )
