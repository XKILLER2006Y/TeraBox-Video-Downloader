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
import threading
import logging
import asyncio
from urllib.parse import urlparse, unquote

import requests

log = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────────────────────

DISKWALA_SESSION = os.getenv("SESSION")
DISKWALA_APP_ID = int(os.getenv("APP_ID", "0"))
DISKWALA_API_HASH = os.getenv("API_HASH", "")

# The bot that hosts the Mini App — must match the one your session has opened
DISKWALA_BOT_USERNAME = os.getenv("DISKWALA_BOT_USERNAME", "sky577bot")
DISKWALA_APP_SHORT_NAME = os.getenv("DISKWALA_APP_SHORT_NAME", "open")

DISKWALA_API_BASE = "https://api2.diskwala.net/api/diskwala"
DISKWALA_DOWNLOAD_API = f"{DISKWALA_API_BASE}/download/d"
DISKWALA_STATUS_API = f"{DISKWALA_API_BASE}/status?link="

# AES-GCM key for decrypting _x responses (extracted from Mini App JS bundle)
# Override via DISKWALA_AES_KEY_HEX env var if Diskwala rotates the key.
_DISKWALA_AES_KEY_HEX = os.environ.get(
    "DISKWALA_AES_KEY_HEX",
    "e7109544dab612bd5b80b8a427ac474ba5541b9efff7a4ca1c8ef85df2489c23",
)

# Regex for Diskwala share URLs
DISKWALA_URL_RE = re.compile(
    r"https?://(?:www\.)?diskwala\.com/(?:app|sharing/link)\b\S*", re.IGNORECASE
)
_LINK_ID_RE = re.compile(r"[a-fA-F0-9]{24}")


# ── Exceptions ─────────────────────────────────────────────────────────────────────────

from diskwalaDL.errors import DiskwalaDirectError  # noqa: E402 — re-exported for compat

__all__ = ["DiskwalaDirectError", "get_diskwala_info_direct"]


# ── AES-GCM decryption ─────────────────────────────────────────────────────────────———

def _decrypt_diskwala_response(encrypted_obj: dict) -> dict:
    """
    Decrypt an _x=1 response from the Diskwala API.

    The Mini App returns { _x: true, s: <iv_hex>, h: <tag_hex>, p: <ct_hex> }.
    This function decrypts using AES-256-GCM with the hardcoded key from the
    Mini App JS bundle, returning the plaintext JSON as a dict.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    if not encrypted_obj.get("_x"):
        return encrypted_obj

    key = bytes.fromhex(_DISKWALA_AES_KEY_HEX)
    iv = bytes.fromhex(encrypted_obj["s"])
    tag = bytes.fromhex(encrypted_obj["h"])
    ct = bytes.fromhex(encrypted_obj["p"])

    # AES-GCM: ciphertext + appended auth tag
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(iv, ct + tag, None)

    return json.loads(plaintext.decode("utf-8"))


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

    # The Mini App sends the raw initData string as Bearer token.
    # The URL fragment contains: tgWebAppData=<URL-encoded initData>&tgWebAppVersion=...
    # We need to extract and decode just the initData portion.
    url = result.url
    fragment = urlparse(url).fragment
    tg_web_app_data = unquote(
        fragment.split("tgWebAppData=", 1)[1].split("&tgWebAppVersion=", 1)[0]
    )

    if not tg_web_app_data:
        raise DiskwalaDirectError("Empty initData in Mini App response")

    log.info("Got Diskwala auth token via Mini App")
    return tg_web_app_data


# ── API calls ─────────────────────────────────────────────────────────────────—————————————————————

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
    # Decrypt _x responses
    if isinstance(data, dict) and data.get("_x"):
        data = _decrypt_diskwala_response(data)
    if not data.get("ok"):
        raise DiskwalaDirectError(
            data.get("error", "Download API returned ok=false")
        )
    return data


def _poll_status(diskwala_url: str, headers: dict, timeout: int = 120) -> dict:
    """Poll the status endpoint until status='done' or timeout. Adaptive interval."""
    from urllib.parse import quote

    status_url = DISKWALA_STATUS_API + quote(diskwala_url, safe="")
    deadline = time.monotonic() + timeout
    poll_interval = 0.5  # start fast

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
        # pending — adaptive backoff: 0.5s → 1s → 2s (capped)
        time.sleep(poll_interval)
        poll_interval = min(poll_interval * 1.5, 2.0)

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


# ── Auth loop thread ─────────────────────────────────────────────────────────
# The Telethon client is bound to the event loop it first connects on.
# Creating a new asyncio.run() loop per call rebinds futures across loops and
# crashes on the second request. Solution: one dedicated background thread
# owns a persistent loop forever; every auth call is submitted to it.

_auth_loop: asyncio.AbstractEventLoop | None = None
_auth_loop_lock = threading.Lock()
_auth_loop_ready = threading.Event()

# Mini App initData tokens stay valid for hours — cache to avoid a Telegram
# roundtrip (RequestAppWebViewRequest) on every single resolution request.
_token_cache: dict = {"token": "", "fetched_at": 0.0}
_token_cache_lock = threading.Lock()
_TOKEN_TTL_SECONDS = 3600  # 1 hour, conservative


def _get_auth_loop() -> asyncio.AbstractEventLoop:
    """Return the persistent background loop, starting its thread if needed."""
    global _auth_loop
    if _auth_loop is not None and _auth_loop.is_running():
        return _auth_loop
    with _auth_loop_lock:
        if _auth_loop is None or not _auth_loop.is_running():
            _auth_loop_ready.clear()

            def _run_forever():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                global _auth_loop
                _auth_loop = loop
                _auth_loop_ready.set()
                loop.run_forever()

            t = threading.Thread(target=_run_forever, daemon=True, name="diskwala-auth")
            t.start()
            _auth_loop_ready.wait(timeout=5)
    return _auth_loop


def get_diskwala_info_direct(diskwala_url: str) -> dict:
    """
    Resolve a Diskwala share URL to downloadable video info using the
    Mini App API.

    Returns {"filename": str, "size": int, "download_url": str}.
    Raises DiskwalaDirectError on failure.
    """
    if not DISKWALA_SESSION:
        raise DiskwalaDirectError("SESSION not configured")

    # Get auth token via the dedicated auth-loop thread (cached with TTL, thread-safe)
    try:
        now = time.monotonic()
        with _token_cache_lock:
            if _token_cache["token"] and (now - _token_cache["fetched_at"]) < _TOKEN_TTL_SECONDS:
                auth_token = _token_cache["token"]
                fresh = True
            else:
                fresh = False
        if not fresh:
            fut = asyncio.run_coroutine_threadsafe(
                _get_auth_token(), _get_auth_loop()
            )
            auth_token = fut.result(timeout=60)
            with _token_cache_lock:
                _token_cache["token"] = auth_token
                _token_cache["fetched_at"] = time.monotonic()
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

    # Decrypt _x encrypted file data
    if isinstance(file_obj, dict) and file_obj.get("_x"):
        file_obj = _decrypt_diskwala_response(file_obj)

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
