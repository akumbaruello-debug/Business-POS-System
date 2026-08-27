"""Health and readiness probes.

Endpoints (per Backend-Architecture-V1.0.md §24 Observability):

* ``/livez`` — liveness. Always 200 if the process is up. No DB call.
* ``/readyz`` — readiness. Returns 200 only if the DB is reachable
  and a trivial query succeeds.
* ``/healthz`` — alias of ``/readyz`` (common convention).

These endpoints are intentionally simple — no auth, no rate limit, no
body, minimal payload. They MUST be cheap and not block.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.db import get_engine
from app.logging import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["health"])


@router.get("/livez", summary="Liveness probe")
async def liveness() -> dict[str, str]:
    """Return 200 if the process is alive. No I/O."""
    return {"status": "ok"}


@router.get("/healthz", summary="Health/readiness probe (alias of /readyz)", response_model=None)
async def healthz() -> dict[str, str] | JSONResponse:
    """Same as /readyz. Common convention for load balancers."""
    return await _ready()


@router.get("/readyz", summary="Readiness probe", response_model=None)
async def readyz() -> dict[str, str] | JSONResponse:
    """200 if the database is reachable and SELECT 1 works."""
    return await _ready()


async def _ready() -> dict[str, str] | JSONResponse:
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            row = (await conn.execute(text("SELECT 1 AS ok"))).scalar()
        if row != 1:
            raise RuntimeError("SELECT 1 returned unexpected result")
        return {"status": "ready", "database": "ok"}
    except Exception as exc:
        logger.warning("readiness_check_failed", error=str(exc))
        # Use a 503-style response (still an ErrorEnvelope per the
        # contract, raised via a small helper).
        from app.core.ids import new_request_id
        from app.errors.codes import ErrorCode
        from app.middleware.request_id import current_request_id

        rid: Any = current_request_id.get() or new_request_id()
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": ErrorCode.SERVICE_UNAVAILABLE.value,
                    "message": "Service is not ready.",
                    "details": {"database": "unreachable"},
                    "request_id": str(rid),
                },
            },
        )


__all__ = ["get_engine", "router"]
