"""
flareDL/__init__.py
"""
from flareDL.public_api import (
    get_flare_info,
    download_flare_file,
    extract_flare_id,
    extract_all_flare_urls,
    FLARE_URL_RE,
)
from flareDL.errors import FlareError, FlareDirectError

__all__ = [
    "get_flare_info",
    "download_flare_file",
    "extract_flare_id",
    "extract_all_flare_urls",
    "FLARE_URL_RE",
    "FlareError",
    "FlareDirectError",
]
