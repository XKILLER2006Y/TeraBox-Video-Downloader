"""
Universal DL router — detects URL type and routes to the correct resolver.

All resolvers return a uniform dict:
    {"filename": str, "size": int, "download_url": str, "headers": dict (optional)}

Supported platforms:
    - TeraBox family (via existing terabox/exp handlers — not routed here)
    - Diskwala (via existing diskwala handler — not routed here)
    - filesadda.site + XFileSharing clones
    - GoFile
    - StreamTape
    - Doodstream (dood.watch/dood.wf/dood.re)
    - MixDrop
    - StreamWish
    - FileLions
    - CatBox (files.catbox.moe)
    - MediaFire
"""
import logging
from typing import Callable

from network import get_session
from filesaddaDL import is_filesadda_url, extract_fileadda_url, resolve_filesadda, FilesAddaError
from gofileDL import is_gofile_url, extract_gofile_url, resolve_gofile, GoFileError
from streamtapeDL import is_streamtape_url, extract_streamtape_url, resolve_streamtape, StreamTapeError
from doodstreamDL import is_dood_url, extract_dood_url, resolve_dood, DoodError
from mixdropDL import is_mixdrop_url, extract_mixdrop_url, resolve_mixdrop, MixDropError
from streamwishDL import is_streamwish_url, extract_streamwish_url, resolve_streamwish, StreamWishError
from filelionsDL import is_filelions_url, extract_filelions_url, resolve_filelions, FileLionsError
from catboxDL import is_catbox_url, extract_catbox_url, resolve_catbox, CatBoxError
from mediafireDL import is_mediafire_url, extract_mediafire_url, resolve_mediafire, MediaFireError

logger = logging.getLogger(__name__)


class UniversalDL(Exception):
    """Base exception for universal DL errors."""


# Router table: (checker, resolver, error_class, display_name)
_ROUTES: list[tuple[Callable[[str], bool], Callable, type, str]] = [
    (is_gofile_url,       resolve_gofile,       GoFileError,       "GoFile"),
    (is_filesadda_url,    resolve_filesadda,    FilesAddaError,    "FilesAdda"),
    (is_dood_url,         resolve_dood,         DoodError,         "Doodstream"),
    (is_streamtape_url,   resolve_streamtape,   StreamTapeError,   "StreamTape"),
    (is_mixdrop_url,      resolve_mixdrop,      MixDropError,      "MixDrop"),
    (is_streamwish_url,   resolve_streamwish,   StreamWishError,   "StreamWish"),
    (is_filelions_url,    resolve_filelions,    FileLionsError,    "FileLions"),
    (is_catbox_url,       resolve_catbox,       CatBoxError,       "CatBox"),
    (is_mediafire_url,    resolve_mediafire,    MediaFireError,    "MediaFire"),
]


def detect_platform(url: str) -> str | None:
    """Detect which platform a URL belongs to. Returns display name or None."""
    for checker, _, _, name in _ROUTES:
        if checker(url):
            return name
    return None


def is_universal_dl_url(url: str) -> bool:
    """Check if URL is supported by any universal DL handler."""
    return any(checker(url) for checker, _, _, _ in _ROUTES)


def extract_universal_urls(text: str) -> list[str]:
    """Extract all universal DL URLs from text."""
    urls = []
    seen = set()
    extractors = [
        extract_fileadda_url, extract_gofile_url, extract_streamtape_url,
        extract_dood_url, extract_mixdrop_url, extract_streamwish_url,
        extract_filelions_url, extract_catbox_url, extract_mediafire_url,
    ]
    for extractor in extractors:
        for u in extractor(text):
            if u not in seen:
                seen.add(u)
                urls.append(u)
    return urls


def resolve_universal(url: str, session=None) -> dict:
    """
    Resolve any supported URL to download info.

    Returns:
        {"filename": str, "size": int, "download_url": str, "headers": dict (optional)}

    Raises:
        UniversalDL: If no handler matches or resolution fails
    """
    sess = session or get_session()
    for checker, resolver, error_cls, name in _ROUTES:
        if checker(url):
            logger.info(f"Routing to {name}: {url}")
            try:
                return resolver(url, session=sess)
            except error_cls as e:
                raise UniversalDL(f"{name} resolution failed: {e}") from e
            except Exception as e:
                raise UniversalDL(f"{name} unexpected error: {e}") from e

    raise UniversalDL(f"No handler found for URL: {url}")
