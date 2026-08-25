"""
Shared HTTP session singleton with connection pooling + DNS cache.

Every resolver and downloader should use get_session() instead of creating
their own requests.Session. This eliminates repeated TLS handshakes and
TCP connection setup — the single biggest performance win across the bot.
"""
import os
import socket
import time
import threading
import logging
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

# ── TCP tuning ──────────────────────────────────────────────────────────────────
# TCP_NODELAY disables Nagle: small API requests are sent immediately.
# TCP receive buffer (64KB) speeds up bulk data reads from TeraBox CDN.
_TCP_NODELAY = True
_RECV_BUFFER = int(os.environ.get("TCP_RECV_BUFFER", "65536"))

# Advertise brotli only when a decoder is importable — advertising br
# without the lib makes servers send compressed bytes requests can't
# decode, and r.text becomes binary garbage (FilesAdda outage root cause).
try:
    import brotli  # noqa: F401

    _ACCEPT_ENCODING = "gzip, deflate, br"
except ImportError:
    _ACCEPT_ENCODING = "gzip, deflate"

_browser_headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": _ACCEPT_ENCODING,
    "Connection": "keep-alive",
}

# Centralized user agents — all resolvers import from here instead of defining their own.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36",
]

_lock = threading.Lock()
_session: requests.Session | None = None


class _TCPAdapter(HTTPAdapter):
    """HTTPAdapter that applies TCP_NODELAY + receive buffer tuning."""

    def __init__(self, *args, nodelay=True, recv_buffer=0, **kwargs):
        self._nodelay = nodelay
        self._recv_buffer = recv_buffer
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, *args, **kwargs):
        kwargs["socket_options"] = []
        if self._nodelay:
            kwargs["socket_options"].append(
                (socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            )
        if self._recv_buffer:
            kwargs["socket_options"].append(
                (socket.SOL_SOCKET, socket.SO_RCVBUF, self._recv_buffer)
            )
        super().init_poolmanager(*args, **kwargs)

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

    Thread-safe. First call creates the session with connection
    pooling. Pool sizes configurable via CONN_POOL_SIZE env var.
    Uses TCP_NODELAY for low-latency API calls and configurable
    receive buffer for faster bulk downloads.
    """
    global _session
    if _session is not None:
        return _session
    with _lock:
        if _session is not None:
            return _session
        try:
            pool_size = int(os.environ.get("CONN_POOL_SIZE", "20"))
        except (ValueError, TypeError):
            pool_size = 20
        s = requests.Session()
        s.headers.update(_browser_headers)
        adapter = _TCPAdapter(
            pool_connections=pool_size,
            pool_maxsize=pool_size,
            max_retries=Retry(total=0),
            nodelay=True,
            recv_buffer=_RECV_BUFFER,
        )
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        _session = s
        log.info(f"HTTP session created (pool_size={pool_size}, TCP_NODELAY=on, recv_buf={_RECV_BUFFER})")
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
    ]
    session = get_session()
    for host in hosts:
        try:
            # DNS resolution + TCP connect + TLS handshake
            session.head(f"https://{host}/", timeout=3)
            log.info(f"Prewarmed connection to {host}")
        except Exception:
            log.debug(f"Prewarm {host} failed (non-critical)")
