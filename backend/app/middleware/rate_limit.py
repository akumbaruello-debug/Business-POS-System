"""Rate limiting integration (slowapi).

M1 wires slowapi as a stateful limiter keyed by client IP. The full
rate-limit policy is per-endpoint and live in the route decorators
themselves (added in M2+). This module just provides the slowapi
setup + the error-handler integration that converts a
``RateLimitExceeded`` into our canonical ``ErrorEnvelope``.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.core.ids import new_request_id
from app.errors.codes import ErrorCode
from app.logging import get_logger
from app.middleware.request_id import current_request_id

logger = get_logger(__name__)

# Module-level singleton so route decorators can find the limiter.
_limiter: Limiter | None = None


def get_limiter() -> Limiter:
    """Return the process-wide slowapi limiter (initialised by ``init``)."""
    global _limiter
    if _limiter is None:
        _limiter = Limiter(key_func=get_remote_address)
    return _limiter


def init_rate_limit(app: FastAPI) -> None:
    """Attach the slowapi exception handler to the app."""
    limiter = get_limiter()
    app.state.limiter = limiter  # for decorators later
    app.exception_handler(RateLimitExceeded)(_on_rate_limit)


async def _on_rate_limit(request: Any, exc: RateLimitExceeded) -> JSONResponse:
    """Return canonical 429 envelope for rate-limit-exceeded."""
    rid = current_request_id.get() or new_request_id()
    get_logger("app").info("rate_limit_exceeded", path=request.url.path)
    return JSONResponse(
        status_code=429,
        content={
            "error": {
                "code": ErrorCode.RATE_LIMITED.value,
                "message": "Too many requests. Please slow down.",
                "details": {"limit": str(exc.detail)},
                "request_id": str(rid),
            },
        },
    )


__all__ = ["_on_rate_limit", "get_limiter", "init_rate_limit"]
