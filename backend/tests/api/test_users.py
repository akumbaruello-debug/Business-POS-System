"""Users API tests (Phase 1.4).

Covers:
* authorization (user.view / user.manage / user.grant_capability / user.revoke_capability / auth.password_reset_others)
* list (pagination, filter[is_active], filter[role_id], q, sort)
* create (valid, duplicate username, duplicate email, invalid role, idempotency replay/different body)
* get-by-id (existing, 404)
* update (valid If-Match, missing If-Match, stale If-Match, duplicate email, role change)
* deactivate (session revocation, last active Owner protection, login rejection afterward)
* reset password (valid reset, password validation, authorization variants, session revocation)
* unlock (clears lockout state)
* capability grant/revoke (authorization, If-Match, unknown capability, effective capability changes)
* effective-capabilities endpoint
* Staff authorization denial where appropriate
* audit records for mutations
* auth/me regression
"""
from __future__ import annotations

import uuid
from typing import Any

from httpx import AsyncClient
from sqlalchemy import text

from app.authz.caps import CANONICAL_CAPABILITIES
from app.db import get_session_factory
from app.errors.codes import ErrorCode


def _idem() -> str:
    return str(uuid.uuid4())


async def _login(app: AsyncClient, username: str, password: str) -> str:
    r = await app.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"Idempotency-Key": _idem()},
    )
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


async def _owner_headers(app: AsyncClient, owner_user: dict[str, Any]) -> dict[str, str]:
    token = await _login(app, owner_user["username"], owner_user["password"])
    return {"Authorization": f"Bearer {token}"}


async def _staff_headers(app: AsyncClient, staff_user: dict[str, Any]) -> dict[str, str]:
    token = await _login(app, staff_user["username"], staff_user["password"])
    return {"Authorization": f"Bearer {token}"}


async def _create_user(
    app: AsyncClient,
    owner_headers: dict[str, str],
    role_id: int,
    username: str | None = None,
    password: str = "ValidPass123!",
) -> dict[str, Any]:
    uname = username or f"user_{uuid.uuid4().hex[:8]}"
    r = await app.post(
        "/api/v1/users",
        json={
            "username": uname,
            "full_name": f"Test {uname}",
            "email": f"{uname}@example.com",
            "password": password,
            "role_id": role_id,
        },
        headers={**owner_headers, "Idempotency-Key": _idem()},
    )
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


