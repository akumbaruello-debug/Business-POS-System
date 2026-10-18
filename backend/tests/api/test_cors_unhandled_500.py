"""CORS headers on unhandled 500 responses — regression tests.

``unhandled_handler`` is registered for ``Exception``, so Starlette promotes
it to ``ServerErrorMiddleware`` — the **outermost** middleware. By the time it
renders the 500, ``CORSMiddleware`` has already been unwound, so the response
would carry no CORS headers. A cross-origin browser then refuses to expose it
and the client surfaces an opaque network failure instead of the error
envelope (the mechanism behind the production Purchase ``Failed to fetch``).

These tests pin:

* an unhandled 500 for an allow-listed Origin stays readable cross-origin
* a disallowed / absent Origin is never granted access, even on a 500
* handled errors and the normal CORS path are unchanged
* the middleware ordering the fix depends on (ServerErrorMiddleware outermost)
"""

from __future__ import annotations

import os
from typing import Any, AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient

ALLOWED = "https://app.example.com"
DISALLOWED = "https://evil.example.com"


@pytest.fixture
async def make_app_with_boom() -> AsyncGenerator[Any, None]:
    """Factory: build a fresh app (allow-list = ALLOWED) with a 500-raising route.

    Follows the env-override + ``load_env(force_reload=True)`` + ``create_app``
    convention already used by ``TestCORS`` / ``TestOriginCheck`` in
    ``tests/api/test_middleware.py``.
    """
    from app.config import get_settings
    from app.config.env import load_env
    from app.main import create_app

    original = os.environ.get("POS_CORS_ALLOWED_ORIGINS")
    os.environ["POS_CORS_ALLOWED_ORIGINS"] = ALLOWED
    load_env(force_reload=True)
    try:
        app = create_app(get_settings())

        @app.get("/_boom")
        async def _boom() -> dict[str, bool]:
            raise RuntimeError("boom")

        yield app
    finally:
        if original is None:
            os.environ.pop("POS_CORS_ALLOWED_ORIGINS", None)
        else:
            os.environ["POS_CORS_ALLOWED_ORIGINS"] = original
        load_env(force_reload=True)


def _client(app: Any) -> AsyncClient:
    # raise_app_exceptions=False: ServerErrorMiddleware re-raises after
    # emitting the response, and httpx's ASGITransport defaults to surfacing
    # that exception instead of returning the response.
    return AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    )


class TestMiddlewareOrdering:
    """The fix depends on ServerErrorMiddleware being outermost."""

    async def test_server_error_middleware_is_outermost(
        self, make_app_with_boom: Any
    ) -> None:
        """Snowflake: ServerErrorMiddleware wraps everything, CORSMiddleware is nested inside.

        This is *why* the defect exists: an unhandled exception is rendered by
        the outermost ServerErrorMiddleware after the inner CORSMiddleware has
        already been unwound, so no CORS headers are added.
        """
        stack = make_app_with_boom.build_middleware_stack()
        assert type(stack).__name__ == "ServerErrorMiddleware"

        # Walk inward and record the order.
        order: list[str] = []
        node: Any = stack
        seen: set[int] = set()
        while node is not None and id(node) not in seen:
            seen.add(id(node))
            order.append(type(node).__name__)
            node = getattr(node, "app", None)

        assert order[0] == "ServerErrorMiddleware"
        assert "CORSMiddleware" in order
        # CORS is nested strictly inside the server-error middleware —
        # exactly the ordering that drops CORS headers from unhandled 500s.
        assert order.index("ServerErrorMiddleware") < order.index("CORSMiddleware")


class TestCorsOnUnhandled500:
    """An unhandled 500 must stay readable for an allow-listed Origin."""

    async def test_allowed_origin_gets_cors_headers_on_500(
        self, make_app_with_boom: Any
    ) -> None:
        async with _client(make_app_with_boom) as client:
            r = await client.get("/_boom", headers={"Origin": ALLOWED})

        assert r.status_code == 500, r.text
        # The envelope is intact...
        assert r.json()["error"]["code"] == "internal_error"
        # ...and readable cross-origin (the regression this fix closes).
        assert r.headers.get("access-control-allow-origin") == ALLOWED
        assert r.headers.get("access-control-allow-credentials") == "true"

    async def test_disallowed_origin_never_granted_on_500(
        self, make_app_with_boom: Any
    ) -> None:
        async with _client(make_app_with_boom) as client:
            r = await client.get("/_boom", headers={"Origin": DISALLOWED})

        assert r.status_code == 500
        assert r.headers.get("access-control-allow-origin") is None
        assert r.headers.get("access-control-allow-credentials") is None

    async def test_lookalike_prefix_origin_never_granted_on_500(
        self, make_app_with_boom: Any
    ) -> None:
        """A prefix lookalike of an allow-listed origin must not match.

        Allow-listing is an exact-string set membership test, so
        ``https://app.example.com.evil.com`` must not inherit the rights
        of ``https://app.example.com``.
        """
        lookalike = "https://app.example.com.evil.com"
        async with _client(make_app_with_boom) as client:
            r = await client.get("/_boom", headers={"Origin": lookalike})

        assert r.status_code == 500
        assert r.headers.get("access-control-allow-origin") is None
        assert r.headers.get("access-control-allow-credentials") is None

    async def test_absent_origin_gets_no_cors_headers_on_500(
        self, make_app_with_boom: Any
    ) -> None:
        async with _client(make_app_with_boom) as client:
            r = await client.get("/_boom")

        assert r.status_code == 500
        assert r.headers.get("access-control-allow-origin") is None
        assert r.headers.get("access-control-allow-credentials") is None

    async def test_no_cors_configured_never_grants_access(
        self, make_app_with_boom: Any
    ) -> None:
        """Empty allow-list must not fall back to a wildcard or echo."""
        from app.config import get_settings
        from app.config.env import load_env
        from app.main import create_app

        original = os.environ.get("POS_CORS_ALLOWED_ORIGINS")
        os.environ["POS_CORS_ALLOWED_ORIGINS"] = ""
        load_env(force_reload=True)
        try:
            app = create_app(get_settings())

            @app.get("/_boom2")
            async def _boom2() -> dict[str, bool]:
                raise RuntimeError("boom")

            async with _client(app) as client:
                r = await client.get("/_boom2", headers={"Origin": ALLOWED})
        finally:
            if original is None:
                os.environ.pop("POS_CORS_ALLOWED_ORIGINS", None)
            else:
                os.environ["POS_CORS_ALLOWED_ORIGINS"] = original
            load_env(force_reload=True)

        assert r.status_code == 500
        assert r.headers.get("access-control-allow-origin") is None


class TestNormalPathsUnchanged:
    """The fix must not alter handled errors or the ordinary CORS path."""

    async def test_handled_error_still_carries_cors_headers(
        self, make_app_with_boom: Any
    ) -> None:
        """A 401 raised inside the stack is unchanged (CORSMiddleware adds them)."""
        async with _client(make_app_with_boom) as client:
            r = await client.get("/api/v1/auth/me", headers={"Origin": ALLOWED})

        assert r.status_code == 401, r.text
        assert r.headers.get("access-control-allow-origin") == ALLOWED
        assert r.headers.get("access-control-allow-credentials") == "true"

    async def test_disallowed_origin_not_allowed_on_handled_error(
        self, make_app_with_boom: Any
    ) -> None:
        async with _client(make_app_with_boom) as client:
            r = await client.get("/api/v1/auth/me", headers={"Origin": DISALLOWED})

        assert r.headers.get("access-control-allow-origin") is None