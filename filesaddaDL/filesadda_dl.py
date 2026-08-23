"""
filesadda.site — XFileSharing direct-download resolver.

XFileSharing is a generic file-hosting script used by hundreds of sites:
filesadda.site, file-upload.com, oload.info, streamsb.com, etc.

Flow:
  1. GET page → extract file metadata (name, size)
  2. POST op=download2 → get timer page
  3. Wait timer → POST op=download3 → get actual download link
  4. Download the file directly via HTTP

No auth needed — pure HTTP with optional CAPTCHA.
"""
import re
import time
import logging
from urllib.parse import urljoin

import requests

logger = logging.getLogger(__name__)


class FilesAddaError(Exception):
    """Base exception for filesadda resolver."""


class FilesAddaNotFound(FilesAddaError):
    """File does not exist or has been removed."""


class FilesAddaCaptcha(FilesAddaError):
    """File requires CAPTCHA — cannot resolve automatically."""


# ── URL patterns ──────────────────────────────────────────────────────────────
# Matches filesadda.site/<code> and similar XFileSharing clones
_FILEADDA_RE = re.compile(
    r'https?://(?:filesadda\.site|file-upload\.com|oload\.info|'
    r'streamsb\.com|streamtape\.com|filesrand\.com|filelions\.to|'
    r'File-Upload\.org|酷云Pan|upload-1fichier\.com)'
    r'/([A-Za-z0-9]{4,20})',
    re.IGNORECASE,
)

# Broader XFileSharing detection — any domain with short alphanumeric path
_XFS_GENERIC_RE = re.compile(
    r'https?://([a-z0-9-]+\.(?:site|com|net|org|to|cc|co|me|pw|icu|xyz|click|link|fun|work|st))'
    r'/([A-Za-z0-9]{6,20})$',
    re.IGNORECASE,
)

# File size patterns on XFS pages
_SIZE_RE = re.compile(r'([\d.,]+)\s*(KB|MB|GB|TB|bytes?)', re.IGNORECASE)
_NAME_RE = re.compile(r'<(?:h1|h2|div)[^>]*class="[^"]*name[^"]*"[^>]*>\s*(.+?)\s*</', re.I)
_NAME_RE2 = re.compile(r'(?:filename|file_name|original_name)["\s:=]+(["\']?)(.+?)\1(?:<|$)', re.I)
_TITLE_RE = re.compile(r'<title>\s*(.+?)\s*(?:\||-|—)\s*(?:Download|XFileSharing)', re.I)

# Timer/countdown patterns
_TIMER_RE = re.compile(r'(?:countdown|timer|wait|delay)["\s:=]+(\d+)', re.I)
_CAPTCHA_RE = re.compile(r'(?:recaptcha|captcha|hcaptcha|turnstile|g-recaptcha)', re.I)

# Download link patterns
_DL_LINK_RE = re.compile(r'href=["\']?(https?://[^"\'>\s]+download[^"\'>\s]*)', re.I)
_DL_LINK_RE2 = re.compile(r'href=["\']?(https?://[^"\'>\s]*(?:\.mp4|\.mkv|\.zip|\.rar|\.mp3|\.avi|\.mov|\.webm)[^"\'>\s]*)', re.I)
_DL_FORM_RE = re.compile(r'action=["\']?([^"\'>\s]+)["\']?', re.I)

# Op values
_OP_DOWNLOAD2 = re.compile(r'name=["\']?op["\']?\s+value=["\']?download2["\']?', re.I)
_OP_DOWNLOAD3 = re.compile(r'name=["\']?op["\']?\s+value=["\']?download3["\']?', re.I)

# Hidden form fields
_HIDDEN_FIELDS_RE = re.compile(
    r'<input[^>]*type=["\']hidden["\'][^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\']',
    re.I,
)
_HIDDEN_FIELDS_RE2 = re.compile(
    r'<input[^>]*name=["\']([^"\']+)["\'][^>]*value=["\']([^"\']*)["\'][^>]*type=["\']hidden["\']',
    re.I,
)


def is_filesadda_url(url: str) -> bool:
    """Check if URL is a filesadda/XFileSharing link."""
    return bool(_FILEADDA_RE.search(url))


def is_generic_xfs_url(url: str) -> bool:
    """Check if URL looks like a generic XFileSharing link."""
    return bool(_XFS_GENERIC_RE.search(url))


def extract_fileadda_url(text: str) -> list[str]:
    """Extract filesadda/XFileSharing URLs from text."""
    urls = []
    seen = set()
    for m in _FILEADDA_RE.finditer(text):
        url = m.group(0)
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _extract_hidden_fields(html: str) -> dict:
    """Extract all hidden form fields from HTML."""
    fields = {}
    for m in _HIDDEN_FIELDS_RE.finditer(html):
        fields[m.group(1)] = m.group(2)
    for m in _HIDDEN_FIELDS_RE2.finditer(html):
        if m.group(1) not in fields:
            fields[m.group(1)] = m.group(2)
    return fields


def _extract_filename(html: str) -> str:
    """Extract filename from XFS page HTML."""
    for regex in (_NAME_RE, _NAME_RE2, _TITLE_RE):
        m = regex.search(html)
        if m:
            name = m.group(1).strip()
            name = re.sub(r'<[^>]+>', '', name).strip()
            if name and len(name) > 2:
                return name
    return "download"


