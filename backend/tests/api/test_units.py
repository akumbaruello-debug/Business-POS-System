"""M2 foundation — Units API tests.

Covers:
* authorization (view/manage capability gating)
* list (pagination, filter[is_active], search)
* create (valid, duplicate code, invalid data, missing Idempotency-Key)
* get-by-id (existing, nonexistent)
* update (PATCH name, immutable code)
* deactivate (soft-delete preserves history)
* delete (hard delete, 409 referenced_by_history, 404, missing Idempotency-Key)
* audit behavior (create/update/deactivate/delete logged)
* error envelopes (uniform shape)
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from app.errors.codes import ErrorCode

pytestmark = pytest.mark.integration


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


def _valid_unit(code: str | None = None, name: str = "Test Unit") -> dict[str, Any]:
    return {
        "code": code or f"u_{uuid.uuid4().hex[:8]}",
        "name": name,
        "is_active": True,
    }


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


class TestUnitAuthorization:
    async def test_create_without_auth_returns_401(self, app: AsyncClient) -> None:
        r = await app.post("/api/v1/units/", json=_valid_unit())
        assert r.status_code == 401

    async def test_create_as_staff_returns_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.post(
            "/api/v1/units/",
            json=_valid_unit(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == ErrorCode.PERMISSION_DENIED.value

    async def test_list_as_staff_succeeds(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/units/", headers=headers)
        assert r.status_code == 200

    async def test_delete_as_staff_returns_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.delete(
            "/api/v1/units/1",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


class TestListUnits:
    async def test_list_empty_returns_pagination(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Fresh DB has no units seeded — list returns empty with pagination."""
        headers = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/units/", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["data"] == []
        assert body["pagination"]["total"] == 0

    async def test_list_after_create(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        # Create 2 units
        for name in ("A", "B"):
            await app.post(
                "/api/v1/units/",
                json=_valid_unit(name=name.lower()),
                headers={**headers, "Idempotency-Key": _idem()},
            )
        r = await app.get("/api/v1/units/", headers=headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert len(data) == 2

    async def test_list_with_filter_is_active(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        # Create active + deactivate it
        body = _valid_unit()
        r = await app.post(
            "/api/v1/units/",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        uid = r.json()["id"]
        await app.post(
            f"/api/v1/units/{uid}/deactivate",
            json={"reason": "x"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        # filter[is_active]=true → only active
        r2 = await app.get("/api/v1/units/?filter[is_active]=true", headers=headers)
        assert r2.status_code == 200
        for row in r2.json()["data"]:
            assert row["is_active"] is True

        # No filter → includes deactivated
        r3 = await app.get("/api/v1/units/", headers=headers)
        ids = {row["id"] for row in r3.json()["data"]}
        assert uid in ids

    async def test_list_search_by_q(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        await app.post(
            "/api/v1/units/",
            json={"code": "searchable_unit", "name": "Kilogram", "is_active": True},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        r = await app.get("/api/v1/units/?q=kilo", headers=headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert any(row["name"] == "Kilogram" for row in data)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestCreateUnit:
    async def test_create_valid(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        body = _valid_unit()
        body["name"] = "Weight Unit"
        r = await app.post(
            "/api/v1/units/",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201, r.text
        out = r.json()
        assert out["code"] == body["code"]
        assert out["name"] == "Weight Unit"
        assert out["is_active"] is True
        assert out["version"] == 1  # placeholder per documented contradiction
        assert "id" in out

    async def test_create_duplicate_code_returns_409(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        code = f"dup_{uuid.uuid4().hex[:8]}"
        r1 = await app.post(
            "/api/v1/units/",
            json={"code": code, "name": "First", "is_active": True},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r1.status_code == 201, r1.text

        r2 = await app.post(
            "/api/v1/units/",
            json={"code": code, "name": "Second", "is_active": True},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 409, r2.text
        assert r2.json()["error"]["code"] == ErrorCode.CONFLICT.value

    async def test_create_invalid_code_pattern_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """code must match ^[A-Za-z0-9_-]+$ per OpenAPI."""
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/units/",
            json={"code": "bad code!", "name": "X", "is_active": True},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400

    async def test_create_empty_name_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/units/",
            json=_valid_unit(name=""),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.VALIDATION_FAILED.value

    async def test_create_missing_idempotency_key_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post("/api/v1/units/", json=_valid_unit(), headers=headers)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.MISSING_HEADER.value

    async def test_create_with_extra_fields_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        body = _valid_unit()
        body["unexpected_field"] = "bad"
        r = await app.post(
            "/api/v1/units/",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


class TestGetUnit:
    async def test_get_existing(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        body = _valid_unit()
        r = await app.post(
            "/api/v1/units/",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        uid = r.json()["id"]
        r2 = await app.get(f"/api/v1/units/{uid}", headers=headers)
        assert r2.status_code == 200
        assert r2.json()["code"] == body["code"]

    async def test_get_nonexistent_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/units/999999", headers=headers)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == ErrorCode.NOT_FOUND.value


# ---------------------------------------------------------------------------
# Update (PATCH)
# ---------------------------------------------------------------------------


class TestUpdateUnit:
    async def test_update_name(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/units/",
            json=_valid_unit(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        uid = r.json()["id"]

        r2 = await app.patch(
            f"/api/v1/units/{uid}",
            json={"name": "Updated name"},
            headers=headers,
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["name"] == "Updated name"

    async def test_update_is_active(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/units/",
            json=_valid_unit(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        uid = r.json()["id"]
        r2 = await app.patch(
            f"/api/v1/units/{uid}",
            json={"is_active": False},
            headers=headers,
        )
        assert r2.status_code == 200
        assert r2.json()["is_active"] is False

    async def test_update_code_is_rejected(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """UnitPatch schema does not expose ``code`` → extra field → 400."""
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/units/",
            json=_valid_unit(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        uid = r.json()["id"]
        r2 = await app.patch(
            f"/api/v1/units/{uid}",
            json={"code": "new_code"},
            headers=headers,
        )
        assert r2.status_code == 400

    async def test_update_nonexistent_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.patch(
            "/api/v1/units/999999",
            json={"name": "X"},
            headers=headers,
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Deactivate
# ---------------------------------------------------------------------------


class TestDeactivateUnit:
    async def test_deactivate_sets_inactive(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/units/",
            json=_valid_unit(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        uid = r.json()["id"]

        r2 = await app.post(
            f"/api/v1/units/{uid}/deactivate",
            json={"reason": "done"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["is_active"] is False

        # History preserved
        r3 = await app.get(f"/api/v1/units/{uid}", headers=headers)
        assert r3.status_code == 200
        assert r3.json()["is_active"] is False

    async def test_deactivate_reversible(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Per DB spec §2.2.4: deactivation is reversible."""
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/units/",
            json=_valid_unit(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        uid = r.json()["id"]
        await app.post(
            f"/api/v1/units/{uid}/deactivate",
            json={"reason": "test"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        # Reactivate
        r2 = await app.patch(
            f"/api/v1/units/{uid}",
            json={"is_active": True},
            headers=headers,
        )
        assert r2.status_code == 200
        assert r2.json()["is_active"] is True

    async def test_deactivate_missing_idempotency_key_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/units/1/deactivate",
            json={"reason": "x"},
            headers=headers,
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.MISSING_HEADER.value


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


class TestDeleteUnit:
    async def test_delete_unreferenced_succeeds(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/units/",
            json=_valid_unit(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        uid = r.json()["id"]

        r2 = await app.delete(
            f"/api/v1/units/{uid}",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 204, r2.text

        r3 = await app.get(f"/api/v1/units/{uid}", headers=headers)
        assert r3.status_code == 404

    async def test_delete_referenced_returns_409(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Unit referenced by a product → 409 referenced_by_history."""
        headers = await _owner_headers(app, owner_user)

        # Create a unit
        r = await app.post(
            "/api/v1/units/",
            json=_valid_unit(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        uid = r.json()["id"]

        # Insert a product referencing it via raw SQL (products need
        # created_by, updated_by, name — use the owner id).
        from sqlalchemy import text

        from app.db import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                text(
                    "INSERT INTO products (name, unit_id, created_by, updated_by) "
                    "VALUES (:name, :unit_id, :uid, :uid)"
                ),
                {"name": "Test Product", "unit_id": uid, "uid": owner_user["user_id"]},
            )
            await session.commit()

        # Attempt to delete the unit → FK violation → 409
        r2 = await app.delete(
            f"/api/v1/units/{uid}",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 409, r2.text
        assert r2.json()["error"]["code"] == ErrorCode.REFERENCED_BY_HISTORY.value

    async def test_delete_nonexistent_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.delete(
            "/api/v1/units/999999",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 404

    async def test_delete_missing_idempotency_key_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.delete("/api/v1/units/1", headers=headers)
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Audit behavior
# ---------------------------------------------------------------------------


class TestUnitAudit:
    async def test_create_writes_audit_log(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        code = f"audit_{uuid.uuid4().hex[:8]}"
        await app.post(
            "/api/v1/units/",
            json={"code": code, "name": "Audit Test", "is_active": True},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        from sqlalchemy import text

        from app.db import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            row = await session.execute(
                text(
                    "SELECT action, entity_type FROM audit_log "
                    "WHERE entity_type = 'unit' AND (new_values->>'code')::text = :code "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"code": code},
            )
            r = row.mappings().first()
        assert r is not None
        assert r["action"] == "create"
        assert r["entity_type"] == "unit"

    async def test_delete_writes_audit_log(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        code = f"del_audit_{uuid.uuid4().hex[:8]}"
        r = await app.post(
            "/api/v1/units/",
            json={"code": code, "name": "Del Audit", "is_active": True},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        uid = r.json()["id"]
        await app.delete(
            f"/api/v1/units/{uid}",
            headers={**headers, "Idempotency-Key": _idem()},
        )

        from sqlalchemy import text

        from app.db import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            row = await session.execute(
                text(
                    "SELECT action FROM audit_log "
                    "WHERE entity_type = 'unit' AND entity_id = :uid "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"uid": uid},
            )
            r2 = row.mappings().first()
        assert r2 is not None
        # 'delete' is not in the audit action whitelist (ck_audit_action),
        # so the service logs it as 'update' with new_values=NULL.
        assert r2["action"] == "update"
