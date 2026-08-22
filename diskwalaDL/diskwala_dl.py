"""
diskwalaDL/diskwala_dl.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Direct Diskwala resolver — bypasses the scraper proxy.

Uses a Telethon user session to call Diskwala's Mini App endpoint,
obtains an auth token, then fetches video metadata directly from
Diskwala's API.

Requires these env vars:
    SESSION       — Telethon StringSession (user account, NOT bot)
    APP_ID        — Telegram API ID (from my.telegram.org)
    API_HASH      — Telegram API hash

Falls back to the proxy (public_api.get_diskwala_info) when SESSION
is not set.
"""
import os
import re
import json
import time
import logging
import asyncio
from urllib.parse import urlparse, unquote

import requests

log = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

DISKWALA_SESSION = os.getenv("SESSION")
DISKWALA_APP_ID = int(os.getenv("APP_ID", "0"))
DISKWALA_API_HASH = os.getenv("API_HASH", "")

# The bot that hosts the Mini App — must match the one your session has opened
DISKWALA_BOT_USERNAME = os.getenv("DISKWALA_BOT_USERNAME", "sky577bot")
DISKWALA_APP_SHORT_NAME = os.getenv("DISKWALA_APP_SHORT_NAME", "open")

DISKWALA_API_BASE = "https://api2.diskwala.net/api/diskwala"
DISKWALA_DOWNLOAD_API = f"{DISKWALA_API_BASE}/download"
DISKWALA_STATUS_API = f"{DISKWALA_API_BASE}/status?link="

# Regex for Diskwala share URLs
DISKWALA_URL_RE = re.compile(
    r"https?://(?:www\.)?diskwala\.com/(?:app|sharing/link)\b\S*", re.IGNORECASE
)
_LINK_ID_RE = re.compile(r"[a-fA-F0-9]{24}")


# ── Exceptions ───────────────────────────────────────────────────────────────

class DiskwalaDirectError(Exception):
    """Raised when direct Diskwala resolution fails."""
    pass


# ── URL helpers ──────────────────────────────────────────────────────────────

def extract_diskwala_id(text: str) -> str | None:
    m = _LINK_ID_RE.search(text or "")
    return m.group(0) if m else None


