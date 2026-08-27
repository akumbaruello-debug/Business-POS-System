"""Structured logging configuration (structlog + stdlib).

Reference: ``Backend-Architecture-V1.0.md`` §4 Logging, §24 Observability.

Design goals
------------
1. **JSON output by default** (``log_format = "json"``), human-readable
   on the console for local dev (``log_format = "console"``).
2. **Request correlation:** every log record emitted during a request
   inherits the ``request_id`` ContextVar so a single processor injects
   ``request_id`` into every log line.
3. **Security:** secrets are never logged. The structured fields use
   a redaction filter so password / token values cannot leak via
   automatic dict rendering.
4. **No performance surprises:** processors are pre-rendered only at
   the terminal sink (JSONRenderer); structured fields remain dicts
   until the last millisecond.
"""

from __future__ import annotations

import logging
import logging.config
import sys
from typing import Any, cast

import structlog
from structlog.contextvars import (
    bind_contextvars,
    clear_contextvars,
    merge_contextvars,
)

from app.core.ids import new_request_id
from app.middleware.request_id import current_request_id

# ---------------------------------------------------------------------------
# Common processors
# ---------------------------------------------------------------------------

_SHARED_PROCESSORS: list[Any] = [
    # Inject contextvar-based merge so `bind_contextvars` / the
    # request-id ContextVar show up automatically.
    merge_contextvars,
    # Add the log level (INFO, ERROR, etc.) as ``"level"``.
    structlog.processors.add_log_level,
    # Add timestamps in ISO 8601 with timezone (UTC).
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    # If a field's value is an exception, render it as a traceback.
    structlog.processors.format_exc_info,
    # Render stack-info for stack_info() calls.
    structlog.processors.StackInfoRenderer(),
]

# The final processor differs by format:
_JSON_RENDERER = structlog.processors.JSONRenderer()
_CONSOLE_RENDERER = structlog.dev.ConsoleRenderer(
    colors=True,
    sort_keys=True,
)


class RequestIDAdder:
    """structlog processor: inject ``request_id`` from the ContextVar.

    If the request_id ContextVar is unset (e.g. for startup logs) we
    generate one so every log line is traceable.
    """

    def __call__(
        self,
        _logger: logging.Logger,
        _name: str,
        event_dict: dict[str, Any],
    ) -> dict[str, Any]:
        rid = current_request_id.get()
        if rid is None:
            rid = new_request_id()
        event_dict["request_id"] = str(rid)
        return event_dict


# Fields that must never appear in a log payload (security filter).
_REDACTED_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "password_hash",
        "access_token",
        "refresh_token",
        "secret",
        "api_key",
        "token",
        "authorization",
        "cookie",
        "old_password",
        "new_password",
        "current_password",
    }
)


class SensitiveFilter:
    """Redact known-sensitive keys from log event dicts (security)."""

    def __call__(
        self,
        _logger: logging.Logger,
        _name: str,
        event_dict: dict[str, Any],
    ) -> dict[str, Any]:
        for key in list(event_dict.keys()):
            if key.lower() in _REDACTED_KEYS:
                event_dict[key] = "[REDACTED]"
        return event_dict


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------


def configure_logging(
    *,
    log_level: str = "INFO",
    log_format: str = "json",
) -> None:
    """Configure structlog + stdlib logging.

    Call this once at app startup (and at the start of tests).
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Build the processor chain.
    renderer = _JSON_RENDERER if log_format == "json" else _CONSOLE_RENDERER
    processors: list[Any] = [
        *_SHARED_PROCESSORS,
        SensitiveFilter(),
        RequestIDAdder(),
        renderer,
    ]

    # structlog global config.
    structlog.configure(
        processors=processors,
        # Use the standard logging Logger so we bridge into stdlib
        # (uvicorn, slowapi, sqlalchemy all emit via stdlib).
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        # Cache the bound logger on the instance so we don't
        # re-bind processors per call.
        cache_logger_on_first_use=True,
    )

    # stdlib root config — uvicorn hooks in here too.
    logging.basicConfig(
        level=level,
        format="%(message)s",  # structlog handles the full render
        stream=sys.stdout,
    )

    # Quiet noisy third-party loggers.
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    # Bind contextvars reset hook for tests that need a clean slate.
    clear_contextvars()

    # Bootstrap message — confirms logging is configured.
    _logger = structlog.get_logger("app")
    _logger.info("logging_configured", level=log_level, format=log_format)


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger bound to ``name``."""
    return cast(
        structlog.stdlib.BoundLogger,
        structlog.get_logger(name) if name else structlog.get_logger(),
    )


def reset_contextvars() -> None:
    """Clear all contextvar bindings (test teardown)."""
    clear_contextvars()


__all__ = [
    "RequestIDAdder",
    "SensitiveFilter",
    "bind_contextvars",
    "clear_contextvars",
    "configure_logging",
    "get_logger",
    "reset_contextvars",
]
