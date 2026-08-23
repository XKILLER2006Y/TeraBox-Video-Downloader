"""
Shared HTTP session singleton with connection pooling.

Every resolver and downloader should use get_session() instead of creating
their own requests.Session. This eliminates repeated TLS handshakes and
TCP connection setup — the single biggest performance win across the bot.
"""
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
