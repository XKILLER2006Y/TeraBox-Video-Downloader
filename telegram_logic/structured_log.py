"""
Zero-dependency structured logging for the TeraBox bot.

Provides:
  - contextvars-based request context (request_id, user_id, download_id)
  - JsonFormatter for file output (machine-readable)
  - Human-readable console output preserved
  - ContextLogger wrapper that auto-injects context

Usage:
    from telegram_logic.structured_log import setup_logging, ctx_logger

    # once at startup
    setup_logging()

    # anywhere — context auto-attached from current scope
    log = ctx_logger(__name__)
    log.info("download started", extra={"surl": "abc123"})
"""
from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
import uuid
from datetime import datetime, timezone
from typing import Any

# ── Context variables ─────────────────────────────────────────────────────────
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
user_id_var: contextvars.ContextVar[int | None] = contextvars.ContextVar("user_id", default=None)
download_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("download_id", default="")


def new_request_id() -> str:
    """Generate a short unique request ID (8 chars)."""
    return uuid.uuid4().hex[:8]


def bind_context(*, request_id: str | None = None, user_id: int | None = None,
                 download_id: str | None = None, chat_id: int | None = None,
                 link_id: str | None = None, **kwargs: Any) -> dict[str, Any]:
    """Set context vars and return a dict of the set values (for logging)."""
    tokens = {}
    if request_id is not None:
        tokens["request_id"] = request_id_var.set(request_id)
    uid = user_id if user_id is not None else chat_id
    if uid is not None:
        tokens["user_id"] = user_id_var.set(uid)
    did = download_id if download_id is not None else link_id
    if did is not None:
        tokens["download_id"] = download_id_var.set(did)
    return tokens


def get_context() -> dict[str, Any]:
    """Read current context vars (returns only non-empty values)."""
    ctx: dict[str, Any] = {}
    rid = request_id_var.get("")
    if rid:
        ctx["request_id"] = rid
    uid = user_id_var.get(None)
    if uid is not None:
        ctx["user_id"] = uid
    did = download_id_var.get("")
    if did:
        ctx["download_id"] = did
    return ctx


# ── Context filter ────────────────────────────────────────────────────────────
class ContextFilter(logging.Filter):
    """Inject request context into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        ctx = get_context()
        for k, v in ctx.items():
            setattr(record, k, v)
        # Ensure 'message' key exists for JSON
        if not hasattr(record, "message"):
            record.message = record.getMessage()
        return True


# ── JSON formatter ────────────────────────────────────────────────────────────
class JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line.

    Fields: ts, level, logger, msg, [request_id, user_id, download_id], [exc], [extra...]
    """

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }

        # Context fields (only if present)
        for field in ("request_id", "user_id", "download_id"):
            val = getattr(record, field, None)
            if val:
                entry[field] = val

        # Module location
        entry["src"] = f"{record.module}:{record.lineno}"

        # Exception info
        if record.exc_info and record.exc_info[0] is not None:
            entry["exc"] = self.formatException(record.exc_info)

        # Extra fields from log calls (log.info("...", extra={"key": "val"}))
        SKIP = {
            "name", "msg", "args", "created", "relativeCreated", "exc_info",
            "exc_text", "stack_info", "lineno", "funcName", "pathname", "filename",
            "module", "levelname", "levelno", "msecs", "message", "thread",
            "threadName", "processName", "process", "taskName",
            "request_id", "user_id", "download_id",
        }
        for key in list(record.__dict__):
            if key.startswith("_") or key in SKIP:
                continue
            if hasattr(record, key):
                val = getattr(record, key)
                if isinstance(val, (str, int, float, bool, list, dict, type(None))):
                    entry[key] = val

        return json.dumps(entry, default=str, ensure_ascii=False)


# ── Context logger wrapper ────────────────────────────────────────────────────
class ContextLogger(logging.Logger):
    """Logger subclass that binds context from current scope to every call."""

    def _extra_with_ctx(self, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        merged = get_context()
        if extra:
            merged.update(extra)
        return merged

    def debug(self, msg: str, *args: Any, extra: dict[str, Any] | None = None, **kw: Any) -> None:
        super().debug(msg, *args, extra=self._extra_with_ctx(extra), **kw)

    def info(self, msg: str, *args: Any, extra: dict[str, Any] | None = None, **kw: Any) -> None:
        super().info(msg, *args, extra=self._extra_with_ctx(extra), **kw)

    def warning(self, msg: str, *args: Any, extra: dict[str, Any] | None = None, **kw: Any) -> None:
        super().warning(msg, *args, extra=self._extra_with_ctx(extra), **kw)

    def error(self, msg: str, *args: Any, extra: dict[str, Any] | None = None, **kw: Any) -> None:
        super().error(msg, *args, extra=self._extra_with_ctx(extra), **kw)

    def exception(self, msg: str, *args: Any, extra: dict[str, Any] | None = None, **kw: Any) -> None:
        super().exception(msg, *args, extra=self._extra_with_ctx(extra), **kw)


# ── Logger registry ───────────────────────────────────────────────────────────
_CTX_LOGGER_NAMES: set[str] = set()


def ctx_logger(name: str) -> ContextLogger:
    """Get (or create) a ContextLogger with the given name."""
    if name not in _CTX_LOGGER_NAMES:
        # Ensure the logger class is ContextLogger
        old_class = logging.getLoggerClass()
        logging.setLoggerClass(ContextLogger)
        logging.getLogger(name)
        logging.setLoggerClass(old_class)
        _CTX_LOGGER_NAMES.add(name)
    return logging.getLogger(name)  # type: ignore[return-value]


# ── Setup ─────────────────────────────────────────────────────────────────────
def setup_logging(
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
    log_file: str = "bot.log",
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 2,
) -> None:
    """Configure structured logging for the bot.

    Console: human-readable (same as before)
    File: JSON lines (machine-readable)
    """
    root = logging.getLogger()
    root.setLevel(min(console_level, file_level))

    # Clear existing handlers (avoid duplicates on reload)
    root.handlers.clear()

    # Add context filter to root
    ctx_filter = ContextFilter()
    root.addFilter(ctx_filter)

    # ── Console handler (human-readable) ──────────────────────────────────
    console = logging.StreamHandler()
    console.setLevel(console_level)
    console.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    console.addFilter(ctx_filter)
    root.addHandler(console)

    # ── File handler (JSON lines) ─────────────────────────────────────────
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    file_handler.setLevel(file_level)
    file_handler.setFormatter(JsonFormatter())
    file_handler.addFilter(ctx_filter)
    root.addHandler(file_handler)

    # Silence noisy libraries
    for noisy in ("httpx", "httpcore", "telethon", "urllib3", "asyncio",
                  "uvicorn.access", "uvicorn.error", "firebase_admin"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
