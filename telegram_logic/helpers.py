import os
import re

def env_int(name: str, default: int = 0) -> int:
    """Parse an int env var; empty/missing/invalid values fall back to default."""
    raw = os.environ.get(name, "")
    try:
        return int(raw.strip() or default)
    except (ValueError, TypeError):
        return default

# Regex to match TeraBox share URLs and extract the SURL
TERA_URL_RE = re.compile(
    r"https?://(?:[\w.-]+\.)?[\w.-]+\.[a-z]{2,}"
    r"(?:/s/1(?P<surl_path>[A-Za-z0-9_-]+)"
    r"|/(?:sharing/link|wap/share/filelist)\?[^#]*surl=(?P<surl_param>[A-Za-z0-9_-]+))",
    re.IGNORECASE,
)

# ── Experimental modes (/exp, /exphd) ────────────────────────────────────────────
# The experimental proxy resolves many more TeraBox mirror domains and accepts
# flexible path shapes, so /exp and /exphd use their own, broader matcher.
#
# Supported shapes:
#   {baseURL}/<something>/{SURL}          e.g. https://terabox.com/s/1abc
#   {baseURL}/{SURL}                      e.g. https://terabox.com/1abc
#   {baseURL}/sharing/link?...surl=1abc   (query-param form, kept for compat)
_TERABOX_EXP_DOMAINS = (
    "terabox.com", "1024terabox.com", "teraboxapp.com", "freeterabox.com",
    "terabox.app", "terabox.fun", "4funbox.co", "4funbox.com",
    "mirrobox.com", "nephobox.com", "1024tera.com", "momerybox.com",
    "tibibox.com",
)
# Longest-first so e.g. "4funbox.com" is preferred over the "4funbox.co" prefix.
_TERABOX_EXP_DOMAIN_ALT = "|".join(
    d.replace(".", r"\.") for d in sorted(_TERABOX_EXP_DOMAINS, key=len, reverse=True)
)

TERA_EXP_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:" + _TERABOX_EXP_DOMAIN_ALT + r")"
    r"(?:"
    # Query-param form: /sharing/link?...surl=<surl>
    r"/(?:sharing/link|wap/share/filelist|share/link)\?[^\s#]*?surl=1?(?P<surl_param>[A-Za-z0-9_-]+)"
    r"|"
    # Path form: optional single intermediate segment, then the SURL.
    # A leading "1" of the SURL is not captured, matching legacy behaviour.
    r"(?:/[A-Za-z0-9_.%-]+)?/1?(?P<surl_path>[A-Za-z0-9_-]+)"
    r")",
    re.IGNORECASE,
)

# — Helpers ————————————————————————————————————————————————————————————————————————

def extract_surl(text: str) -> str | None:
    """Extract the first SURL from a TeraBox URL in the message text."""
    m = TERA_URL_RE.search(text)
    if m:
        return m.group("surl_path") or m.group("surl_param")
    return None

def extract_all_terabox_url(text: str) -> list[str]:
    """Extract all unique TeraBox URLs from the message text."""
    seen: set[str] = set()
    urls: list[str] = []
    for m in TERA_URL_RE.finditer(text):
        url = m.group(0)
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def extract_surl_exp(text: str) -> str | None:
    """Extract the first SURL from an experimental-mode (/exp, /exphd) TeraBox URL."""
    m = TERA_EXP_URL_RE.search(text)
    if m:
        return m.group("surl_path") or m.group("surl_param")
    return None


def extract_all_terabox_url_exp(text: str) -> list[str]:
    """Extract all unique TeraBox share URLs supported by /exp and /exphd."""
    seen: set[str] = set()
    urls: list[str] = []
    for m in TERA_EXP_URL_RE.finditer(text):
        url = m.group(0)
        if url and url not in seen:
            seen.add(url)
            urls.append(url)
    return urls

def extract_all_surls(text: str) -> list[str]:
    """Extract all unique SURLs from TeraBox URLs in the message text."""
    seen: set[str] = set()
    surls: list[str] = []
    for m in TERA_URL_RE.finditer(text):
        surl = m.group("surl_path") or m.group("surl_param")
        if surl and surl not in seen:
            seen.add(surl)
            surls.append(surl)
    return surls


def format_size(size_bytes: int) -> str:
    """Format bytes into a human-readable size string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / 1024 / 1024:.1f} MB"
    else:
        return f"{size_bytes / 1024 / 1024 / 1024:.2f} GB"


def format_duration(seconds: float) -> str:
    """Format seconds into a human-readable duration."""
    if seconds < 1:
        return f"{seconds:.1f}s"
    
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    
    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    if secs > 0 or not parts:
        parts.append(f"{secs}s")
        
    return " ".join(parts[:2]) # Return max 2 most significant parts for nice reading


# ── Quality selection (/exp <url> 720p) ────────────────────────────────────────

# Trailing token only, so URLs that merely contain "720" are never misparsed.
_QUALITY_TAIL_RE = re.compile(r"(?:^|\s)(360|480|720|1080)p?\s*$", re.IGNORECASE)

QUALITY_MAP: dict[int, str] = {
    360: "M3U8_AUTO_360",
    480: "M3U8_AUTO_480",
    720: "M3U8_AUTO_720",
    1080: "M3U8_AUTO_1080",
}

DEFAULT_QUALITY = "M3U8_AUTO_1080"


def parse_quality(text: str) -> tuple[str, str]:
    """
    Extract a trailing quality token (e.g. `720` / `720p`) from the end of
    the message text.

    Returns (remaining_text, terabox_quality_string). remaining_text has the
    quality token stripped; terabox_quality_string defaults to AUTO_1080.
    """
    m = _QUALITY_TAIL_RE.search(text)
    if not m:
        return text, DEFAULT_QUALITY
    height = int(m.group(1))
    quality = QUALITY_MAP.get(height, DEFAULT_QUALITY)
    remaining = (text[: m.start()] + " " + text[m.end():]).strip()
    return remaining, quality


# ── File size limit ────────────────────────────────────────────────────────────

MAX_FILE_SIZE_MB = env_int("MAX_FILE_SIZE_MB", 0)  # 0 = unlimited


def check_size_limit(size_bytes: int) -> str | None:
    """
    Return a user-facing rejection message when size_bytes exceeds
    MAX_FILE_SIZE_MB, else None. Disabled when MAX_FILE_SIZE_MB is 0.
    """
    if MAX_FILE_SIZE_MB <= 0 or size_bytes <= 0:
        return None
    if size_bytes > MAX_FILE_SIZE_MB * 1024 * 1024:
        return (
            f"❌ File too large: **{format_size(size_bytes)}** "
            f"(limit: **{MAX_FILE_SIZE_MB} MB**).\n\n"
            f"Ask the bot admin to raise `MAX_FILE_SIZE_MB` if you need bigger files."
        )
    return None


# ── Batch download cap ─────────────────────────────────────────────────────────

MAX_LINKS_PER_MESSAGE = env_int("MAX_LINKS_PER_MESSAGE", 5)


def cap_links(urls: list[str]) -> tuple[list[str], int]:
    """
    Cap the number of links processed per message.

    Returns (kept_urls, dropped_count).
    """
    if MAX_LINKS_PER_MESSAGE <= 0 or len(urls) <= MAX_LINKS_PER_MESSAGE:
        return urls, 0
    return urls[:MAX_LINKS_PER_MESSAGE], len(urls) - MAX_LINKS_PER_MESSAGE