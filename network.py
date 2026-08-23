"""
Shared HTTP session singleton with connection pooling + DNS cache.

Every resolver and downloader should use get_session() instead of creating
their own requests.Session. This eliminates repeated TLS handshakes and
TCP connection setup — the single biggest performance win across the bot.
"""
import socket
import time
import threading
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_browser_headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

_lock = threading.Lock()
_session: requests.Session | None = None

# ── DNS cache ──────────────────────────────────────────────────────────────────
# Avoids repeated DNS resolution for the same host across many concurrent
# downloads. Entries cached for 10 minutes.
_dns_cache: dict = {}  # hostname -> (ip, expiry)
_dns_cache_lock = threading.Lock()
_dns_TTL = 600  # seconds


def _cached_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    """DNS resolver with in-memory TTL cache."""
    now = time.time()
    with _dns_cache_lock:
        entry = _dns_cache.get(host)
        if entry and entry[1] > now:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (entry[0], port))]

    result = socket.getaddrinfo(host, port, family, type, proto, flags)
    if result:
        ip = result[0][4][0]
        with _dns_cache_lock:
            _dns_cache[host] = (ip, now + _dns_TTL)
    return result


def get_session() -> requests.Session:
    """
    Return the global requests.Session singleton.

    Thread-safe. First call creates the session with generous connection
    pooling (50 connections, 50 max per host). Subsequent calls return
    the same instance — connection reuse means zero TLS overhead on
    repeated requests to the same host.
    """
    global _session
    if _session is not None:
        return _session
    with _lock:
        if _session is not None:
            return _session
        s = requests.Session()
        s.headers.update(_browser_headers)
        adapter = HTTPAdapter(
            pool_connections=50,
            pool_maxsize=50,
            max_retries=Retry(total=0),  # we handle retries ourselves
        )
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        _session = s
        return _session


def prewarm_connections():
    """
    Pre-resolve DNS and warm connection pools for key hosts.
    Call once at bot startup so the first user download doesn't pay
    the full TLS handshake cost.
    """
    hosts = [
        "dm.1024tera.com",
        "www.1024tera.com",
        "terabox.com",
    ]
    import logging
    log = logging.getLogger(__name__)
    session = get_session()
    for host in hosts:
        try:
            # DNS resolution + TCP connect + TLS handshake
            session.head(f"https://{host}/", timeout=5)
            log.info(f"Prewarmed connection to {host}")
        except Exception:
            log.debug(f"Prewarm {host} failed (non-critical)")
