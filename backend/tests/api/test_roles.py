"""Roles API tests (Phase 1.5).

Covers:
* authorization (role.view / role.manage)
* list (pagination, q search, sort)
* create (valid, duplicate name 409, Idempotency-Key, zero capabilities)
* get-by-id (existing, 404)
* update (PATCH name/description, If-Match required, stale → 412)
* system role PATCH blocked
* system role DELETE blocked
* delete custom role (succeeds unreferenced; 409 when users assigned)
* set capabilities (replace, unknown code, system role blocked)
* audit logging
* auth/authz regression
"""

from __future__ import annotations

import uuid
from typing import Any

from httpx import AsyncClient
from sqlalchemy import text

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


def _valid_role(name: str | None = None, **kw: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name or f"role_{uuid.uuid4().hex[:8]}",
    }
    body.update(kw)
    return body


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


class TestRoleAuthorization:
    async def test_list_without_auth_returns_401(self, app: AsyncClient) -> None:
        r = await app.get("/api/v1/roles")
        assert r.status_code == 401

    async def test_list_as_staff_denied(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/roles", headers=headers)
        assert r.status_code == 403
        assert r.json()["error"]["code"] == ErrorCode.PERMISSION_DENIED.value

    async def test_create_as_staff_denied(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.post(
            "/api/v1/roles",
            json=_valid_role(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 403

    async def test_patch_as_staff_denied(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.patch(
            "/api/v1/roles/1",
            json={"name": "x"},
            headers={**headers, "If-Match": '"*"'},
        )
        assert r.status_code == 403

    async def test_delete_as_staff_denied(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.delete(
            "/api/v1/roles/1",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


class TestListRoles:
    async def test_list_returns_pagination(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/roles", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert "data" in body
        assert "pagination" in body
        assert body["pagination"]["page"] == 1

    async def test_list_includes_system_roles(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/roles?per_page=200", headers=headers)
        assert r.status_code == 200
        names = {item["name"] for item in r.json()["data"]}
        assert "Owner" in names
        assert "Staff" in names

    async def test_list_search_by_q(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        unique = f"searchrole{uuid.uuid4().hex[:6]}"
        await app.post(
            "/api/v1/roles",
            json=_valid_role(name=unique),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        r = await app.get(f"/api/v1/roles?q={unique}", headers=headers)
        assert r.status_code == 200
        names = [row["name"] for row in r.json()["data"]]
        assert unique in names


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestCreateRole:
    async def test_create_valid(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        body = _valid_role()
        r = await app.post(
            "/api/v1/roles",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201, r.text
        out = r.json()
        assert out["name"] == body["name"]
        assert out["is_system_role"] is False
        assert out["is_active"] is True
        assert out["capability_codes"] == []
        assert "id" in out
        assert "version" in out

    async def test_create_with_description(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        body = _valid_role(description="A test role")
        r = await app.post(
            "/api/v1/roles",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201
        assert r.json()["description"] == "A test role"

    async def test_create_duplicate_name_returns_409(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        name = f"dup_{uuid.uuid4().hex[:8]}"
        r1 = await app.post(
            "/api/v1/roles",
            json=_valid_role(name=name),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r1.status_code == 201
        r2 = await app.post(
            "/api/v1/roles",
            json=_valid_role(name=name),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 409
        assert r2.json()["error"]["code"] == ErrorCode.CONFLICT.value

    async def test_create_missing_idempotency_key_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post("/api/v1/roles", json=_valid_role(), headers=headers)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.MISSING_HEADER.value


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


class TestGetRole:
    async def test_get_existing(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/roles",
            json=_valid_role(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        rid = r.json()["id"]
        r2 = await app.get(f"/api/v1/roles/{rid}", headers=headers)
        assert r2.status_code == 200
        assert r2.json()["id"] == rid

    async def test_get_nonexistent_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/roles/9999", headers=headers)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == ErrorCode.NOT_FOUND.value


# ---------------------------------------------------------------------------
# Update (PATCH)
# ---------------------------------------------------------------------------


class TestUpdateRole:
    async def test_patch_rename(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/roles",
            json=_valid_role(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        rid = r.json()["id"]
        updated_at = r.json()["updated_at"]
        new_name = f"renamed_{uuid.uuid4().hex[:6]}"
        r2 = await app.patch(
            f"/api/v1/roles/{rid}",
            json={"name": new_name},
            headers={**headers, "If-Match": f'"{updated_at}"'},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["name"] == new_name

    async def test_patch_without_if_match_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/roles",
            json=_valid_role(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        rid = r.json()["id"]
        r2 = await app.patch(
            f"/api/v1/roles/{rid}",
            json={"name": "x"},
            headers=headers,
        )
        assert r2.status_code == 400
        assert r2.json()["error"]["code"] == ErrorCode.MISSING_HEADER.value

    async def test_patch_with_stale_if_match_returns_412(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/roles",
            json=_valid_role(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        rid = r.json()["id"]
        etag = r.json()["updated_at"]
        await app.patch(
            f"/api/v1/roles/{rid}",
            json={"name": "first"},
            headers={**headers, "If-Match": f'"{etag}"'},
        )
        r3 = await app.patch(
            f"/api/v1/roles/{rid}",
            json={"name": "second"},
            headers={**headers, "If-Match": f'"{etag}"'},
        )
        assert r3.status_code == 412
        assert r3.json()["error"]["code"] == ErrorCode.VERSION_MISMATCH.value

    async def test_patch_system_role_returns_403(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        # Owner role id == 1
        r = await app.get("/api/v1/roles/1", headers=headers)
        etag = r.json()["updated_at"]
        r2 = await app.patch(
            "/api/v1/roles/1",
            json={"name": "x"},
            headers={**headers, "If-Match": f'"{etag}"'},
        )
        assert r2.status_code == 403
        assert r2.json()["error"]["code"] == ErrorCode.SYSTEM_ROLE_IMMUTABLE.value


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


class TestDeleteRole:
    async def test_delete_unreferenced_custom_role_succeeds(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/roles",
            json=_valid_role(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        rid = r.json()["id"]
        r2 = await app.delete(
            f"/api/v1/roles/{rid}",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 204
        r3 = await app.get(f"/api/v1/roles/{rid}", headers=headers)
        assert r3.status_code == 404

    async def test_delete_system_role_returns_403(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.delete(
            "/api/v1/roles/1",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == ErrorCode.SYSTEM_ROLE_IMMUTABLE.value

    async def test_delete_system_role_returns_403_for_staff(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        # staff user fixture uses role_id 2 (Staff). Staff is system_role TRUE.
        r = await app.delete(
            "/api/v1/roles/2",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == ErrorCode.SYSTEM_ROLE_IMMUTABLE.value

    async def test_delete_referenced_custom_role_returns_409(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        # Create a custom role
        r = await app.post(
            "/api/v1/roles",
            json=_valid_role(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        role_id = r.json()["id"]
        # Create a user assigned to that role
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                text(
                    """
                    INSERT INTO users (username, full_name, email, is_active, role_id, password_hash)
                    VALUES (:u, :fn, :em, true, :rid, :ph)
                    """
                ),
                {
                    "u": f"refuser_{uuid.uuid4().hex[:6]}",
                    "fn": "Referenced User",
                    "em": "ref@example.com",
                    "rid": role_id,
                    "ph": "x",
                },
            )
            await session.commit()
        r2 = await app.delete(
            f"/api/v1/roles/{role_id}",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 409
        assert r2.json()["error"]["code"] == ErrorCode.REFERENCED_BY_HISTORY.value

    async def test_delete_missing_idempotency_key_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.delete("/api/v1/roles/999999", headers=headers)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.MISSING_HEADER.value


# ---------------------------------------------------------------------------
# Set capabilities
# ---------------------------------------------------------------------------


class TestSetRoleCapabilities:
    async def test_set_capabilities_replaces_set(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/roles",
            json=_valid_role(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        rid = r.json()["id"]
        r2 = await app.put(
            f"/api/v1/roles/{rid}/capabilities",
            json={"capability_codes": ["sale.view", "sale.create"]},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 200, r2.text
        assert set(r2.json()["capability_codes"]) == {"sale.create", "sale.view"}

    async def test_set_capabilities_to_empty(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/roles",
            json=_valid_role(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        rid = r.json()["id"]
        await app.put(
            f"/api/v1/roles/{rid}/capabilities",
            json={"capability_codes": ["sale.view"]},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        r2 = await app.put(
            f"/api/v1/roles/{rid}/capabilities",
            json={"capability_codes": []},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 200
        assert r2.json()["capability_codes"] == []

    async def test_set_unknown_capability_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/roles",
            json=_valid_role(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        rid = r.json()["id"]
        r2 = await app.put(
            f"/api/v1/roles/{rid}/capabilities",
            json={"capability_codes": ["not.real"]},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 400
        assert r2.json()["error"]["code"] == ErrorCode.VALIDATION_FAILED.value

    async def test_set_capabilities_system_role_returns_403(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.put(
            "/api/v1/roles/1/capabilities",
            json={"capability_codes": ["sale.view"]},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == ErrorCode.SYSTEM_ROLE_IMMUTABLE.value


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class TestRoleAudit:
    async def test_create_writes_audit_log(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        name = f"audit_{uuid.uuid4().hex[:8]}"
        r = await app.post(
            "/api/v1/roles",
            json=_valid_role(name=name),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        rid = r.json()["id"]

        factory = get_session_factory()
        async with factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT action, entity_type FROM audit_log "
                            "WHERE entity_type = 'role' AND entity_id = :rid "
                            "ORDER BY id DESC LIMIT 1"
                        ),
                        {"rid": rid},
                    )
                )
                .mappings()
                .first()
            )
        assert row is not None
        assert row["action"] == "create"
        assert row["entity_type"] == "role"
