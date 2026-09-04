"""Request ID + middleware tests.

Covers:
* Request ID generated when absent
* X-Request-ID echoed / preserved
* Request ID bound to context
* Security response headers
* CORS behavior
* Origin header check on state-changing requests
* Rate limit handler (canonical 429 envelope)
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.asyncio


class TestRequestID:
    """Tests for X-Request-ID generation + echo."""

    async def test_request_id_generated_when_absent(self, app: AsyncClient) -> None:
        """No X-Request-ID -> server generates one and echoes it back."""
        r = await app.get("/livez")
        rid = r.headers.get("X-Request-ID")
        assert rid is not None
        # Must be a valid UUID
        parsed = uuid.UUID(rid)
        assert str(parsed) == rid

    async def test_request_id_echoed_when_present(self, app: AsyncClient) -> None:
        """Valid X-Request-ID is preserved."""
        my_rid = "123e4567-e89b-12d3-a456-426614174000"
        r = await app.get("/livez", headers={"X-Request-ID": my_rid})
        assert r.headers.get("X-Request-ID") == my_rid

    async def test_invalid_request_id_regenerated(self, app: AsyncClient) -> None:
        """Invalid X-Request-ID (not a UUID) -> server generates fresh."""
        r = await app.get("/livez", headers={"X-Request-ID": "not-a-uuid"})
        rid = r.headers.get("X-Request-ID")
        assert rid is not None
        uuid.UUID(rid)  # raises ValueError if invalid

    async def test_request_id_in_error_envelope(self, app: AsyncClient) -> None:
        """Error responses carry the request_id that was echoed."""
        my_rid = "123e4567-e89b-12d3-a456-426614174000"
        r = await app.get(
            "/api/v1/auth/me",
            headers={"X-Request-ID": my_rid},
        )
        assert r.status_code == 401
        body = r.json()
        assert body["error"]["request_id"] == my_rid


class TestSecurityHeaders:
    """Tests for security response headers."""

    async def test_security_headers_present(self, app: AsyncClient) -> None:
        """Core secure headers are present on all responses."""
        r = await app.get("/livez")
        assert r.headers.get("X-Content-Type-Options") == "nosniff"
        assert r.headers.get("X-Frame-Options") == "DENY"
        assert r.headers.get("Referrer-Policy") == "no-referrer"
        assert r.headers.get("X-XSS-Protection") == "0"
        assert "geolocation" in r.headers.get("Permissions-Policy", "")
        assert "microphone" in r.headers.get("Permissions-Policy", "")
        assert "camera" in r.headers.get("Permissions-Policy", "")

    async def test_security_headers_on_error(self, app: AsyncClient) -> None:
        """Security headers are present even on error responses."""
        r = await app.get("/api/v1/auth/me")
        assert r.headers.get("X-Content-Type-Options") == "nosniff"
        assert r.headers.get("X-Frame-Options") == "DENY"
        assert r.headers.get("Referrer-Policy") == "no-referrer"
        assert r.headers.get("X-XSS-Protection") == "0"


    async def test_proxy_owned_headers_absent(self, app: AsyncClient) -> None:
        """CSP and HSTS are reverse-proxy responsibilities; the app must not set them.

        Per Backend-Architecture-V1.0.md §23.2, the API serves JSON and
        those headers would conflict with the proxy's configuration.
        """
        r = await app.get("/livez")
        assert "content-security-policy" not in r.headers
        assert "strict-transport-security" not in r.headers


class TestCORS:
    """Tests for CORS behavior per the configured policy."""

    async def test_no_cors_by_default(self, app: AsyncClient) -> None:
        """No CORS headers when no origins configured (dev default)."""
        r = await app.options(
            "/livez",
            headers={
                "Origin": "https://malicious.com",
                "Access-Control-Request-Method": "GET",
            },
        )
        # CORS middleware is not installed when cors_origins is empty
        assert "access-control-allow-origin" not in r.headers

    async def test_cors_allows_configured_origin(self, owner_user: dict[str, Any]) -> None:
        """When a CORS origin is configured, it's allowed."""
        from app.config import get_settings
        from app.config.env import load_env
        from app.main import create_app

        os.environ["POS_CORS_ALLOWED_ORIGINS"] = "https://app.example.com"
        try:
            load_env(force_reload=True)
            fresh_app = create_app(get_settings())
            async with AsyncClient(
                transport=ASGITransport(app=fresh_app),
                base_url="http://test",
            ) as client:
                r = await client.options(
                    "/livez",
                    headers={
                        "Origin": "https://app.example.com",
                        "Access-Control-Request-Method": "GET",
                    },
                )
                assert r.status_code == 200
                assert r.headers.get("access-control-allow-origin") == "https://app.example.com"
        finally:
            del os.environ["POS_CORS_ALLOWED_ORIGINS"]
            load_env(force_reload=True)

    async def test_cors_rejects_unconfigured_origin(self, owner_user: dict[str, Any]) -> None:
        """When a CORS origin is configured, unlisted origins are rejected."""
        from app.config import get_settings
        from app.config.env import load_env
        from app.main import create_app

        os.environ["POS_CORS_ALLOWED_ORIGINS"] = "https://app.example.com"
        try:
            load_env(force_reload=True)
            fresh_app = create_app(get_settings())
            async with AsyncClient(
                transport=ASGITransport(app=fresh_app),
                base_url="http://test",
            ) as client:
                r = await client.options(
                    "/livez",
                    headers={
                        "Origin": "https://evil.com",
                        "Access-Control-Request-Method": "GET",
                    },
                )
                # Should NOT echo the origin
                allow = r.headers.get("access-control-allow-origin")
                assert allow != "https://evil.com"
        finally:
            del os.environ["POS_CORS_ALLOWED_ORIGINS"]
            load_env(force_reload=True)


