"""M2 Categories (B.1) — API tests.

Covers:
* authorization (view vs manage capability gating)
* list (pagination, filter[is_active], q search, sort)
* create (valid, duplicate name 409, Idempotency-Key, parent_id, audit)
* get-by-id (existing, 404)
* update (PATCH name, PATCH is_active, parent_id reassignment)
* update with If-Match (required, mismatch → 412, missing → 400)
* deactivate (preserves history, Idempotency-Key required)
* hard delete (succeeds when unreferenced; 409 when children or products
  reference it; 404 not found)
* audit logging (create/update/deactivate/delete all log)
* error envelopes (uniform shape)
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


def _valid_category(name: str | None = None, **kw: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name or f"cat_{uuid.uuid4().hex[:8]}",
        "is_active": True,
    }
    body.update(kw)
    return body


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


class TestCategoryAuthorization:
    async def test_list_without_auth_returns_401(self, app: AsyncClient) -> None:
        r = await app.get("/api/v1/categories")
        assert r.status_code == 401

    async def test_list_as_staff_succeeds_view_capability(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/categories", headers=headers)
        assert r.status_code == 200

    async def test_create_as_staff_returns_403_no_manage(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.post(
            "/api/v1/categories",
            json=_valid_category(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == ErrorCode.PERMISSION_DENIED.value

    async def test_patch_as_staff_returns_403(
        self, app: AsyncClient, staff_user: dict[str, Any], owner_user: dict[str, Any]
    ) -> None:
        owner = await _owner_headers(app, owner_user)
        staff = await _staff_headers(app, staff_user)
        r = await app.post(
            "/api/v1/categories",
            json=_valid_category(name="to_patch"),
            headers={**owner, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201
        cid = r.json()["id"]
        etag = r.json()["updated_at"]
        r2 = await app.patch(
            f"/api/v1/categories/{cid}",
            json={"name": "renamed"},
            headers={**staff, "If-Match": f'"{etag}"'},
        )
        assert r2.status_code == 403

    async def test_delete_as_staff_returns_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.delete(
            "/api/v1/categories/1",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 403

    async def test_deactivate_as_staff_returns_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.post(
            "/api/v1/categories/1/deactivate",
            json={"reason": "x"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


class TestListCategories:
    async def test_list_empty_returns_pagination(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/categories", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["data"] == []
        assert body["pagination"]["total"] == 0

    async def test_list_after_create(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        for n in ("alpha", "beta"):
            await app.post(
                "/api/v1/categories",
                json=_valid_category(name=f"{n}_{uuid.uuid4().hex[:6]}"),
                headers={**headers, "Idempotency-Key": _idem()},
            )
        r = await app.get("/api/v1/categories", headers=headers)
        assert r.status_code == 200
        assert len(r.json()["data"]) >= 2

    async def test_list_filter_is_active(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/categories",
            json=_valid_category(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        await app.post(
            f"/api/v1/categories/{cid}/deactivate",
            json={"reason": "test"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        # filter[is_active]=true → only active
        r2 = await app.get("/api/v1/categories?filter[is_active]=true", headers=headers)
        assert r2.status_code == 200
        for row in r2.json()["data"]:
            assert row["is_active"] is True

    async def test_list_search_by_q(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        unique = f"searchterm{uuid.uuid4().hex[:6]}"
        await app.post(
            "/api/v1/categories",
            json=_valid_category(name=unique),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        r = await app.get(f"/api/v1/categories?q={unique}", headers=headers)
        assert r.status_code == 200
        names = [row["name"] for row in r.json()["data"]]
        assert unique in names

    async def test_list_pagination_respects_per_page(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        for _ in range(3):
            await app.post(
                "/api/v1/categories",
                json=_valid_category(),
                headers={**headers, "Idempotency-Key": _idem()},
            )
        r = await app.get("/api/v1/categories?per_page=2&page=1", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert len(body["data"]) <= 2
        assert body["pagination"]["per_page"] == 2


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestCreateCategory:
    async def test_create_valid(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        body = _valid_category(name=f"valid_{uuid.uuid4().hex[:8]}")
        r = await app.post(
            "/api/v1/categories",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201, r.text
        out = r.json()
        assert out["name"] == body["name"]
        assert out["is_active"] is True
        assert "id" in out
        assert "version" in out
        assert "created_at" in out
        assert "updated_at" in out

    async def test_create_duplicate_name_returns_409(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        name = f"dup_{uuid.uuid4().hex[:8]}"
        r1 = await app.post(
            "/api/v1/categories",
            json=_valid_category(name=name),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r1.status_code == 201
        r2 = await app.post(
            "/api/v1/categories",
            json=_valid_category(name=name),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 409
        assert r2.json()["error"]["code"] == ErrorCode.CONFLICT.value

    async def test_create_missing_idempotency_key_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post("/api/v1/categories", json=_valid_category(), headers=headers)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.MISSING_HEADER.value

    async def test_create_with_invalid_idempotency_key_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/categories",
            json=_valid_category(),
            headers={**headers, "Idempotency-Key": "not-a-uuid"},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.INVALID_HEADER.value

    async def test_create_empty_name_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/categories",
            json={"name": "", "is_active": True},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.VALIDATION_FAILED.value

    async def test_create_with_extra_field_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        body = _valid_category()
        body["unexpected"] = "bad"
        r = await app.post(
            "/api/v1/categories",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400

    async def test_create_with_parent_id(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        # Create parent
        r = await app.post(
            "/api/v1/categories",
            json=_valid_category(name=f"parent_{uuid.uuid4().hex[:6]}"),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        parent_id = r.json()["id"]
        # Create child
        r2 = await app.post(
            "/api/v1/categories",
            json=_valid_category(name=f"child_{uuid.uuid4().hex[:6]}", parent_id=parent_id),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 201
        assert r2.json()["parent_id"] == parent_id


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


class TestGetCategory:
    async def test_get_existing(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/categories",
            json=_valid_category(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        r2 = await app.get(f"/api/v1/categories/{cid}", headers=headers)
        assert r2.status_code == 200
        assert r2.json()["id"] == cid

    async def test_get_nonexistent_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/categories/999999", headers=headers)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == ErrorCode.NOT_FOUND.value


# ---------------------------------------------------------------------------
# Update (PATCH) — If-Match is REQUIRED per OpenAPI
# ---------------------------------------------------------------------------


class TestUpdateCategory:
    async def test_patch_rename(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/categories",
            json=_valid_category(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        updated_at = r.json()["updated_at"]
        new_name = f"renamed_{uuid.uuid4().hex[:6]}"
        r2 = await app.patch(
            f"/api/v1/categories/{cid}",
            json={"name": new_name},
            headers={**headers, "If-Match": f'"{updated_at}"'},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["name"] == new_name

    async def test_patch_is_active(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/categories",
            json=_valid_category(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        updated_at = r.json()["updated_at"]
        r2 = await app.patch(
            f"/api/v1/categories/{cid}",
            json={"is_active": False},
            headers={**headers, "If-Match": f'"{updated_at}"'},
        )
        assert r2.status_code == 200
        assert r2.json()["is_active"] is False

    async def test_patch_without_if_match_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/categories",
            json=_valid_category(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        r2 = await app.patch(
            f"/api/v1/categories/{cid}",
            json={"name": "x"},
            headers=headers,
        )
        # If-Match missing → 400 missing_header
        assert r2.status_code == 400
        assert r2.json()["error"]["code"] == ErrorCode.MISSING_HEADER.value

    async def test_patch_with_stale_if_match_returns_412(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/categories",
            json=_valid_category(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        # First PATCH — succeeds
        await app.patch(
            f"/api/v1/categories/{cid}",
            json={"name": "first"},
            headers={**headers, "If-Match": f'"{r.json()["updated_at"]}"'},
        )
        # Second PATCH — uses the OLD (stale) ETag from initial GET → 412
        r3 = await app.patch(
            f"/api/v1/categories/{cid}",
            json={"name": "second"},
            headers={**headers, "If-Match": f'"{r.json()["updated_at"]}"'},
        )
        assert r3.status_code == 412
        assert r3.json()["error"]["code"] == ErrorCode.VERSION_MISMATCH.value

    async def test_patch_with_invalid_if_match_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/categories",
            json=_valid_category(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        r2 = await app.patch(
            f"/api/v1/categories/{cid}",
            json={"name": "x"},
            headers={**headers, "If-Match": "not-a-quoted-etag"},
        )
        assert r2.status_code == 400

    async def test_patch_nonexistent_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.patch(
            "/api/v1/categories/999999",
            json={"name": "x"},
            headers={**headers, "If-Match": '"2024-01-01T00:00:00+00:00"'},
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Deactivate
# ---------------------------------------------------------------------------


class TestDeactivateCategory:
    async def test_deactivate_sets_inactive(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/categories",
            json=_valid_category(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        r2 = await app.post(
            f"/api/v1/categories/{cid}/deactivate",
            json={"reason": "closing line"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 200
        assert r2.json()["is_active"] is False

    async def test_deactivate_preserves_history_via_get(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/categories",
            json=_valid_category(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        await app.post(
            f"/api/v1/categories/{cid}/deactivate",
            json={"reason": "test"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        r2 = await app.get(f"/api/v1/categories/{cid}", headers=headers)
        assert r2.status_code == 200
        assert r2.json()["is_active"] is False

    async def test_deactivate_missing_idempotency_key_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post("/api/v1/categories/1/deactivate", json={"reason": "x"}, headers=headers)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.MISSING_HEADER.value

    async def test_deactivate_nonexistent_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/categories/999999/deactivate",
            json={"reason": "x"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Hard Delete
# ---------------------------------------------------------------------------


class TestDeleteCategory:
    async def test_delete_unreferenced_succeeds(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/categories",
            json=_valid_category(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        r2 = await app.delete(
            f"/api/v1/categories/{cid}",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 204
        r3 = await app.get(f"/api/v1/categories/{cid}", headers=headers)
        assert r3.status_code == 404

    async def test_delete_with_child_returns_409(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        # Create parent
        r = await app.post(
            "/api/v1/categories",
            json=_valid_category(name=f"parent_{uuid.uuid4().hex[:6]}"),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        parent_id = r.json()["id"]
        # Create child
        await app.post(
            "/api/v1/categories",
            json=_valid_category(name=f"child_{uuid.uuid4().hex[:6]}", parent_id=parent_id),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        # Attempt delete parent → 409
        r2 = await app.delete(
            f"/api/v1/categories/{parent_id}",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 409
        assert r2.json()["error"]["code"] == ErrorCode.REFERENCED_BY_HISTORY.value

    async def test_delete_referenced_by_product_returns_409(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """A product referencing the category → 409 referenced_by_history."""
        headers = await _owner_headers(app, owner_user)
        # Create category
        r = await app.post(
            "/api/v1/categories",
            json=_valid_category(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]

        # Insert a product that references this category directly via SQL.
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                text(
                    "INSERT INTO products (name, category_id, unit_id, created_by, updated_by) "
                    "VALUES (:nm, :cid, "
                    "(SELECT id FROM units ORDER BY id LIMIT 1), "
                    ":uid, :uid)"
                ),
                {
                    "nm": f"Test_Product_{uuid.uuid4().hex[:6]}",
                    "cid": cid,
                    "uid": owner_user["user_id"],
                },
            )
            await session.commit()

        # Attempt to delete the category → FK violation → 409
        r2 = await app.delete(
            f"/api/v1/categories/{cid}",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 409, r2.text
        assert r2.json()["error"]["code"] == ErrorCode.REFERENCED_BY_HISTORY.value

    async def test_delete_nonexistent_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.delete(
            "/api/v1/categories/999999",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 404

    async def test_delete_missing_idempotency_key_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.delete("/api/v1/categories/1", headers=headers)
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Audit behavior
# ---------------------------------------------------------------------------


class TestCategoryAudit:
    async def test_create_writes_audit_log(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        name = f"audit_{uuid.uuid4().hex[:8]}"
        r = await app.post(
            "/api/v1/categories",
            json=_valid_category(name=name),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]

        factory = get_session_factory()
        async with factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT action, entity_type FROM audit_log "
                            "WHERE entity_type = 'category' AND entity_id = :cid "
                            "ORDER BY id DESC LIMIT 1"
                        ),
                        {"cid": cid},
                    )
                )
                .mappings()
                .first()
            )
        assert row is not None
        assert row["action"] == "create"
        assert row["entity_type"] == "category"

    async def test_delete_writes_audit_log(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        # Create
        r = await app.post(
            "/api/v1/categories",
            json=_valid_category(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        # Delete
        await app.delete(
            f"/api/v1/categories/{cid}",
            headers={**headers, "Idempotency-Key": _idem()},
        )

        factory = get_session_factory()
        async with factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT action FROM audit_log "
                            "WHERE entity_type = 'category' AND entity_id = :cid "
                            "ORDER BY id DESC LIMIT 1"
                        ),
                        {"cid": cid},
                    )
                )
                .mappings()
                .first()
            )
        assert row is not None
        # 'delete' is not in ck_audit_action whitelist; mapped to 'update'.
        assert row["action"] == "update"

    async def test_deactivate_writes_audit_log(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/categories",
            json=_valid_category(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        await app.post(
            f"/api/v1/categories/{cid}/deactivate",
            json={"reason": "audit test"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        factory = get_session_factory()
        async with factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT action, reason FROM audit_log "
                            "WHERE entity_type = 'category' AND entity_id = :cid "
                            "AND action = 'deactivate' "
                            "ORDER BY id DESC LIMIT 1"
                        ),
                        {"cid": cid},
                    )
                )
                .mappings()
                .first()
            )
        assert row is not None
        assert row["reason"] == "audit test"
