"""
StreamWish (streamwish.to/streamwish.xyz) resolver — video hosting with JS obfuscation.

Flow:
  1. GET page → extract obfuscated JS
  2. Parse download URL construction
  3. Return direct download URL

No auth needed.
"""
import re
import logging
import requests
from network import get_session
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}


class StreamWishError(Exception):
    """Base exception for StreamWish resolver."""


class StreamWishNotFound(StreamWishError):
    """Video not found or deleted."""


_STREAMWISH_RE = re.compile(
    r'https?://(?:www\.)?(?:streamwish\.to|streamwish\.xyz|streamwish\.com|'
    r'streamtape\.xyz|ashdyn\.net|awish\.xyz|cine\.xyz|kissasian\.sh|'
    r'streamani\.net|embeds\.aBest|ouo\.today|sbplay\.org|'
    r'mega\.nz/embed|krakenfiles\.com|fembed\.com|'
    r'streamcdn\.xyz|kissmanga\.link|vetstream\.xyz)'
    r'/(?:v|e|embed|f)/([A-Za-z0-9]+)',
    re.I,
)

# Broader StreamWish domain detection
_SW_DOMAIN_RE = re.compile(
    r'https?://(?:www\.)?(?:streamwish\.to|streamwish\.xyz|streamwish\.com|'
    r'ashdyn\.net|awish\.xyz|cine\.xyz)',
    re.I,
)

# Download URL patterns
_SW_DL_RE = re.compile(
    r'(?:href|src)=["\']?(https?://[^"\'>\s]*(?:\.mp4|\.m3u8|/download|/stream|/get)[^"\'>\s]*)',
    re.I,
)

# File name
_TITLE_RE = re.compile(r'<title>\s*(.+?)\s*(?:\||-|—)', re.I)

# JS eval pattern (similar to MixDrop)
_EVAL_RE = re.compile(r'eval\(function\(p,a,c,k,e,d\)', re.I)
_PASS_MD5_RE = re.compile(r'/pass_md5/([^\s"\']+)', re.I)


def is_streamwish_url(url: str) -> bool:
    return bool(_STREAMWISH_RE.search(url) or _SW_DOMAIN_RE.search(url))


def extract_streamwish_url(text: str) -> list[str]:
    urls = []
    seen = set()
    for m in _STREAMWISH_RE.finditer(text):
        u = m.group(0)
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def resolve_streamwish(url: str, session: requests.Session | None = None) -> dict:
    """
    Resolve a StreamWish URL.
    Returns: {"filename": str, "size": int, "download_url": str, "headers": dict}
    """
    sess = session or get_session()

    try:
        resp = sess.get(url, headers=_HEADERS, timeout=20, allow_redirects=True)
    except requests.RequestException as e:
        raise StreamWishError(f"Failed to fetch page: {e}") from e

    if resp.status_code == 404:
        raise StreamWishNotFound(f"Video not found: {url}")

    html = resp.text

    # Try direct download link
    m = _SW_DL_RE.search(html)
    if m:
        dl_url = m.group(1)
        if not dl_url.startswith('http'):
            dl_url = urljoin(resp.url, dl_url)
        fname_m = _TITLE_RE.search(html)
        fname = fname_m.group(1).strip() if fname_m else "streamwish_video"
        return {"filename": fname, "size": 0, "download_url": dl_url, "headers": {'Referer': resp.url}}

    # Try pass_md5 pattern (similar to Dood)
    pass_m = _PASS_MD5_RE.search(html)
    if pass_m:
        # Extract token
        token_m = re.search(r'(?:var|const|let)\s+token\s*=\s*["\']([^"\']+)["\']', html)
        if token_m:
            parsed = requests.compat.urlparse(resp.url)
            base = f"{parsed.scheme}://{parsed.netloc}"
            pass_url = f"{base}/pass_md5/{pass_m.group(1)}?token={token_m.group(1)}"
            try:
                pass_resp = sess.get(pass_url, timeout=15, headers={**_HEADERS, 'Referer': resp.url})
                dl_url = pass_resp.text.strip() if pass_resp.status_code == 200 else pass_resp.headers.get('Location', '')
                if dl_url:
                    if 'token=' not in dl_url:
                        dl_url += f"?token={token_m.group(1)}"
                    fname_m = _TITLE_RE.search(html)
                    fname = fname_m.group(1).strip() if fname_m else "streamwish_video"
                    return {"filename": fname, "size": 0, "download_url": dl_url, "headers": {'Referer': resp.url}}
            except Exception:
                pass

    raise StreamWishError(f"Could not extract download URL from StreamWish: {url}")