class TestOriginCheck:
    """Tests for OriginCheckMiddleware: rejects missing/unlisted origins on state-changing requests."""

    async def test_origin_check_allows_configured_origin(self):
        """With cors_origins set, a state-changing request with allowed Origin passes."""
        from app.config import get_settings
        from app.config.env import load_env
        from app.main import create_app

        os.environ["POS_CORS_ALLOWED_ORIGINS"] = "https://app.example.com"
        try:
            load_env(force_reload=True)
            fresh_app = create_app(get_settings())
            async with AsyncClient(
                transport=ASGITransport(app=fresh_app),
                base_url="http://test",
            ) as client:
                r = await client.post(
                    "/api/v1/auth/login",
                    json={"username": "owner", "password": "password"},
                    headers={"Origin": "https://app.example.com"},
                )
                # Should not get 403; auth will fail with 400/401 because credentials are invalid.
                assert r.status_code != 403
        finally:
            del os.environ["POS_CORS_ALLOWED_ORIGINS"]
            load_env(force_reload=True)

    async def test_origin_check_rejects_unconfigured_origin(self):
        """Unlisted origin -> 403 origin_not_allowed."""
        from app.config import get_settings
        from app.config.env import load_env
        from app.main import create_app

        os.environ["POS_CORS_ALLOWED_ORIGINS"] = "https://app.example.com"
        try:
            load_env(force_reload=True)
            fresh_app = create_app(get_settings())
            async with AsyncClient(
                transport=ASGITransport(app=fresh_app),
                base_url="http://test",
            ) as client:
                r = await client.post(
                    "/api/v1/auth/login",
                    json={"username": "owner", "password": "password"},
                    headers={"Origin": "https://evil.com"},
                )
                assert r.status_code == 403
                body = r.json()
                assert body["error"]["code"] == "origin_not_allowed"
                assert "request_id" in body["error"]
        finally:
            del os.environ["POS_CORS_ALLOWED_ORIGINS"]
            load_env(force_reload=True)

    async def test_origin_check_rejects_missing_origin(self):
        """No Origin header -> 403 origin_not_allowed."""
        from app.config import get_settings
        from app.config.env import load_env
        from app.main import create_app

        os.environ["POS_CORS_ALLOWED_ORIGINS"] = "https://app.example.com"
        try:
            load_env(force_reload=True)
            fresh_app = create_app(get_settings())
            async with AsyncClient(
                transport=ASGITransport(app=fresh_app),
                base_url="http://test",
            ) as client:
                r = await client.post(
                    "/api/v1/auth/login",
                    json={"username": "owner", "password": "password"},
                )
                assert r.status_code == 403
                body = r.json()
                assert body["error"]["code"] == "origin_not_allowed"
        finally:
            del os.environ["POS_CORS_ALLOWED_ORIGINS"]
            load_env(force_reload=True)

    async def test_origin_check_skipped_when_no_cors_configured(self):
        """When cors_origins empty, no origin check; request proceeds."""
        from app.config import get_settings
        from app.config.env import load_env
        from app.main import create_app

        if "POS_CORS_ALLOWED_ORIGINS" in os.environ:
            del os.environ["POS_CORS_ALLOWED_ORIGINS"]
        load_env(force_reload=True)
        fresh_app = create_app(get_settings())
        async with AsyncClient(
            transport=ASGITransport(app=fresh_app),
            base_url="http://test",
        ) as client:
            r = await client.post(
                "/api/v1/auth/login",
                json={"username": "owner", "password": "password"},
                headers={"Origin": "https://evil.com"},
            )
            # Should not be 403; likely 400/401 due to invalid creds
            assert r.status_code != 403


class TestRateLimiting:
    """Tests for the rate-limit handler producing canonical 429 envelope."""

    async def test_rate_limit_exceeded_returns_429(self) -> None:
        """When rate limit is exceeded, produce canonical 429 envelope.

        Tests the production rate-limit handler by wiring slowapi properly
        (SlowAPIMiddleware + app.state.limiter) and using the real
        production exception handler from app.middleware.rate_limit.
        """
        from fastapi import FastAPI
        from slowapi import Limiter
        from slowapi.errors import RateLimitExceeded
        from slowapi.middleware import SlowAPIMiddleware
        from slowapi.util import get_remote_address

        from app.middleware.rate_limit import _on_rate_limit

        limiter = Limiter(key_func=get_remote_address, default_limits=["1/minute"])
        minimal = FastAPI()
        minimal.state.limiter = limiter
        minimal.add_middleware(SlowAPIMiddleware)
        minimal.exception_handler(RateLimitExceeded)(_on_rate_limit)

        @minimal.get("/limited")
        @limiter.limit("1/minute")
        async def _limited(request: Request) -> dict[str, bool]:
            return {"ok": True}

        async with AsyncClient(
            transport=ASGITransport(app=minimal),
            base_url="http://test",
        ) as client:
            # First call OK
            r1 = await client.get("/limited")
            assert r1.status_code == 200, r1.text
            # Second should be rate limited
            r2 = await client.get("/limited")
            assert r2.status_code == 429, r2.text
            body = r2.json()
            assert "error" in body
            assert body["error"]["code"] == "rate_limited"
            assert body["error"]["message"]
            assert body["error"]["request_id"]
