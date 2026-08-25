"""
MixDrop (mixdrop.to/mixdrop.co) resolver — video hosting with JS obfuscation.

Flow:
  1. GET page → extract JS code that builds download URL
  2. Decode the URL (usually base64 + char code operations)
  3. Return direct download URL

No auth needed.
"""
import re
import logging
import base64
import requests
from network import get_session
from urllib.parse import urljoin

logger = logging.getLogger(__name__)

_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}


class MixDropError(Exception):
    """Base exception for MixDrop resolver."""


class MixDropNotFound(MixDropError):
    """Video not found or deleted."""


_MIXDROP_RE = re.compile(
    # md*. mirrors use literal subdomain dots — the old unescaped `md*.`
    # matched any m*.to host ("m.to", "md.co", ...).
    r'https?://(?:www\.)?(?:mixdrop\.(?:to|co|ch)|'
    r'md[-a-z0-9]*\.(?:to|co|ch|cc|cx|st|sx|se|gz|vf|hk|si|ws|pm))'
    r'/(?:e|f|embed|v)/([A-Za-z0-9]+)',
    re.I,
)

# Also catch bare domain patterns
_MIXDROP_DOMAIN_RE = re.compile(
    r'https?://(?:www\.)?mixdrop\.(?:to|co|ch|cc|cx|st|sx|se|gz|vf|hk|si|ws|pm)',
    re.I,
)

# The characteristic MixDrop JS pattern:
#eval(function(p,a,c,k,e,d){...})
# After deobfuscation, it contains:  /dl/<id>/... or a direct URL
_DL_EVAL_RE = re.compile(r'eval\(function\(p,a,c,k,e,d\)\s*\{', re.I)

# Patterns in deobfuscated code
_MIXDROP_DL_RE = re.compile(r'["\']?(?:https?://[^"\'>\s]*)?(?:/dl/|/download/)([A-Za-z0-9/]+)', re.I)
_MIXDROP_PATH_RE = re.compile(r'(?:src|href)\s*=\s*["\']([^"\']*(?:\.mp4|\.m3u8|/dl/)[^"\']*)', re.I)
_STYLES_RE = re.compile(r'background:\s*url\(["\']?([^"\')\s]+)', re.I)

# Also try direct URL patterns
_DIRECT_DL_RE = re.compile(r'href=["\']?(https?://[^"\'>\s]*(?:\.mp4|download|/dl/)[^"\'>\s]*)', re.I)


def is_mixdrop_url(url: str) -> bool:
    return bool(_MIXDROP_RE.search(url) or _MIXDROP_DOMAIN_RE.search(url))


def extract_mixdrop_url(text: str) -> list[str]:
    urls = []
    seen = set()
    for m in _MIXDROP_RE.finditer(text):
        u = m.group(0)
        if u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def _deobfuscate_pack(text: str) -> str:
    """Attempt to deobfuscate packed JS (base64 + char code)."""
    # Simple deobfuscation: look for base64 encoded URLs
    b64_match = re.search(r'atob\(["\']([A-Za-z0-9+/=]+)["\']\)', text)
    if b64_match:
        try:
            return base64.b64decode(b64_match.group(1)).decode('utf-8', errors='ignore')
        except Exception:
            pass

    # Look for char code arrays
    charcode = re.search(r'(?:String\.fromCharCode|chr)\(([\d,\s]+)\)', text)
    if charcode:
        try:
            codes = [int(c.strip()) for c in charcode.group(1).split(',')]
            return ''.join(chr(c) for c in codes)
        except Exception:
            pass

    return text


def resolve_mixdrop(url: str, session: requests.Session | None = None) -> dict:
    """
    Resolve a MixDrop URL.
    Returns: {"filename": str, "size": int, "download_url": str, "headers": dict}
    """
    sess = session or get_session()

    # Normalize URL
    if not _MIXDROP_RE.search(url):
        # Try to find the video ID in the URL
        m = re.search(r'/([A-Za-z0-9]{6,12})(?:\?|$|#)', url)
        if m:
            url = url.rstrip('/') + '/' + m.group(1)

    try:
        resp = sess.get(url, headers=_HEADERS, timeout=20, allow_redirects=True)
    except requests.RequestException as e:
        raise MixDropError(f"Failed to fetch page: {e}") from e

    if resp.status_code == 404:
        raise MixDropNotFound(f"Video not found: {url}")

    html = resp.text

    # Try direct download link
    m = _DIRECT_DL_RE.search(html)
    if m:
        dl_url = urljoin(resp.url, m.group(1))
        fname_m = re.search(r'<title>\s*(.+?)\s*(?:\||-)', html)
        fname = fname_m.group(1).strip() if fname_m else "mixdrop_video"
        return {"filename": fname, "size": 0, "download_url": dl_url, "headers": {'Referer': resp.url}}

    # Try CSS background URL
    m = _STYLES_RE.search(html)
    if m:
        dl_url = urljoin(resp.url, m.group(1))
        fname_m = re.search(r'<title>\s*(.+?)\s*(?:\||-)', html)
        fname = fname_m.group(1).strip() if fname_m else "mixdrop_video"
        return {"filename": fname, "size": 0, "download_url": dl_url, "headers": {'Referer': resp.url}}

    # Try to deobfuscate packed JS
    if _DL_EVAL_RE.search(html):
        deob = _deobfuscate_pack(html)
        m = _MIXDROP_DL_RE.search(deob) or _MIXDROP_PATH_RE.search(deob)
        if m:
            dl_url = urljoin(resp.url, m.group(0) if not m.group(0).startswith('http') else m.group(0))
            fname_m = re.search(r'<title>\s*(.+?)\s*(?:\||-)', html)
            fname = fname_m.group(1).strip() if fname_m else "mixdrop_video"
            return {"filename": fname, "size": 0, "download_url": dl_url, "headers": {'Referer': resp.url}}

    # Last resort: look for any iframe or script src pointing to a video
    iframe_m = re.search(r'<iframe[^>]*src=["\']([^"\']*(?:mp4|m3u8|video)[^"\']*)', html, re.I)
    if iframe_m:
        return {"filename": "mixdrop_video", "size": 0, "download_url": iframe_m.group(1), "headers": {'Referer': resp.url}}

    raise MixDropError(f"Could not extract download URL from MixDrop: {url}")
