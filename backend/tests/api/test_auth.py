"""Authentication flow tests.

Covers the complete M1 auth flow:
* POST /auth/login        — valid/invalid credentials, idempotency
* POST /auth/refresh      — token rotation
* POST /auth/logout       — session revocation
* POST /auth/logout-all   — revoke all (requires user.manage)
* GET  /auth/me           — current user + capabilities

Each test uses the ``app`` fixture (which depends on ``db_clean``)
so every test starts with a clean DB state.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import AsyncClient

from app.config import get_settings
from app.core.clock import reset_clock, set_clock
from app.errors.codes import ErrorCode


def _make_uuid() -> str:
    """Generate a fresh UUID string for Idempotency-Key."""
    return str(uuid.uuid4())


class _FrozenClock:
    """Minimal ``Clock`` pinned to a single instant (see ``app.core.clock``)."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class TestLogin:
    """POST /auth/login tests."""

    async def test_valid_login(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        """Valid credentials return 200 with token pair."""
        key = _make_uuid()
        r = await app.post(
            "/api/v1/auth/login",
            json={"username": "owner", "password": owner_user["password"]},
            headers={"Idempotency-Key": key},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "access_token" in body
        assert "refresh_token" in body
        assert "expires_in" in body
        assert "refresh_expires_in" in body
        assert "user" in body
        assert body["user"]["username"] == "owner"
        assert "id" in body["user"]
        assert "capabilities" in body["user"]

    async def test_invalid_password(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        """Wrong password returns 401 invalid_credentials."""
        r = await app.post(
            "/api/v1/auth/login",
            json={"username": "owner", "password": "wrongpassword"},
            headers={"Idempotency-Key": _make_uuid()},
        )
        assert r.status_code == 401, r.text
        err = r.json()["error"]
        assert err["code"] == ErrorCode.INVALID_CREDENTIALS.value

    async def test_unknown_user(self, app: AsyncClient) -> None:
        """Unknown username returns 401 (not 404)."""
        r = await app.post(
            "/api/v1/auth/login",
            json={"username": "nobody", "password": "x"},
            headers={"Idempotency-Key": _make_uuid()},
        )
        assert r.status_code == 401, r.text
        err = r.json()["error"]
        assert err["code"] == ErrorCode.INVALID_CREDENTIALS.value

    async def test_password_too_short(self, app: AsyncClient) -> None:
        """Empty password fails Pydantic min_length=1 → 400 validation_failed."""
        r = await app.post(
            "/api/v1/auth/login",
            json={"username": "owner", "password": ""},
            headers={"Idempotency-Key": _make_uuid()},
        )
        assert r.status_code == 400, r.text
        err = r.json()["error"]
        assert err["code"] == ErrorCode.VALIDATION_FAILED.value

    async def test_username_too_long(self, app: AsyncClient) -> None:
        """Username exceeding max_length triggers 400 validation_failed."""
        r = await app.post(
            "/api/v1/auth/login",
            json={"username": "x" * 200, "password": "someValidPass!"},
            headers={"Idempotency-Key": _make_uuid()},
        )
        assert r.status_code == 400, r.text

    async def test_extra_body_fields_rejected(self, app: AsyncClient) -> None:
        """LoginRequest forbids extra fields (extra='forbid')."""
        r = await app.post(
            "/api/v1/auth/login",
            json={
                "username": "owner",
                "password": "x",
                "extra_field": "bad",
            },
            headers={"Idempotency-Key": _make_uuid()},
        )
        assert r.status_code == 400, r.text
        err = r.json()["error"]
        assert err["code"] == ErrorCode.VALIDATION_FAILED.value

    async def test_missing_idempotency_key(self, app: AsyncClient) -> None:
        """Login without Idempotency-Key returns 400 missing_header."""
        r = await app.post(
            "/api/v1/auth/login",
            json={"username": "owner", "password": "x"},
        )
        assert r.status_code == 400, r.text
        err = r.json()["error"]
        assert err["code"] == ErrorCode.MISSING_HEADER.value


class TestLoginIdempotency:
    """Idempotency behavior for POST /auth/login."""

    async def test_idempotent_replay(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        """Same Idempotency-Key + identical body returns cached response."""
        key = _make_uuid()
        body = {"username": "owner", "password": owner_user["password"]}

        # First request
        r1 = await app.post(
            "/api/v1/auth/login",
            json=body,
            headers={"Idempotency-Key": key},
        )
        assert r1.status_code == 200, r1.text
        resp1 = r1.json()

        # Second request with same key + same body → replay
        r2 = await app.post(
            "/api/v1/auth/login",
            json=body,
            headers={"Idempotency-Key": key},
        )
        assert r2.status_code == 200, r2.text
        resp2 = r2.json()

        # The cached response must be an exact replay (same tokens).
        assert resp1["access_token"] == resp2["access_token"]
        assert resp1["refresh_token"] == resp2["refresh_token"]

    async def test_conflicting_replay_returns_409(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Same key + different body returns 409 idempotency_violation."""
        key = _make_uuid()

        # First request succeeds
        r1 = await app.post(
            "/api/v1/auth/login",
            json={"username": "owner", "password": owner_user["password"]},
            headers={"Idempotency-Key": key},
        )
        assert r1.status_code == 200, r1.text

        # Second request with same key but different password
        r2 = await app.post(
            "/api/v1/auth/login",
            json={"username": "owner", "password": "wrongpassword"},
            headers={"Idempotency-Key": key},
        )
        assert r2.status_code == 409, r2.text
        err = r2.json()["error"]
        assert err["code"] == ErrorCode.IDEMPOTENCY_VIOLATION.value

    async def test_conflicting_does_not_create_idempotency_record(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Conflicting request must not insert a conflicting idempotency row."""
        key = _make_uuid()

        # First succeeds → creates idempotency_keys row
        r1 = await app.post(
            "/api/v1/auth/login",
            json={"username": "owner", "password": owner_user["password"]},
            headers={"Idempotency-Key": key},
        )
        assert r1.status_code == 200

        # Second with different body → 409
        r2 = await app.post(
            "/api/v1/auth/login",
            json={"username": "owner", "password": "differentpassword"},
            headers={"Idempotency-Key": key},
        )
        assert r2.status_code == 409

        # Verify the original idempotency record is unchanged (still 1 row)
        from sqlalchemy import text
        from sqlalchemy.engine import RowMapping

        from app.db import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            cnt_row: RowMapping | None = (
                (
                    await session.execute(
                        text("SELECT COUNT(*) AS cnt FROM idempotency_keys WHERE key = :k"),
                        {"k": key},
                    )
                )
                .mappings()
                .first()
            )
            assert cnt_row is not None
            assert int(cnt_row["cnt"]) == 1
            # The original row's request_hash must be unchanged
            row: RowMapping | None = (
                (
                    await session.execute(
                        text(
                            "SELECT request_hash, response_status "
                            "FROM idempotency_keys WHERE key = :k"
                        ),
                        {"k": key},
                    )
                )
                .mappings()
                .first()
            )
            assert row is not None
            assert int(row["response_status"]) == 200


class TestRefresh:
    """POST /auth/refresh tests."""

    async def test_valid_refresh(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        """Valid refresh token rotates tokens."""
        # Login first
        r = await app.post(
            "/api/v1/auth/login",
            json={"username": "owner", "password": owner_user["password"]},
            headers={"Idempotency-Key": _make_uuid()},
        )
        assert r.status_code == 200
        tokens = r.json()
        refresh = tokens["refresh_token"]

        # Refresh
        r2 = await app.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": refresh},
        )
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert "access_token" in body
        assert "refresh_token" in body
        # New tokens differ from old
        assert body["access_token"] != tokens["access_token"]
        assert body["refresh_token"] != tokens["refresh_token"]

    async def test_invalid_refresh_token(self, app: AsyncClient) -> None:
        """Invalid refresh token returns 401."""
        r = await app.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": "bogus-token-value"},
        )
        assert r.status_code == 401, r.text
        err = r.json()["error"]
        assert err["code"] == ErrorCode.UNAUTHENTICATED.value


class TestLogout:
    """POST /auth/logout tests."""

    async def test_logout_revokes_session(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Logout revokes the current session — /me then fails."""
        r = await app.post(
            "/api/v1/auth/login",
            json={"username": "owner", "password": owner_user["password"]},
            headers={"Idempotency-Key": _make_uuid()},
        )
        assert r.status_code == 200
        access = r.json()["access_token"]

        # Logout
        r2 = await app.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {access}"},
        )
        assert r2.status_code == 204, r2.text

        # /me should now return 401 (revoked)
        r3 = await app.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {access}"},
        )
        assert r3.status_code == 401, r3.text
        err = r3.json()["error"]
        assert err["code"] == ErrorCode.SESSION_REVOKED.value

    async def test_logout_without_auth(self, app: AsyncClient) -> None:
        """Logout without auth returns 401."""
        r = await app.post("/api/v1/auth/logout")
        assert r.status_code == 401


class TestLogoutAll:
    """POST /auth/logout-all tests."""

    async def test_logout_all_requires_user_manage(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        """Staff user (no user.manage) cannot call logout-all."""
        # Login as staff
        r = await app.post(
            "/api/v1/auth/login",
            json={"username": "staff", "password": staff_user["password"]},
            headers={"Idempotency-Key": _make_uuid()},
        )
        assert r.status_code == 200
        access = r.json()["access_token"]

        r2 = await app.post(
            "/api/v1/auth/logout-all",
            headers={"Authorization": f"Bearer {access}"},
        )
        assert r2.status_code == 403, r2.text
        err = r2.json()["error"]
        assert err["code"] == ErrorCode.PERMISSION_DENIED.value

    async def test_logout_all_as_owner(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        """Owner can revoke all sessions."""
        r = await app.post(
            "/api/v1/auth/login",
            json={"username": "owner", "password": owner_user["password"]},
            headers={"Idempotency-Key": _make_uuid()},
        )
        assert r.status_code == 200
        access = r.json()["access_token"]

        r2 = await app.post(
            "/api/v1/auth/logout-all",
            headers={"Authorization": f"Bearer {access}"},
        )
        assert r2.status_code == 204, r2.text


class TestMe:
    """GET /auth/me tests."""

    async def test_me_returns_user_info(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        """Authenticated /me returns user details + capabilities."""
        r = await app.post(
            "/api/v1/auth/login",
            json={"username": "owner", "password": owner_user["password"]},
            headers={"Idempotency-Key": _make_uuid()},
        )
        assert r.status_code == 200
        access = r.json()["access_token"]

        r2 = await app.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {access}"},
        )
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body["username"] == "owner"
        assert body["id"] == owner_user["user_id"]
        assert "capabilities" in body
        # Owner has all capabilities
        assert "user.manage" in body["capabilities"]
        assert "sale.create" in body["capabilities"]

    async def test_me_as_staff_has_restricted_caps(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        """Staff /me returns only Staff-default capabilities."""
        r = await app.post(
            "/api/v1/auth/login",
            json={"username": "staff", "password": staff_user["password"]},
            headers={"Idempotency-Key": _make_uuid()},
        )
        assert r.status_code == 200
        access = r.json()["access_token"]

        r2 = await app.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {access}"},
        )
        assert r2.status_code == 200
        body = r2.json()
        assert body["username"] == "staff"
        assert "user.manage" not in body["capabilities"]
        assert "sale.create" in body["capabilities"]


# ---------------------------------------------------------------------------
# Bearer-only contract
# ---------------------------------------------------------------------------


async def _login_tokens(
    app: AsyncClient,
    password: str,
    username: str = "owner",
    key: str | None = None,
) -> dict[str, Any]:
    r = await app.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"Idempotency-Key": key or _make_uuid()},
    )
    assert r.status_code == 200, r.text
    return r.json()


class TestNoAuthCookies:
    """RC1 is Bearer-only: no auth cookie is ever set or cleared."""

    async def test_login_sets_no_cookie(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        r = await app.post(
            "/api/v1/auth/login",
            json={"username": "owner", "password": owner_user["password"]},
            headers={"Idempotency-Key": _make_uuid()},
        )
        assert r.status_code == 200, r.text
        assert "set-cookie" not in r.headers

    async def test_login_replay_sets_no_cookie(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        key = _make_uuid()
        first = await _login_tokens(app, owner_user["password"], key=key)
        second = await app.post(
            "/api/v1/auth/login",
            json={"username": "owner", "password": owner_user["password"]},
            headers={"Idempotency-Key": key},
        )
        assert second.status_code == 200, second.text
        assert "set-cookie" not in second.headers
        assert second.json()["access_token"] == first["access_token"]

    async def test_refresh_sets_no_cookie(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        tokens = await _login_tokens(app, owner_user["password"])
        r = await app.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert r.status_code == 200, r.text
        assert "set-cookie" not in r.headers

    async def test_logout_sets_no_cookie(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        tokens = await _login_tokens(app, owner_user["password"])
        r = await app.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert r.status_code == 204, r.text
        assert "set-cookie" not in r.headers

    async def test_logout_all_sets_no_cookie(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        tokens = await _login_tokens(app, owner_user["password"])
        r = await app.post(
            "/api/v1/auth/logout-all",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert r.status_code == 204, r.text
        assert "set-cookie" not in r.headers

    async def test_cookie_without_bearer_cannot_authenticate(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """A valid token in a Cookie header must NOT authenticate."""
        tokens = await _login_tokens(app, owner_user["password"])
        r = await app.get(
            "/api/v1/auth/me",
            headers={"Cookie": f"access_token={tokens['access_token']}"},
        )
        assert r.status_code == 401, r.text
        assert r.json()["error"]["code"] == ErrorCode.UNAUTHENTICATED.value


class TestTokenExpiry:
    """Expired / revoked tokens are rejected."""

    async def test_expired_access_token_rejected(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        t0 = datetime.now(tz=UTC)
        set_clock(_FrozenClock(t0))
        try:
            tokens = await _login_tokens(app, owner_user["password"])
            set_clock(
                _FrozenClock(
                    t0 + timedelta(seconds=get_settings().access_token_ttl_seconds + 1)
                )
            )
            r = await app.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
        finally:
            reset_clock()
        assert r.status_code == 401, r.text
        assert r.json()["error"]["code"] == ErrorCode.SESSION_EXPIRED.value

    async def test_expired_refresh_token_rejected(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        t0 = datetime.now(tz=UTC)
        set_clock(_FrozenClock(t0))
        try:
            tokens = await _login_tokens(app, owner_user["password"])
            set_clock(
                _FrozenClock(
                    t0 + timedelta(seconds=get_settings().refresh_token_ttl_seconds + 1)
                )
            )
            r = await app.post(
                "/api/v1/auth/refresh",
                json={"refresh_token": tokens["refresh_token"]},
            )
        finally:
            reset_clock()
        assert r.status_code == 401, r.text
        assert r.json()["error"]["code"] == ErrorCode.SESSION_EXPIRED.value

    async def test_revoked_refresh_token_rejected(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        tokens = await _login_tokens(app, owner_user["password"])
        logout = await app.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert logout.status_code == 204

        r = await app.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert r.status_code == 401, r.text
        assert r.json()["error"]["code"] == ErrorCode.SESSION_REVOKED.value

    async def test_rotation_invalidates_previous_access_token(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        tokens = await _login_tokens(app, owner_user["password"])
        rotated = await app.post(
            "/api/v1/auth/refresh",
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert rotated.status_code == 200, rotated.text

        r = await app.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert r.status_code == 401, r.text

        ok = await app.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {rotated.json()['access_token']}"},
        )
        assert ok.status_code == 200, ok.text