def extract_all_diskwala_urls(text: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for m in DISKWALA_URL_RE.finditer(text or ""):
        url = m.group(0).rstrip(").,]}\"'")
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


# ── Telethon auth ────────────────────────────────────────────────────────────

_telethon_client = None


def _get_telethon_client():
    """Lazily create a Telethon client from the user session string."""
    global _telethon_client
    if _telethon_client is not None:
        return _telethon_client

    if not DISKWALA_SESSION:
        raise DiskwalaDirectError("SESSION not set — cannot resolve directly")
    if not DISKWALA_APP_ID or not DISKWALA_API_HASH:
        raise DiskwalaDirectError("APP_ID / API_HASH not set")

    from telethon import TelegramClient
    from telethon.sessions import StringSession

    _telethon_client = TelegramClient(
        StringSession(DISKWALA_SESSION),
        DISKWALA_APP_ID,
        DISKWALA_API_HASH,
    )
    return _telethon_client


async def _get_auth_token() -> str:
    """Call Diskwala's Mini App endpoint to obtain a Bearer auth token."""
    from telethon.tl.functions.messages import RequestAppWebViewRequest
    from telethon.tl.types import (
        InputBotAppShortName,
        InputPeerSelf,
        DataJSON,
    )

    client = _get_telethon_client()
    if not client.is_connected():
        await client.connect()

    # Resolve the bot entity
    bot_entity = await client.get_input_entity(DISKWALA_BOT_USERNAME)

    # Request the Mini App web view
    result = await client(RequestAppWebViewRequest(
        peer=InputPeerSelf(),
        app=InputBotAppShortName(
            bot_id=bot_entity,
            short_name=DISKWALA_APP_SHORT_NAME,
        ),
        platform="android",
        write_allowed=True,
        start_param="",
        theme_params=DataJSON("{}"),
    ))

    # Extract the auth token from the URL fragment
    url = result.url
    fragment = urlparse(url).fragment
    tg_web_app_data = unquote(
        fragment.split("tgWebAppData=", 1)[1].split("&tgWebAppVersion=", 1)[0]
    )

    # The token is inside the JSON data
    data = json.loads(tg_web_app_data)
    auth_token = data.get("auth", {}).get("auth_token")
    if not auth_token:
        raise DiskwalaDirectError(f"No auth_token in Mini App response: {tg_web_app_data[:200]}")

    log.info("Got Diskwala auth token via Mini App")
    return auth_token


# ── API calls ────────────────────────────────────────────────────────────────

def _make_headers(auth_token: str) -> dict:
    return {
        "Authorization": f"Bearer {auth_token}",
        "X-Bot-Id": "diskwala",
        "Origin": "https://miniapp.diskwala.net",
        "Referer": "https://miniapp.diskwala.net/",
        "Content-Type": "application/json",
        "Accept": "*/*",
        "User-Agent": "Mozilla/5.0",
    }


def _start_download(diskwala_url: str, headers: dict) -> dict:
    """POST to the download endpoint to start a resolution job."""
    resp = requests.post(
        DISKWALA_DOWNLOAD_API,
        headers=headers,
        json={"link": diskwala_url},
        timeout=60,
    )
    data = resp.json()
    if not data.get("ok"):
        raise DiskwalaDirectError(
            data.get("error", "Download API returned ok=false")
        )
    return data


def _poll_status(diskwala_url: str, headers: dict, timeout: int = 120) -> dict:
    """Poll the status endpoint until status='done' or timeout."""
    from urllib.parse import quote

    status_url = DISKWALA_STATUS_API + quote(diskwala_url, safe="")
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        resp = requests.get(status_url, headers=headers, timeout=60)
        data = resp.json()

        if not data.get("ok"):
            raise DiskwalaDirectError(
                data.get("error", "Status API returned ok=false")
            )

        status = data.get("status", "").lower()
        if status == "done":
            return data
        if status == "error":
            raise DiskwalaDirectError(
                data.get("error", "Diskwala processing failed")
            )
        # pending — wait and retry
        time.sleep(2)

    raise DiskwalaDirectError(
        f"Timed out after {timeout}s waiting for Diskwala status"
    )


def _pick_file_field(file_obj: dict, *keys, required=True, default=None):
    """Extract the first non-empty value from a file object by key name."""
    for k in keys:
        if k in file_obj and file_obj[k] not in (None, ""):
            return file_obj[k]
    if required:
        raise DiskwalaDirectError(
            f"Diskwala response format changed — none of {keys} found. "
            f"Raw: {json.dumps(file_obj, indent=2)[:500]}"
        )
    return default


# ── Public API ───────────────────────────────────────────────────────────────

def get_diskwala_info_direct(diskwala_url: str) -> dict:
    """
    Resolve a Diskwala share URL to downloadable video info using the
    Mini App API.

    Returns {"filename": str, "size": int, "download_url": str}.
    Raises DiskwalaDirectError on failure.
    """
    if not DISKWALA_SESSION:
        raise DiskwalaDirectError("SESSION not configured")

    # Get auth token (sync wrapper around async Telethon)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're inside an async context — use ensure_future
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                auth_token = pool.submit(
                    asyncio.run, _get_auth_token()
                ).result(timeout=30)
        else:
            auth_token = loop.run_until_complete(_get_auth_token())
    except DiskwalaDirectError:
        raise
    except Exception as e:
        raise DiskwalaDirectError(f"Failed to get auth token: {e}") from e

    headers = _make_headers(auth_token)

    # Start download job
    log.info("Starting Diskwala download job…")
    try:
        _start_download(diskwala_url, headers)
    except requests.RequestException as e:
        raise DiskwalaDirectError(f"Network error calling Diskwala API: {e}") from e

    # Poll for completion
    log.info("Polling Diskwala status…")
    try:
        result = _poll_status(diskwala_url, headers, timeout=120)
    except requests.RequestException as e:
        raise DiskwalaDirectError(f"Network error polling Diskwala status: {e}") from e

    file_obj = result.get("file")
    if not file_obj:
        raise DiskwalaDirectError(f"No file in Diskwala response: {result}")

    filename = _pick_file_field(file_obj, "name", "fileName", "filename", "title")
    size = _pick_file_field(file_obj, "size", "fileSize", "length", required=False, default=0)
    download_url = _pick_file_field(
        file_obj, "downloadUrl", "download_url", "url", "link"
    )

    log.info(f"Diskwala direct resolved: {filename} ({size} bytes)")

    return {
        "filename": filename or "diskwala_video.mp4",
        "size": int(size) if size else 0,
        "download_url": download_url,
    }
