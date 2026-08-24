"""
Canonical exception hierarchy for all downloader modules.

Every resolver/downloader raises one of these; the Telegram pipelines catch
the base classes and render user-friendly messages.

Hierarchy
---------
DownloadError                     — base for all known download failures
├── TeraBoxError                  — generic TeraBox failure
│   └── TeraBoxDirectError        — direct-resolution failure (API/HTML)
│       └── TeraBoxRateLimited    — 429 / rate-limit errno (triggers cookie rotation)
└── DiskwalaError                 — generic Diskwala failure
    └── DiskwalaDirectError       — direct Mini-App resolution failure

CancelledError is intentionally NOT a DownloadError: cancellation is a
normal control-flow signal, not a failure.
"""


class DownloadError(Exception):
    """Base class for known, expected download failures."""


class CancelledError(Exception):
    """Raised when a download is cancelled by the user."""


# ── TeraBox ────────────────────────────────────────────────────────────────────────

class TeraBoxError(DownloadError):
    """Raised for known, expected TeraBox errors."""


class TeraBoxDirectError(TeraBoxError):
    """Raised when TeraBox direct resolution fails with a known error."""


class TeraBoxRateLimited(TeraBoxDirectError):
    """
    Raised on HTTP 429 or rate-limit errnos from TeraBox.

    The metadata resolver treats this specially: it marks the current cookie
    as rate-limited and retries with the next one in the pool.
    """


class TeraBoxMultipleChoice(TeraBoxError):
    """
    Control-flow signal (not a failure): the share holds several files and
    the caller didn't say which one it wants. Carries a brief file list for
    the picker UI: [{"fs_id","name","size","is_video"}, ...]
    """

    def __init__(self, files: list):
        self.files = files
        super().__init__(f"share contains {len(files)} files")


# ── Diskwala ───────────────────────────────────────────────────────────────────────

class DiskwalaError(DownloadError):
    """Raised for known, expected Diskwala errors."""


class DiskwalaDirectError(DiskwalaError):
    """Raised when Diskwala direct (Mini App) resolution fails."""
