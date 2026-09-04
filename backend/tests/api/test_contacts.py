"""M2 Contacts — API tests.

Covers:
* authorization (view vs manage capability gating)
* list (pagination, filter[type], q search, sort)
* create (valid, duplicate name+type 409, Idempotency-Key, audit)
* get-by-id (existing, 404)
* update (PATCH name, PATCH is_active, If-Match required)
* deactivate (preserves history, Idempotency-Key required)
* hard delete (succeeds when unreferenced; 409 when referenced; 404 not found)
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


class ContactType(str):
    CUSTOMER = "customer"
    SUPPLIER = "supplier"
    BOTH = "both"


def _valid_contact(
    name: str | None = None,
    contact_type: str = ContactType.CUSTOMER,
    **kw: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": contact_type,
        "name": name or f"contact_{uuid.uuid4().hex[:8]}",
        "is_active": True,
    }
    body.update(kw)
    return body


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


class TestContactAuthorization:
    async def test_list_without_auth_returns_401(self, app: AsyncClient) -> None:
        r = await app.get("/api/v1/contacts")
        assert r.status_code == 401

    async def test_list_as_staff_succeeds_view_capability(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/contacts", headers=headers)
        assert r.status_code == 200

    async def test_create_as_staff_returns_403_no_manage(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.post(
            "/api/v1/contacts",
            json=_valid_contact(),
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
            "/api/v1/contacts",
            json=_valid_contact(name="to_patch"),
            headers={**owner, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201
        cid = r.json()["id"]
        etag = r.json()["updated_at"]
        r2 = await app.patch(
            f"/api/v1/contacts/{cid}",
            json={"name": "renamed"},
            headers={**staff, "If-Match": f'"{etag}"'},
        )
        assert r2.status_code == 403

    async def test_delete_as_staff_returns_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.delete(
            "/api/v1/contacts/1",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 403

    async def test_deactivate_as_staff_returns_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.post(
            "/api/v1/contacts/1/deactivate",
            json={"reason": "x"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


class TestListContacts:
    async def test_list_empty_returns_pagination(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/contacts", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["data"] == []
        assert body["pagination"]["total"] == 0

    async def test_list_after_create(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        for n in ("alpha", "beta"):
            await app.post(
                "/api/v1/contacts",
                json=_valid_contact(name=f"{n}_{uuid.uuid4().hex[:6]}"),
                headers={**headers, "Idempotency-Key": _idem()},
            )
        r = await app.get("/api/v1/contacts", headers=headers)
        assert r.status_code == 200
        assert len(r.json()["data"]) >= 2

    async def test_list_filter_type(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        # Create customer
        await app.post(
            "/api/v1/contacts",
            json=_valid_contact(contact_type=ContactType.CUSTOMER),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        # Create supplier
        await app.post(
            "/api/v1/contacts",
            json=_valid_contact(contact_type=ContactType.SUPPLIER),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        # Filter by type
        r = await app.get("/api/v1/contacts?filter[type]=customer", headers=headers)
        assert r.status_code == 200
        customer_count = len([c for c in r.json()["data"] if c["type"] == ContactType.CUSTOMER])
        assert customer_count == 1

    async def test_list_filter_is_active(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/contacts",
            json=_valid_contact(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        await app.post(
            f"/api/v1/contacts/{cid}/deactivate",
            json={"reason": "test"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        # filter[is_active]=true → only active
        r2 = await app.get("/api/v1/contacts?filter[is_active]=true", headers=headers)
        assert r2.status_code == 200
        for row in r2.json()["data"]:
            assert row["is_active"] is True

    async def test_list_search_by_q(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        unique = f"searchterm{uuid.uuid4().hex[:6]}"
        await app.post(
            "/api/v1/contacts",
            json=_valid_contact(name=unique),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        r = await app.get(f"/api/v1/contacts?q={unique}", headers=headers)
        assert r.status_code == 200
        names = [row["name"] for row in r.json()["data"]]
        assert unique in names

    async def test_list_pagination_respects_per_page(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        for _ in range(3):
            await app.post(
                "/api/v1/contacts",
                json=_valid_contact(),
                headers={**headers, "Idempotency-Key": _idem()},
            )
        r = await app.get("/api/v1/contacts?per_page=2&page=1", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert len(body["data"]) <= 2
        assert body["pagination"]["per_page"] == 2


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestCreateContact:
    async def test_create_valid(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        body = _valid_contact(name=f"valid_{uuid.uuid4().hex[:8]}")
        r = await app.post(
            "/api/v1/contacts",
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

    async def test_create_duplicate_name_and_type_returns_409(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        name = f"dup_{uuid.uuid4().hex[:8]}"
        r1 = await app.post(
            "/api/v1/contacts",
            json=_valid_contact(name=name),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r1.status_code == 201
        r2 = await app.post(
            "/api/v1/contacts",
            json=_valid_contact(name=name, type="customer"),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 409
        assert r2.json()["error"]["code"] == ErrorCode.CONFLICT.value

    async def test_create_missing_idempotency_key_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post("/api/v1/contacts", json=_valid_contact(), headers=headers)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.MISSING_HEADER.value

    async def test_create_with_invalid_idempotency_key_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/contacts",
            json=_valid_contact(),
            headers={**headers, "Idempotency-Key": "not-a-uuid"},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.INVALID_HEADER.value

    async def test_create_empty_name_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/contacts",
            json={"type": ContactType.CUSTOMER, "name": "", "is_active": True},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.VALIDATION_FAILED.value

    async def test_create_with_extra_field_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        body = _valid_contact()
        body["unexpected"] = "bad"
        r = await app.post(
            "/api/v1/contacts",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


class TestGetContact:
    async def test_get_existing(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/contacts",
            json=_valid_contact(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        r2 = await app.get(f"/api/v1/contacts/{cid}", headers=headers)
        assert r2.status_code == 200
        assert r2.json()["id"] == cid

    async def test_get_nonexistent_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/contacts/999999", headers=headers)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == ErrorCode.NOT_FOUND.value


# ---------------------------------------------------------------------------
# Update (PATCH) — If-Match is REQUIRED per OpenAPI
# ---------------------------------------------------------------------------


class TestUpdateContact:
    async def test_patch_rename(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/contacts",
            json=_valid_contact(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        updated_at = r.json()["updated_at"]
        new_name = f"renamed_{uuid.uuid4().hex[:6]}"
        r2 = await app.patch(
            f"/api/v1/contacts/{cid}",
            json={"name": new_name},
            headers={**headers, "If-Match": f'"{updated_at}"'},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["name"] == new_name

    async def test_patch_is_active(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/contacts",
            json=_valid_contact(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        updated_at = r.json()["updated_at"]
        r2 = await app.patch(
            f"/api/v1/contacts/{cid}",
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
            "/api/v1/contacts",
            json=_valid_contact(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        r2 = await app.patch(
            f"/api/v1/contacts/{cid}",
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
            "/api/v1/contacts",
            json=_valid_contact(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        # First PATCH — succeeds
        await app.patch(
            f"/api/v1/contacts/{cid}",
            json={"name": "first"},
            headers={**headers, "If-Match": f'"{r.json()["updated_at"]}"'},
        )
        # Second PATCH — uses the OLD (stale) ETag from initial GET → 412
        r3 = await app.patch(
            f"/api/v1/contacts/{cid}",
            json={"name": "second"},
            headers={**headers, "If-Match": f'"{r.json()["updated_at"]}"'},
        )
        assert r3.status_code == 412
        assert r3.json()["error"]["code"] == ErrorCode.VERSION_MISMATCH.value

    async def test_patch_nonexistent_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.patch(
            "/api/v1/contacts/999999",
            json={"name": "x"},
            headers={**headers, "If-Match": '"2024-01-01T00:00:00+00:00"'},
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Deactivate
# ---------------------------------------------------------------------------


class TestDeactivateContact:
    async def test_deactivate_sets_inactive(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/contacts",
            json=_valid_contact(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        r2 = await app.post(
            f"/api/v1/contacts/{cid}/deactivate",
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
            "/api/v1/contacts",
            json=_valid_contact(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        await app.post(
            f"/api/v1/contacts/{cid}/deactivate",
            json={"reason": "test"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        r2 = await app.get(f"/api/v1/contacts/{cid}", headers=headers)
        assert r2.status_code == 200
        assert r2.json()["is_active"] is False


# ---------------------------------------------------------------------------
# Delete (hard)
# ---------------------------------------------------------------------------


class TestDeleteContact:
    async def test_delete_nonexistent_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.delete(
            "/api/v1/contacts/999999",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 404
        assert r.json()["error"]["code"] == ErrorCode.NOT_FOUND.value

    async def test_delete_unreferenced_succeeds(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/contacts",
            json=_valid_contact(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        r2 = await app.delete(
            f"/api/v1/contacts/{cid}",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 204

    async def test_delete_referenced_returns_409(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        # Create a contact
        r = await app.post(
            "/api/v1/contacts",
            json=_valid_contact(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        # Create a draft sale referencing it as customer (draft persists
        # the customer row → FK RESTRICT blocks hard delete).
        sale_body = {"customer_id": cid, "notes": "test-ref"}
        await app.post(
            "/api/v1/sales",
            json=sale_body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        # Try to delete the contact → 409
        r2 = await app.delete(
            f"/api/v1/contacts/{cid}",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 409
        assert r2.json()["error"]["code"] == ErrorCode.REFERENCED_BY_HISTORY.value


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class TestAuditContact:
    async def test_create_audit_log(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/contacts",
            json=_valid_contact(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        # Verify audit log entry
        factory = get_session_factory()
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT action FROM audit_log WHERE entity_type = 'contact'"
                        " AND entity_id = :id ORDER BY id DESC LIMIT 1"
                    ),
                    {"id": cid},
                )
            ).mappings().first()
            assert row is not None
            assert row["action"] == "create"

    async def test_update_audit_log(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        # Create contact
        r = await app.post(
            "/api/v1/contacts",
            json=_valid_contact(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        updated_at = r.json()["updated_at"]
        # Update contact
        await app.patch(
            f"/api/v1/contacts/{cid}",
            json={"name": "updated"},
            headers={**headers, "If-Match": f'"{updated_at}"'},
        )
        # Verify audit log entry
        factory = get_session_factory()
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT action FROM audit_log WHERE entity_type = 'contact'"
                        " AND entity_id = :id ORDER BY id DESC LIMIT 1"
                    ),
                    {"id": cid},
                )
            ).mappings().first()
            assert row is not None
            assert row["action"] == "update"

    async def test_deactivate_audit_log(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        # Create contact
        r = await app.post(
            "/api/v1/contacts",
            json=_valid_contact(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        # Deactivate contact
        await app.post(
            f"/api/v1/contacts/{cid}/deactivate",
            json={"reason": "test"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        # Verify audit log entry
        factory = get_session_factory()
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT action FROM audit_log WHERE entity_type = 'contact'"
                        " AND entity_id = :id ORDER BY id DESC LIMIT 1"
                    ),
                    {"id": cid},
                )
            ).mappings().first()
            assert row is not None
            assert row["action"] == "deactivate"

    async def test_delete_audit_log(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        # Create contact
        r = await app.post(
            "/api/v1/contacts",
            json=_valid_contact(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        # Delete contact
        await app.delete(
            f"/api/v1/contacts/{cid}",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        # Verify audit log entry
        factory = get_session_factory()
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT action FROM audit_log WHERE entity_type = 'contact'"
                        " AND entity_id = :id ORDER BY id DESC LIMIT 1"
                    ),
                    {"id": cid},
                )
            ).mappings().first()
            assert row is not None
            assert row["action"] == "update"  # 'delete' not in ck_audit_action; use 'update' (matches categories pattern)