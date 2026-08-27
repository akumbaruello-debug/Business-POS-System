"""Request-ID middleware and request context.

Provides:
* ``X-Request-ID`` echo + generation.
* A ``ContextVar`` (``current_request_id``) that all downstream code
  (including the error handlers and structlog processors) can read so
  every log line and every error envelope carries the same correlation
  token.

Reference: ``Backend-Architecture-V1.0.md`` §4 Logging, §19 Error
Architecture.
"""

from __future__ import annotations

import contextvars
import logging
import uuid
from typing import Final

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.ids import new_request_id

# Use stdlib logging here — this module is imported very early in the
# dependency chain (before structlog is configured). Calling
# ``get_logger`` here would create a circular import.
logger = logging.getLogger(__name__)

#: ContextVar holding the active request UUID. Set on entry to every
#: request; read by error handlers, structlog processors, and
#: middleware.
current_request_id: contextvars.ContextVar[uuid.UUID | None] = contextvars.ContextVar(
    "request_id",
    default=None,
)

#: The response header name.
X_REQUEST_ID_HEADER: Final[str] = "X-Request-ID"


class RequestIDMiddleware:
    """Generate or echo the request id, stash it in scope + ContextVar.

    * If the incoming request has a valid ``X-Request-ID`` header (a
      UUID), we reuse it. Otherwise we generate a fresh UUID4.
    * The value is put into ``scope['request_id']`` (as a string, for
      middleware that inspects scope) **and** into the ContextVar
      (so sync / async code without access to the scope can read it).
    * The same value is echoed back in the response.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = scope["headers"]
        existing = None
        for raw_name, raw_val in incoming:
            if raw_name == b"x-request-id":
                try:
                    candidate = uuid.UUID(raw_val.decode())
                except (ValueError, UnicodeDecodeError):
                    # Not a valid UUID — ignore it, generate fresh.
                    continue
                existing = candidate
                break  # first valid header wins

        rid = existing or new_request_id()

        # Store for scope-bound readers
        scope["request_id"] = str(rid)

        # Store for thread/coroutine-bound readers (structlog etc.)
        token = current_request_id.set(rid)

        async def _send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.get("headers", [])
                # Echo back
                headers.append((X_REQUEST_ID_HEADER.encode(), str(rid).encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, _send_wrapper)
        finally:
            current_request_id.reset(token)


def get_request_id() -> uuid.UUID:
    """Return the current request's id, or generate one if absent."""
    rid = current_request_id.get()
    if rid is None:
        return new_request_id()
    return rid
