"""Rate limiting integration (slowapi).

M1 wires slowapi as a stateful limiter keyed by client IP. The full
rate-limit policy is per-endpoint and live in the route decorators
themselves (added in M2+). This module just provides the slowapi
setup + the error-handler integration that converts a
``RateLimitExceeded`` into our canonical ``ErrorEnvelope``.

P0 #2 — Hybrid / Dependency-Based Rate Limiting:
This module also exposes ``check_limit`` and ``check_rate_limits``
helpers that route through the same singleton slowapi limiter, so
authenticated user limits can be enforced from a FastAPI dependency
(after ``current_principal`` resolves the principal) while anonymous
IP-based limits keep using slowapi decorators.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from limits import parse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from slowapi.wrappers import Limit as SlowapiLimit
from starlette.requests import Request

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


async def check_limit(limit_str: str, key: str, request: Request) -> None:
    """Check a single rate limit; raise ``RateLimitExceeded`` if exceeded.

    Routes through the existing singleton slowapi limiter so storage and
    configuration are shared with the decorator-based limits. ``key`` is
    the bucket identifier (IP, ``user:<id>``, or login username).
    """
    limiter = get_limiter()
    limit = parse(limit_str)
    if not limiter.limiter.hit(limit, key):
        # Construct a proper SlowapiLimit so RateLimitExceeded gets the
        # expected type and the exception handler can render ``details.limit``.
        raise RateLimitExceeded(
            SlowapiLimit(
                limit=limit,
                key_func=get_remote_address,
                scope=None,
                per_method=False,
                methods=None,
                error_message=limit_str,
                exempt_when=None,
                cost=1,
                override_defaults=False,
            )
        )


async def check_rate_limits(request: Request, user_id: int) -> None:
    """Apply authenticated-user rate limits (after principal resolution).

    Policy per Backend-Architecture-V1.0.md §23.4 / API-Architecture §13.6:
    * GET/read: 600/minute/user
    * exports POST: 5/hour/user + 20/day/user
    * other mutations: 60/minute/user + 600/minute/IP
    """
    key = f"user:{user_id}"
    ip_key = get_remote_address(request)

    method = request.method.upper()
    path = request.url.path

    if method == "GET" and not path.startswith("/api/v1/exports"):
        await check_limit("600/minute", key, request)
    elif method in ("POST", "PUT", "PATCH", "DELETE"):
        if path.startswith("/api/v1/exports"):
            await check_limit("5/hour", key, request)
            await check_limit("20/day", key, request)
        else:
            await check_limit("60/minute", key, request)
            await check_limit("600/minute", ip_key, request)


__all__ = [
    "_on_rate_limit",
    "check_limit",
    "check_rate_limits",
    "get_limiter",
    "init_rate_limit",
]
