class FlezenError(Exception):
    """Base exception for Flezen errors."""
    pass


class FlezenDirectError(FlezenError):
    """Direct, user-facing error for Flezen links (e.g. expired, deleted, private)."""
    pass