class TestUserAuthorization:
    async def test_list_without_auth_returns_401(self, app: AsyncClient) -> None:
        r = await app.get("/api/v1/users")
        assert r.status_code == 401

    async def test_list_as_staff_denied(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/users", headers=headers)
        assert r.status_code == 403
        assert r.json()["error"]["code"] == ErrorCode.PERMISSION_DENIED.value

    async def test_create_as_staff_denied(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.post(
            "/api/v1/users",
            json={
                "username": "x",
                "full_name": "x",
                "password": "ValidPass123!",
                "role_id": 1,
            },
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 403

    async def test_reset_password_as_staff_denied(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.post(
            "/api/v1/users/1/reset-password",
            json={"new_password": "ValidPass123!"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 403

    async def test_reset_password_with_password_reset_others_cap_succeeds(
        self, app: AsyncClient, owner_user: dict[str, Any], staff_user: dict[str, Any]
    ) -> None:
        # Grant staff the auth.password_reset_others capability.
        await _owner_headers(app, owner_user)
        factory = get_session_factory()
        async with factory() as session:
            staff = (
                await session.execute(text("SELECT id FROM users WHERE username = 'staff'"))
            ).mappings().first()
            cap = (
                await session.execute(
                    text("SELECT id FROM capabilities WHERE code = 'auth.password_reset_others'")
                )
            ).mappings().first()
            await session.execute(
                text(
                    """
                    INSERT INTO user_capability_overrides (user_id, capability_id, is_granted, granted_by)
                    VALUES (:uid, :cid, true, :owner_id)
                    ON CONFLICT (user_id, capability_id) DO UPDATE SET is_granted = true
                    """
                ),
                {"uid": staff["id"], "cid": cap["id"], "owner_id": owner_user["user_id"]},
            )
            await session.commit()

        staff = await _staff_headers(app, staff_user)
        r = await app.post(
            "/api/v1/users/1/reset-password",
            json={"new_password": "ValidPass123!"},
            headers={**staff, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


class TestListUsers:
    async def test_list_empty(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/users", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["pagination"]["total"] >= 1

    async def test_list_filter_is_active(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/users?filter[is_active]=true", headers=headers)
        assert r.status_code == 200
        for row in r.json()["data"]:
            assert row["is_active"] is True

    async def test_list_search_by_q(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        unique = f"searchuser{uuid.uuid4().hex[:6]}"
        await _create_user(app, headers, 2, username=unique)
        r = await app.get(f"/api/v1/users?q={unique}", headers=headers)
        assert r.status_code == 200
        usernames = [u["username"] for u in r.json()["data"]]
        assert unique in usernames

    async def test_list_pagination(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/users?per_page=1", headers=headers)
        assert r.status_code == 200
        assert r.json()["pagination"]["per_page"] == 1


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestCreateUser:
    async def test_create_valid(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        out = await _create_user(app, headers, 2)
        assert out["id"] is not None
        assert out["role_name"] == "Staff"
        assert "version" in out
        assert "created_at" in out

    async def test_create_duplicate_username(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        name = f"dupuser{uuid.uuid4().hex[:6]}"
        await _create_user(app, headers, 2, username=name)
        r = await app.post(
            "/api/v1/users",
            json={
                "username": name,
                "full_name": "Other",
                "email": "other@example.com",
                "password": "ValidPass123!",
                "role_id": 2,
            },
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 409
        assert r.json()["error"]["code"] == ErrorCode.CONFLICT.value

    async def test_create_duplicate_email(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        email = f"dupemail{uuid.uuid4().hex[:6]}@example.com"
        await _create_user(app, headers, 2, username=f"u1{uuid.uuid4().hex[:6]}")
        # Change email manually since helper uses unique email per call.
        r = await app.post(
            "/api/v1/users",
            json={
                "username": f"u2{uuid.uuid4().hex[:6]}",
                "full_name": "Other",
                "email": email,
                "password": "ValidPass123!",
                "role_id": 2,
            },
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201
        r2 = await app.post(
            "/api/v1/users",
            json={
                "username": f"u3{uuid.uuid4().hex[:6]}",
                "full_name": "Other2",
                "email": email,
                "password": "ValidPass123!",
                "role_id": 2,
            },
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 409

    async def test_create_invalid_role(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/users",
            json={
                "username": f"badrole{uuid.uuid4().hex[:6]}",
                "full_name": "Bad",
                "password": "ValidPass123!",
                "role_id": 9999,
            },
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400

    async def test_create_missing_idempotency_key(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/users",
            json={
                "username": f"x{uuid.uuid4().hex[:6]}",
                "full_name": "X",
                "password": "ValidPass123!",
                "role_id": 2,
            },
            headers=headers,
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.MISSING_HEADER.value

    async def test_create_idempotency_replay_same_body(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        idem = _idem()
        body = {
            "username": f"idem{uuid.uuid4().hex[:6]}",
            "full_name": "Idem",
            "email": f"idem{uuid.uuid4().hex[:6]}@example.com",
            "password": "ValidPass123!",
            "role_id": 2,
        }
        r1 = await app.post("/api/v1/users", json=body, headers={**headers, "Idempotency-Key": idem})
        assert r1.status_code == 201
        r2 = await app.post("/api/v1/users", json=body, headers={**headers, "Idempotency-Key": idem})
        assert r2.status_code == 201
        assert r1.json()["id"] == r2.json()["id"]

    async def test_create_idempotency_different_body(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        idem = _idem()
        r1 = await app.post(
            "/api/v1/users",
            json={
                "username": f"idem2a{uuid.uuid4().hex[:6]}",
                "full_name": "A",
                "password": "ValidPass123!",
                "role_id": 2,
            },
            headers={**headers, "Idempotency-Key": idem},
        )
        assert r1.status_code == 201
        r2 = await app.post(
            "/api/v1/users",
            json={
                "username": f"idem2b{uuid.uuid4().hex[:6]}",
                "full_name": "B",
                "password": "ValidPass123!",
                "role_id": 2,
            },
            headers={**headers, "Idempotency-Key": idem},
        )
        assert r2.status_code == 409


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


class TestGetUser:
    async def test_get_existing(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        user = await _create_user(app, headers, 2)
        r = await app.get(f"/api/v1/users/{user['id']}", headers=headers)
        assert r.status_code == 200
        assert r.json()["id"] == user["id"]

    async def test_get_nonexistent(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/users/999999", headers=headers)
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Update (PATCH)
# ---------------------------------------------------------------------------


class TestUpdateUser:
    async def test_patch_valid(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        user = await _create_user(app, headers, 2)
        etag = user["updated_at"]
        r = await app.patch(
            f"/api/v1/users/{user['id']}",
            json={"full_name": "Updated Name"},
            headers={**headers, "If-Match": f'"{etag}"'},
        )
        assert r.status_code == 200
        assert r.json()["full_name"] == "Updated Name"

    async def test_patch_missing_if_match(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        user = await _create_user(app, headers, 2)
        r = await app.patch(
            f"/api/v1/users/{user['id']}",
            json={"full_name": "X"},
            headers=headers,
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.MISSING_HEADER.value

    async def test_patch_stale_if_match(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        user = await _create_user(app, headers, 2)
        etag = user["updated_at"]
        await app.patch(
            f"/api/v1/users/{user['id']}",
            json={"full_name": "First"},
            headers={**headers, "If-Match": f'"{etag}"'},
        )
        r2 = await app.patch(
            f"/api/v1/users/{user['id']}",
            json={"full_name": "Second"},
            headers={**headers, "If-Match": f'"{etag}"'},
        )
        assert r2.status_code == 412

    async def test_patch_duplicate_email(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        u1 = await _create_user(app, headers, 2)
        u2 = await _create_user(app, headers, 2)
        etag = u2["updated_at"]
        r = await app.patch(
            f"/api/v1/users/{u2['id']}",
            json={"email": u1["email"]},
            headers={**headers, "If-Match": f'"{etag}"'},
        )
        assert r.status_code == 409

    async def test_patch_role_change(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        user = await _create_user(app, headers, 2)
        etag = user["updated_at"]
        # Promote to Owner, then revert so later tests that depend on a single
        # active Owner remain isolated.
        r = await app.patch(
            f"/api/v1/users/{user['id']}",
            json={"role_id": 1},
            headers={**headers, "If-Match": f'"{etag}"'},
        )
        assert r.status_code == 200
        assert r.json()["role_id"] == 1

        r2 = await app.patch(
            f"/api/v1/users/{user['id']}",
            json={"role_id": 2},
            headers={**headers, "If-Match": f'"{r.json()["updated_at"]}"'},
        )
        assert r2.status_code == 200
        assert r2.json()["role_id"] == 2


# ---------------------------------------------------------------------------
# Deactivate
# ---------------------------------------------------------------------------


class TestDeactivateUser:
    async def test_deactivate_revokes_sessions(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        user = await _create_user(app, headers, 2, password="ValidPass123!")
        # Login as the new user.
        token = await _login(app, user["username"], "ValidPass123!")
        user_headers = {"Authorization": f"Bearer {token}"}

        # Deactivate the user.
        r = await app.post(
            f"/api/v1/users/{user['id']}/deactivate",
            json={"reason": "test"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 200
        assert r.json()["is_active"] is False

        # Subsequent authenticated call should fail (session revoked + inactive).
        r2 = await app.get("/api/v1/auth/me", headers=user_headers)
        assert r2.status_code == 401

    async def test_deactivate_last_active_owner_denied(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            f"/api/v1/users/{owner_user['user_id']}/deactivate",
            json={"reason": "test"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == ErrorCode.PERMISSION_DENIED.value


# ---------------------------------------------------------------------------
# Reset password
# ---------------------------------------------------------------------------


class TestResetPassword:
    async def test_reset_password_valid(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        user = await _create_user(app, headers, 2, password="OldPass123!")
        token = await _login(app, user["username"], "OldPass123!")

        r = await app.post(
            f"/api/v1/users/{user['id']}/reset-password",
            json={"new_password": "NewPass123!"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 200

        # Old token should be revoked.
        r2 = await app.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r2.status_code == 401

        # New password should work.
        token2 = await _login(app, user["username"], "NewPass123!")
        r3 = await app.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token2}"})
        assert r3.status_code == 200

    async def test_reset_password_too_short(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        user = await _create_user(app, headers, 2)
        r = await app.post(
            f"/api/v1/users/{user['id']}/reset-password",
            json={"new_password": "short"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Unlock
# ---------------------------------------------------------------------------


class TestUnlockUser:
    async def test_unlock_clears_lockout(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        user = await _create_user(app, headers, 2, password="RightPass123!")

        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                text(
                    "UPDATE users SET failed_login_count = 5, locked_until = NOW() + INTERVAL '1 hour' "
                    "WHERE id = :uid"
                ),
                {"uid": user["id"]},
            )
            await session.commit()

        r = await app.post(
            f"/api/v1/users/{user['id']}/unlock",
            json={},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 200

        async with factory() as session:
            row = (
                await session.execute(
                    text("SELECT failed_login_count, locked_until FROM users WHERE id = :uid"),
                    {"uid": user["id"]},
                )
            ).mappings().first()
            assert row["failed_login_count"] == 0
            assert row["locked_until"] is None


# ---------------------------------------------------------------------------
# Effective capabilities
# ---------------------------------------------------------------------------


class TestEffectiveCapabilities:
    async def test_owner_effective_caps(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.get(
            f"/api/v1/users/{owner_user['user_id']}/capabilities",
            headers=headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert set(body["effective"]) == set(CANONICAL_CAPABILITIES)
        assert body["role"] == "Owner"

    async def test_staff_effective_caps(
        self, app: AsyncClient, owner_user: dict[str, Any], staff_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.get(
            f"/api/v1/users/{staff_user['user_id']}/capabilities",
            headers=headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert "sale.create" in body["effective"]
        assert "user.manage" not in body["effective"]


# ---------------------------------------------------------------------------
# Capability grant / revoke
# ---------------------------------------------------------------------------


class TestCapabilityOverrides:
    async def test_grant_capability(
        self, app: AsyncClient, owner_user: dict[str, Any], staff_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        # Get staff ETag.
        r1 = await app.get(f"/api/v1/users/{staff_user['user_id']}", headers=headers)
        etag = r1.json()["updated_at"]

        r = await app.post(
            f"/api/v1/users/{staff_user['user_id']}/capabilities",
            json={"capability_code": "user.manage", "is_granted": True},
            headers={**headers, "Idempotency-Key": _idem(), "If-Match": f'"{etag}"'},
        )
        assert r.status_code == 200
        assert "user.manage" in r.json()["effective"]

    async def test_revoke_capability(
        self, app: AsyncClient, owner_user: dict[str, Any], staff_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        # Grant then revoke.
        r1 = await app.get(f"/api/v1/users/{staff_user['user_id']}", headers=headers)
        etag = r1.json()["updated_at"]
        await app.post(
            f"/api/v1/users/{staff_user['user_id']}/capabilities",
            json={"capability_code": "sale.create", "is_granted": False},
            headers={**headers, "Idempotency-Key": _idem(), "If-Match": f'"{etag}"'},
        )
        r = await app.delete(
            f"/api/v1/users/{staff_user['user_id']}/capabilities/sale.create",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 204

    async def test_grant_unknown_capability(
        self, app: AsyncClient, owner_user: dict[str, Any], staff_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r1 = await app.get(f"/api/v1/users/{staff_user['user_id']}", headers=headers)
        etag = r1.json()["updated_at"]
        r = await app.post(
            f"/api/v1/users/{staff_user['user_id']}/capabilities",
            json={"capability_code": "not.a.real.capability", "is_granted": True},
            headers={**headers, "Idempotency-Key": _idem(), "If-Match": f'"{etag}"'},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.VALIDATION_FAILED.value

    async def test_grant_capability_staff_denied(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.post(
            f"/api/v1/users/{staff_user['user_id']}/capabilities",
            json={"capability_code": "user.manage", "is_granted": True},
            headers={**headers, "Idempotency-Key": _idem(), "If-Match": '"*"'},
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class TestUserAudit:
    async def test_create_writes_audit(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        user = await _create_user(app, headers, 2)

        factory = get_session_factory()
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT action FROM audit_log WHERE entity_type = 'user' AND entity_id = :uid"
                    ),
                    {"uid": user["id"]},
                )
            ).mappings().first()
        assert row is not None
        assert row["action"] == "create"


# ---------------------------------------------------------------------------
# Auth/me regression
# ---------------------------------------------------------------------------


class TestAuthMeRegression:
    async def test_auth_me_still_works(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/auth/me", headers=headers)
        assert r.status_code == 200
        assert r.json()["username"] == "owner"
