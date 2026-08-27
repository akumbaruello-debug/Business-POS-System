"""Health probe tests.

Covers:
* /livez — always 200
* /readyz — DB check
* /healthz — alias of /readyz
* Service unavailable when DB unreachable
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.errors.codes import ErrorCode


class TestHealthProbes:
    """Tests for liveness / readiness / health endpoints."""

    async def test_livez_returns_ok(self, app: AsyncClient) -> None:
        """/livez returns 200 with status ok, no DB call."""
        r = await app.get("/livez")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"

    async def test_healthz_is_readyz_alias(self, app: AsyncClient) -> None:
        """/healthz aliases /readyz."""
        r1 = await app.get("/healthz")
        r2 = await app.get("/readyz")
        # Both should return the same status code (200 or 503)
        assert r1.status_code == r2.status_code

    async def test_readyz_returns_ready_when_db_ok(self, app: AsyncClient) -> None:
        """/readyz returns 200 with database ok when DB is reachable."""
        r = await app.get("/readyz")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ready"
        assert body["database"] == "ok"

    async def test_readyz_returns_503_when_db_down(
        self, app: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """/readyz returns 503 SERVICE_UNAVAILABLE when DB is unreachable."""
        # Monkeypatch get_engine to return a broken engine
        from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

        from app import health

        original = health.get_engine

        def _broken_engine() -> AsyncEngine:
            # Create a real engine pointed at a non-existent DB
            bad: AsyncEngine = create_async_engine(
                "postgresql+asyncpg://postgres@127.0.0.1:5433/nonexistent_db_xyz"
            )
            return bad

        monkeypatch.setattr(health, "get_engine", _broken_engine)
        try:
            r = await app.get("/readyz")
            assert r.status_code == 503
            body = r.json()
            err = body["error"]
            assert err["code"] == ErrorCode.SERVICE_UNAVAILABLE.value
            assert "database" in err["details"]
        finally:
            monkeypatch.setattr(health, "get_engine", original)
