"""
flareDL/errors.py
~~~~~~~~~~~~~~~~~
Exception classes for the Flare / CashSnap downloader engine.
"""

class FlareError(Exception):
    """Base exception for all Flare downloader errors."""
    pass


class FlareDirectError(FlareError):
    """Raised when a Flare link is expired, deleted, invalid, or violates content policy."""
    pass
