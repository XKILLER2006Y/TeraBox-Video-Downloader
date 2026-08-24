"""
diskwalaDL/errors.py
~~~~~~~~~~~~~~~~~~~~
Diskwala exception types.

The canonical hierarchy lives in teraboxDL.errors so all downloader packages
share one base class (DownloadError). This module re-exports the Diskwala
branch for convenient, package-local imports.
"""
from teraboxDL.errors import DownloadError, DiskwalaError, DiskwalaDirectError

__all__ = ["DownloadError", "DiskwalaError", "DiskwalaDirectError"]
