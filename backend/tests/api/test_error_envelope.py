"""Error envelope + canonical ErrorEnvelope tests.

Covers the M1 contract:
* Every 4xx/5xx JSON response has the canonical ErrorEnvelope shape
* Codes map to status codes per the spec
* request_id is always present
* details is null by default
"""

from __future__ import annotations

import json
from typing import Any

from httpx import AsyncClient

from app.errors.codes import ErrorCode


def _assert_envelope_shape(body: dict[str, Any]) -> dict[str, Any]:
    """Assert the canonical ErrorEnvelope shape and return the error dict.

    Shape: ``{"error": {"code", "message", "details", "request_id"}}``.
    """
    assert "error" in body, f"missing top-level 'error' key: {body}"
    err: dict[str, Any] = body["error"]
    assert "code" in err
    assert "message" in err
    assert "details" in err
    assert "request_id" in err
    assert isinstance(err["code"], str)
    assert isinstance(err["message"], str)
    assert isinstance(err["request_id"], str)
    return err


class TestCanonicalErrorEnvelope:
    """Tests for the canonical ErrorEnvelope contract."""

    async def test_validation_failed_envelope(self, app: AsyncClient) -> None:
        """Validation failures return 400 validation_failed envelope."""
        r = await app.post(
            "/api/v1/auth/login",
            json={"username": "", "password": ""},  # both too short
            headers={"Idempotency-Key": "11111111-1111-4111-8111-111111111111"},
        )
        # The canonical handler returns 400 with validation_failed
        assert r.status_code == 400
        err = _assert_envelope_shape(r.json())
        assert err["code"] == ErrorCode.VALIDATION_FAILED.value
        assert err["request_id"]

    async def test_missing_idempotency_key_returns_400(self, app: AsyncClient) -> None:
        """Login without Idempotency-Key header returns 400 missing_header."""
        r = await app.post("/api/v1/auth/login", json={"username": "x", "password": "y"})
        assert r.status_code == 400
        err = _assert_envelope_shape(r.json())
        assert err["code"] == ErrorCode.MISSING_HEADER.value

    async def test_invalid_idempotency_key_returns_400(self, app: AsyncClient) -> None:
        """Non-UUID Idempotency-Key returns 400 invalid_header."""
        r = await app.post(
            "/api/v1/auth/login",
            json={"username": "x", "password": "y"},
            headers={"Idempotency-Key": "not-a-uuid"},
        )
        assert r.status_code == 400
        err = _assert_envelope_shape(r.json())
        assert err["code"] == ErrorCode.INVALID_HEADER.value

    async def test_unauthenticated_when_no_bearer(self, app: AsyncClient) -> None:
        """Missing Authorization header returns 401 unauthenticated."""
        r = await app.get("/api/v1/auth/me")
        assert r.status_code == 401
        err = _assert_envelope_shape(r.json())
        assert err["code"] == ErrorCode.UNAUTHENTICATED.value

    async def test_request_id_present_in_every_envelope(self, app: AsyncClient) -> None:
        """Every error response has a non-empty request_id."""
        # 401
        r = await app.get("/api/v1/auth/me")
        assert r.json()["error"]["request_id"]
        # 400
        r = await app.post("/api/v1/auth/login", json={"username": "x", "password": "y"})
        assert r.json()["error"]["request_id"]

    async def test_request_id_echoed_in_header(self, app: AsyncClient) -> None:
        """X-Request-ID header is echoed back in the response."""
        rid = "11111111-2222-4333-8444-555555555555"
        r = await app.get(
            "/api/v1/auth/me",
            headers={"X-Request-ID": rid},
        )
        assert r.headers.get("X-Request-ID") == rid

    async def test_invalid_bearer_returns_unauthenticated(self, app: AsyncClient) -> None:
        """Invalid Bearer token returns 401 unauthenticated."""
        r = await app.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer not-a-real-token"},
        )
        assert r.status_code == 401
        err = _assert_envelope_shape(r.json())
        assert err["code"] == ErrorCode.UNAUTHENTICATED.value

    async def test_malformed_auth_header_returns_unauthenticated(self, app: AsyncClient) -> None:
        """Malformed Authorization header (no Bearer) returns 401."""
        r = await app.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Basic dXNlcjpwYXNz"},
        )
        assert r.status_code == 401
        err = _assert_envelope_shape(r.json())
        assert err["code"] == ErrorCode.UNAUTHENTICATED.value


class TestEnvelopeShape:
    """Tests for the envelope field semantics."""

    def test_app_error_envelope_shape(self) -> None:
        """Direct AppError rendering matches the spec."""
        from starlette.requests import Request

        from app.core.ids import new_uuid
        from app.errors import Unauthenticated
        from app.errors.handlers import _render
        from app.middleware.request_id import current_request_id

        rid = new_uuid()
        # Build a real Request with the request_id pre-set in the scope so
        # _get_request_id resolves it deterministically.
        req = Request(
            scope={
                "type": "http",
                "method": "GET",
                "path": "/",
                "raw_path": b"/",
                "query_string": b"",
                "headers": [],
                "request_id": str(rid),
            }
        )
        err = Unauthenticated()
        token = current_request_id.set(rid)
        try:
            resp = _render(req, err.status, err.code, err.message, err.details)
        finally:
            current_request_id.reset(token)
        body = json.loads(bytes(resp.body))
        _assert_envelope_shape(body)
        assert body["error"]["code"] == ErrorCode.UNAUTHENTICATED.value
        assert body["error"]["request_id"] == str(rid)