def _extract_filesize(html: str) -> int:
    """Extract file size in bytes from XFS page HTML."""
    m = _SIZE_RE.search(html)
    if not m:
        return 0
    val = float(m.group(1).replace(',', ''))
    unit = m.group(2).upper()
    multipliers = {'BYTES': 1, 'B': 1, 'KB': 1024, 'MB': 1024**2, 'GB': 1024**3, 'TB': 1024**4}
    return int(val * multipliers.get(unit, 1))


def _extract_download_link(html: str, page_url: str) -> str | None:
    """Extract the actual download link from the final page."""
    # Try explicit download links
    for regex in (_DL_LINK_RE, _DL_LINK_RE2):
        m = regex.search(html)
        if m:
            link = m.group(1)
            if not link.startswith('http'):
                link = urljoin(page_url, link)
            return link

    # Look for onclick handlers with URLs
    onclick = re.findall(r"onclick=['\"]([^'\"]*https?://[^'\"]+)['\"]", html, re.I)
    for link in onclick:
        if 'download' in link.lower() or any(ext in link.lower() for ext in ['.mp4', '.mkv', '.zip']):
            return link

    return None


def resolve_filesadda(url: str, session: requests.Session | None = None) -> dict:
    """
    Resolve a filesadda/XFileSharing URL to get download info.

    Returns:
        {"filename": str, "size": int, "download_url": str}

    Raises:
        FilesAddaNotFound: File doesn't exist
        FilesAddaCaptcha: CAPTCHA required
        FilesAddaError: Any other resolution error
    """
    sess = session or requests.Session()
    sess.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
    })

    # ── Step 1: GET the page ──────────────────────────────────────────────
    try:
        resp = sess.get(url, timeout=20, allow_redirects=True)
    except requests.RequestException as e:
        raise FilesAddaError(f"Failed to fetch page: {e}") from e

    if resp.status_code == 404:
        raise FilesAddaNotFound(f"File not found: {url}")

    html = resp.text

    # Check for file-not-found indicators
    if re.search(r'file\s+(has\s+been\s+)?removed|not\s+found|expired|deleted', html, re.I):
        raise FilesAddaNotFound(f"File has been removed or expired: {url}")

    # Check for CAPTCHA on first page
    if _CAPTCHA_RE.search(html):
        # Some XFS sites require CAPTCHA even on first page — try without it
        logger.warning("CAPTCHA detected on initial page, attempting to proceed")

    # ── Step 2: Extract metadata ──────────────────────────────────────────
    filename = _extract_filename(html)
    filesize = _extract_filesize(html)

    # ── Step 3: Check for direct download link (some files skip timer) ────
    direct = _extract_download_link(html, resp.url)
    if direct:
        logger.info("Direct download link found (no timer)")
        return {"filename": filename, "size": filesize, "download_url": direct}

    # ── Step 4: POST op=download2 (timer page) ────────────────────────────
    if _OP_DOWNLOAD2.search(html) or 'download' in html.lower():
        hidden = _extract_hidden_fields(html)
        post_data = {'op': 'download2', **hidden}

        # Try free download button text
        if 'method_free' not in post_data:
            post_data['method_free'] = 'Free Download'
        if 'method_premium' not in post_data:
            post_data['method_premium'] = 'Premium Download'

        try:
            resp2 = sess.post(url, data=post_data, timeout=20, allow_redirects=True)
            html2 = resp2.text
        except requests.RequestException as e:
            raise FilesAddaError(f"Download form submission failed: {e}") from e

        # Check for CAPTCHA on timer page
        if _CAPTCHA_RE.search(html2):
            raise FilesAddaCaptcha("File requires CAPTCHA verification — cannot resolve automatically")

        # Check for download link on timer page
        direct2 = _extract_download_link(html2, resp2.url)
        if direct2:
            return {"filename": filename, "size": filesize, "download_url": direct2}

        # ── Step 5: Wait timer ────────────────────────────────────────────
        timer_match = _TIMER_RE.search(html2)
        if timer_match:
            wait_secs = int(timer_match.group(1))
            logger.info(f"Timer: waiting {wait_secs}s")
            time.sleep(min(wait_secs, 60))  # cap at 60s safety

        # ── Step 6: POST op=download3 (actual link) ───────────────────────
        hidden2 = _extract_hidden_fields(html2)
        post_data2 = {'op': 'download3', **hidden2}

        try:
            resp3 = sess.post(url, data=post_data2, timeout=20, allow_redirects=True)
            html3 = resp3.text
        except requests.RequestException as e:
            raise FilesAddaError(f"Download link fetch failed: {e}") from e

        direct3 = _extract_download_link(html3, resp3.url)
        if direct3:
            return {"filename": filename, "size": filesize, "download_url": direct3}

        # Some XFS sites embed the link in JS
        js_link = re.search(r'(?:window\.)?(?:location(?:\.href)?)\s*=\s*["\']?(https?://[^"\'>\s]+)', html3)
        if js_link:
            return {"filename": filename, "size": filesize, "download_url": js_link.group(1)}

    # ── Fallback: check if page itself is the download (binary response) ──
    ct = resp.headers.get('Content-Type', '')
    if 'video' in ct or 'octet-stream' in ct or 'zip' in ct:
        cl = int(resp.headers.get('Content-Length', 0))
        return {"filename": filename, "size": cl or filesize, "download_url": resp.url}

    raise FilesAddaError(f"Could not extract download link from {url}")
