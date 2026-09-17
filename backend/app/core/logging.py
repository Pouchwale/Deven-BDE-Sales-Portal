"""Logging for the whole backend.

Other modules only ever need::

    from app.core.logging import get_logger
    log = get_logger(__name__)
    log.warning("SAP sync failed", extra={"file": name})

`configure_logging()` is called once by app.main. It installs a single
handler on the root logger: JSON lines in production (LOG_FORMAT=json), a
readable one-liner in development. Every record carries the current
request id (from a context variable set by the request middleware), so a
failure deep inside a service can be tied back to the request that caused it.

Never log secrets, request bodies, query strings, Authorization or Cookie
headers. The helpers here do not try to scrub arbitrary text - they rely on
callers not passing such values in the first place.
"""
from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

#: The id of the request being handled on this task/thread, or "-" outside one.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

# Attributes every LogRecord has; anything else was passed through `extra=`.
_STANDARD_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()
) | {"message", "asctime", "request_id", "taskName"}

_HANDLER_MARK = "_bde_portal_handler"


def get_logger(name: str) -> logging.Logger:
    """A standard library logger. Safe to call at import time."""
    return logging.getLogger(name)


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get()
        return True


def _extras(record: logging.LogRecord) -> dict[str, Any]:
    return {
        key: value
        for key, value in vars(record).items()
        if key not in _STANDARD_ATTRS and not key.startswith("_")
    }


class JsonFormatter(logging.Formatter):
    """One JSON object per line; extra= fields become top-level keys."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        for key, value in _extras(record).items():
            payload.setdefault(key, value)
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__(
            "%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s"
        )

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        extras = _extras(record)
        if extras:
            fields = " ".join(f"{k}={v}" for k, v in extras.items())
            # Tracebacks are appended by the base class after the message.
            head, sep, tail = text.partition("\n")
            text = f"{head} {fields}{sep}{tail}"
        return text


def configure_logging(level: str = "INFO", fmt: str = "text") -> None:
    """Install (or replace) the portal's handler on the root logger.

    Idempotent: calling it again - uvicorn --reload re-imports app.main -
    swaps the handler rather than stacking a second one.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    setattr(handler, _HANDLER_MARK, True)
    handler.addFilter(_RequestIdFilter())
    handler.setFormatter(JsonFormatter() if fmt.lower() == "json" else TextFormatter())
    root.addHandler(handler)
    root.setLevel((level or "INFO").upper())

    # Uvicorn's own access log would duplicate the request middleware's line
    # (and it includes the raw query string). Run uvicorn with
    # --no-access-log; this keeps it quiet even when someone forgets.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    # httpx logs every outbound URL at INFO (the Groq client is built on it).
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
