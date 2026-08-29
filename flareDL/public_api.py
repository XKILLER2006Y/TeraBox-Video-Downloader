"""
flareDL/public_api.py
~~~~~~~~~~~~~~~~~~~~~
Public interface for the Flare / CashSnap downloader module.
"""

from flareDL.flare_dl import (
    get_flare_info,
    download_flare_file,
    extract_flare_id,
    extract_all_flare_urls,
    FLARE_URL_RE,
)

__all__ = [
    "get_flare_info",
    "download_flare_file",
    "extract_flare_id",
    "extract_all_flare_urls",
    "FLARE_URL_RE",
]
