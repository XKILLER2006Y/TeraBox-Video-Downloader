"""
Doodstream (dood.watch/dood.wf/dood.re) resolver — video hosting with JS token.

Flow:
  1. GET page → extract token from JS
  2. Build download URL: https://doodwatch.com/download/<token>/<id>
  3. Handle referer check (must send correct Referer header)

No auth needed. Token expires quickly — resolve and download immediately.
"""
import re
import logging
import requests
from network import get_session

logger = logging.getLogger(__name__)


class DoodError(Exception):
    """Base exception for Dood resolver."""


class DoodNotFound(DoodError):
    """Video not found or deleted."""


_DOOD_RE = re.compile(
    r'https?://(?:www\.)?(?:dood\.watch|dood\.wf|dood\.re|dood\.to|dood\.pm|'
    r'dood\.ws|dood\.so|dood\.pro|doodstream\.com|doodstream\.co|'
    r'poystd\.com|lolalytics\.com|d0o0d\.com|dewdew\.xyz|'
    r'iframe\.fafm\.xyz|tithus\.xyz|yswtg\.xyz|'
    r'k2s\.cc|bayfiles\.com|megaup\.net|letsupload\.io|'
    r'streamtape\.com|fsmost\.com|canvas\.garden)'
    r'/(?:d|e|embed)/([A-Za-z0-9]+)',
    re.I,
)

# More permissive — catch any dood domain
_DOOD_DOMAIN_RE = re.compile(
    r'https?://(?:www\.)?(dood\.(?:watch|wf|re|to|pm|ws|so|pro)|'
    r'doodstream\.(?:com|co))',
    re.I,
)

# Token in page:  var token = "xxxxx" or  const token = 'xxxxx'
_TOKEN_RE = re.compile(r'(?:var|const|let)\s+token\s*=\s*["\']([a-f0-9]+)["\']', re.I)

# Pass token from JS:  /pass_md5/xxx/yyy → actual download URL
_PASS_MD5_RE = re.compile(r'/pass_md5/([^\s"\']+)', re.I)

# Direct download URL pattern
_DL_RE = re.compile(r'href=["\']?(https?://[^"\'>\s]*(?:\.mp4|/download|/stream)[^"\'>\s]*)', re.I)

# The complete download URL construction:
# https://doodwatch.com/pass_md5/<base64_part>?token=<token>
# Then it returns a redirect to the actual download URL with ?token= appended
_PASS_URL_RE = re.compile(
    r'(?:https?://[^"\'>\s]*)/pass_md5/([^\s"\'>]+)',
    re.I,
)


def is_dood_url(url: str) -> bool:
    return bool(_DOOD_RE.search(url) or _DOOD_DOMAIN_RE.search(url))


def extract_dood_url(text: str) -> list[str]:
    urls = []
    seen = set()
    for m in _DOOD_RE.finditer(text):
        u = m.group(0)
        if u not in seen:
            seen.add(u)
            urls.append(u)
    # Also catch bare dood domains
    for m in _DOOD_DOMAIN_RE.finditer(text):
        u = m.group(0)
        if u not in seen and len(u) > 20:
            seen.add(u)
            urls.append(u)
    return urls


def _extract_token(html: str) -> str | None:
    """Extract the token from page JS."""
    m = _TOKEN_RE.search(html)
    return m.group(1) if m else None


def _extract_pass_path(html: str) -> str | None:
    """Extract /pass_md5/ path from page JS."""
    m = _PASS_MD5_RE.search(html)
    return m.group(1) if m else None


def resolve_dood(url: str, session: requests.Session | None = None) -> dict:
    """
    Resolve a Doodstream URL.
    Returns: {"filename": str, "size": int, "download_url": str, "headers": dict}
    """
    sess = session or get_session()
    sess.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0.0.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    })

    # Get the page
    try:
        resp = sess.get(url, timeout=20, allow_redirects=True)
    except requests.RequestException as e:
        raise DoodError(f"Failed to fetch page: {e}") from e

    if resp.status_code == 404:
        raise DoodNotFound(f"Video not found: {url}")

    html = resp.text
    if re.search(r'video\s+(has\s+been\s+)?(removed|deleted|expired)', html, re.I):
        raise DoodNotFound(f"Video has been removed: {url}")

    # Extract token
    token = _extract_token(html)
    if not token:
        raise DoodError(f"Could not extract token from Dood page: {url}")

    # Extract pass_md5 path
    pass_path = _extract_pass_path(html)
    if not pass_path:
        raise DoodError(f"Could not extract pass_md5 path from Dood page: {url}")

    # Build the pass URL
    # Determine the domain from the original URL
    domain_m = _DOOD_DOMAIN_RE.search(url)
    if not domain_m:
        domain_m = _DOOD_RE.search(url)
    if not domain_m:
        raise DoodError(f"Cannot determine Dood domain from: {url}")

    base_domain = domain_m.group(1) if '.' in domain_m.group(0) else domain_m.group(0)
    if not base_domain.startswith('http'):
        base_domain = f"https://{base_domain}"

    pass_url = f"{base_domain}/pass_md5/{pass_path}?token={token}"

    # Fetch the pass URL (returns redirect or the download URL)
    try:
        pass_resp = sess.get(pass_url, timeout=15, headers={
            'Referer': url,
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131',
        })

        if pass_resp.status_code == 200:
            # Check if response is the URL itself
            text = pass_resp.text.strip()
            if text.startswith('http'):
                download_url = text
            else:
                raise DoodError(f"Unexpected pass_md5 response: {text[:200]}")
        elif pass_resp.status_code in (301, 302):
            download_url = pass_resp.headers.get('Location', '')
        else:
            raise DoodError(f"pass_md5 returned status {pass_resp.status_code}")
    except requests.RequestException as e:
        raise DoodError(f"pass_md5 request failed: {e}") from e

    if not download_url:
        raise DoodError("No download URL from pass_md5")

    # Append token if not already present
    if 'token=' not in download_url:
        download_url += f"?token={token}"

    # Dood requires correct referer header for download
    headers = {
        'Referer': f"{base_domain}/",
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131',
    }

    # Try to extract filename from page
    fname_m = re.search(r'<title>\s*(.+?)\s*(?:\||-|—)', html)
    filename = fname_m.group(1).strip() if fname_m else f"dood_{token[:12]}"

    return {
        "filename": filename,
        "size": 0,
        "download_url": download_url,
        "headers": headers,
    }
