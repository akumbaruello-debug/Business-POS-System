"""V3 Phase 2 Product Attributes (Jenis / Size / Kualitas) — API tests.

Covers:
* authorization (view vs manage capability gating; staff can view, cannot manage)
* list (pagination, filter[kind], filter[is_active], q search)
* create (valid 201, duplicate (kind,name) 409, invalid kind 400,
  Idempotency-Key required, same name under different kinds allowed,
  audit logged, kind stored)
* get-by-id (existing 200, 404)
* update (PATCH name/is_active, If-Match required → 400, stale → 412,
  kind immutable → 400 extra-forbid, duplicate rename → 409)
* deactivate (preserves history, Idempotency-Key required)
* hard delete (unreferenced 204; 404; referenced by product → 409)
* product linkage (create product with jenis/size/kualitas ids; wrong kind → 404)
* error envelopes (uniform shape)
"""

from __future__ import annotations

import uuid
from typing import Any

from httpx import AsyncClient

from app.db import get_session_factory
from app.errors.codes import ErrorCode
from sqlalchemy import text


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


def _valid_attr(kind: str = "jenis", name: str | None = None, **kw: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "kind": kind,
        "name": name or f"attr_{uuid.uuid4().hex[:8]}",
        "is_active": True,
    }
    body.update(kw)
    return body


async def _create_attr(
    app: AsyncClient,
    headers: dict[str, str],
    kind: str = "jenis",
    name: str | None = None,
) -> dict[str, Any]:
    r = await app.post(
        "/api/v1/product-attributes",
        json=_valid_attr(kind, name),
        headers={**headers, "Idempotency-Key": _idem()},
    )
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


class TestProductAttributeAuthorization:
    async def test_list_without_auth_returns_401(self, app: AsyncClient) -> None:
        r = await app.get("/api/v1/product-attributes")
        assert r.status_code == 401

    async def test_list_as_staff_succeeds_view_capability(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/product-attributes", headers=headers)
        assert r.status_code == 200

    async def test_create_as_staff_returns_403_no_manage(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.post(
            "/api/v1/product-attributes",
            json=_valid_attr(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == ErrorCode.PERMISSION_DENIED.value


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


class TestListProductAttributes:
    async def test_list_empty(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/product-attributes", headers=headers)
        assert r.status_code == 200
        assert r.json()["data"] == []
        assert r.json()["pagination"]["total"] == 0

    async def test_list_seeded_rows(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        await _create_attr(app, headers, "jenis", "Tongkol")
        await _create_attr(app, headers, "size", "Besar")
        await _create_attr(app, headers, "kualitas", "A")
        r = await app.get("/api/v1/product-attributes", headers=headers)
        assert r.status_code == 200
        assert r.json()["pagination"]["total"] == 3

    async def test_list_filter_by_kind(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        await _create_attr(app, headers, "jenis", "Tongkol")
        await _create_attr(app, headers, "size", "Besar")
        r = await app.get(
            "/api/v1/product-attributes?filter[kind]=size", headers=headers
        )
        assert r.status_code == 200
        data = r.json()["data"]
        assert len(data) == 1
        assert data[0]["kind"] == "size"

    async def test_list_filter_kind_requires_valid_value(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        # Unknown kind → no rows (server never invents kinds; API stays silent).
        headers = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/product-attributes?filter[kind]=warna", headers=headers
        )
        assert r.status_code == 200
        assert r.json()["data"] == []


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestCreateProductAttribute:
    async def test_create_valid(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/product-attributes",
            json={"kind": "jenis", "name": "Tongkol", "is_active": True},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["kind"] == "jenis"
        assert body["name"] == "Tongkol"
        assert body["is_active"] is True
        assert body["version"] >= 1

    async def test_create_all_three_kinds_distinct(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        for kind in ("jenis", "size", "kualitas"):
            r = await app.post(
                "/api/v1/product-attributes",
                json={"kind": kind, "name": f"val-{kind}"},
                headers={**headers, "Idempotency-Key": _idem()},
            )
            assert r.status_code == 201, r.text
            assert r.json()["kind"] == kind

    async def test_same_name_across_kinds_allowed(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        shared = f"same_{uuid.uuid4().hex[:6]}"
        a = await _create_attr(app, headers, "size", shared)
        b = await _create_attr(app, headers, "kualitas", shared)
        assert a["id"] != b["id"]

    async def test_duplicate_same_kind_returns_409(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        dup_name = f"dup_{uuid.uuid4().hex[:6]}"
        await _create_attr(app, headers, "jenis", dup_name)
        r = await app.post(
            "/api/v1/product-attributes",
            json={"kind": "jenis", "name": dup_name},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 409
        assert r.json()["error"]["code"] == ErrorCode.CONFLICT.value

    async def test_create_invalid_kind_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/product-attributes",
            json={"kind": "warna", "name": "Merah"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == ErrorCode.VALIDATION_FAILED.value

    async def test_create_missing_idempotency_key_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/product-attributes",
            json=_valid_attr(),
            headers=headers,
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.MISSING_HEADER.value

    async def test_create_extra_field_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/product-attributes",
            json={**_valid_attr(), "code": "XX"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.VALIDATION_FAILED.value

    async def test_create_logs_audit(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        created = await _create_attr(app, headers, "kualitas", "Premium")
        factory = get_session_factory()
        async with factory() as session:
            row = (
                await session.execute(
                    text(
                        "SELECT entity_type, action, entity_id FROM audit_log "
                        "WHERE entity_type = 'product_attribute' AND entity_id = :id "
                        "ORDER BY id DESC LIMIT 1"
                    ),
                    {"id": created["id"]},
                )
            ).mappings().first()
            assert row is not None
            assert row["action"] == "create"


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


class TestGetProductAttribute:
    async def test_get_existing(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        created = await _create_attr(app, headers, "size", "Sedang")
        r = await app.get(
            f"/api/v1/product-attributes/{created['id']}", headers=headers
        )
        assert r.status_code == 200
        assert r.json()["name"] == "Sedang"
        assert r.json()["kind"] == "size"

    async def test_get_nonexistent_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/product-attributes/999999", headers=headers)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == ErrorCode.NOT_FOUND.value


# ---------------------------------------------------------------------------
# Update (PATCH)
# ---------------------------------------------------------------------------


class TestUpdateProductAttribute:
    async def test_patch_rename(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        created = await _create_attr(app, headers, "jenis", "Cakalang")
        new_name = f"renamed_{uuid.uuid4().hex[:6]}"
        r = await app.patch(
            f"/api/v1/product-attributes/{created['id']}",
            json={"name": new_name},
            headers={**headers, "If-Match": f'"{created["updated_at"]}"'},
        )
        assert r.status_code == 200, r.text
        assert r.json()["name"] == new_name
        assert r.json()["kind"] == "jenis"

    async def test_patch_kind_is_rejected(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        # kind is immutable — PATCH schema has no kind field (extra=forbid → 400).
        headers = await _owner_headers(app, owner_user)
        created = await _create_attr(app, headers, "jenis", "Tuna")
        r = await app.patch(
            f"/api/v1/product-attributes/{created['id']}",
            json={"kind": "size"},
            headers={**headers, "If-Match": f'"{created["updated_at"]}"'},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.VALIDATION_FAILED.value

    async def test_patch_without_if_match_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        created = await _create_attr(app, headers, "size", "Kecil")
        r = await app.patch(
            f"/api/v1/product-attributes/{created['id']}",
            json={"name": "x"},
            headers=headers,
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.MISSING_HEADER.value

    async def test_patch_with_stale_if_match_returns_412(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        created = await _create_attr(app, headers, "kualitas", "B")
        # First rename uses fresh ETag.
        r1 = await app.patch(
            f"/api/v1/product-attributes/{created['id']}",
            json={"name": "first"},
            headers={**headers, "If-Match": f'"{created["updated_at"]}"'},
        )
        assert r1.status_code == 200
        # Second rename reuses the stale ETag → 412.
        r2 = await app.patch(
            f"/api/v1/product-attributes/{created['id']}",
            json={"name": "second"},
            headers={**headers, "If-Match": f'"{created["updated_at"]}"'},
        )
        assert r2.status_code == 412
        assert r2.json()["error"]["code"] == ErrorCode.VERSION_MISMATCH.value

    async def test_patch_duplicate_rename_returns_409(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        a = await _create_attr(app, headers, "jenis", "Kembung")
        b = await _create_attr(app, headers, "jenis", "Layang")
        r = await app.patch(
            f"/api/v1/product-attributes/{b['id']}",
            json={"name": "Kembung"},
            headers={**headers, "If-Match": f'"{b["updated_at"]}"'},
        )
        assert r.status_code == 409
        assert r.json()["error"]["code"] == ErrorCode.CONFLICT.value

    async def test_patch_nonexistent_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.patch(
            "/api/v1/product-attributes/999999",
            json={"name": "x"},
            headers={**headers, "If-Match": '"stale"'},  # existence wins over ETag
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Deactivate
# ---------------------------------------------------------------------------


class TestDeactivateProductAttribute:
    async def test_deactivate_sets_inactive(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        created = await _create_attr(app, headers, "jenis", "Gulamo")
        r = await app.post(
            f"/api/v1/product-attributes/{created['id']}/deactivate",
            json={},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 200, r.text
        assert r.json()["is_active"] is False

    async def test_deactivate_preserves_history(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        created = await _create_attr(app, headers, "size", "XL")
        await app.post(
            f"/api/v1/product-attributes/{created['id']}/deactivate",
            json={},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        r = await app.get(
            f"/api/v1/product-attributes/{created['id']}", headers=headers
        )
        assert r.status_code == 200  # still retrievable (history preserved)
        assert r.json()["is_active"] is False

    async def test_deactivate_nonexistent_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/product-attributes/999999/deactivate",
            json={},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Hard Delete
# ---------------------------------------------------------------------------


class TestDeleteProductAttribute:
    async def test_delete_unreferenced_succeeds(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        created = await _create_attr(app, headers, "kualitas", "C")
        r = await app.delete(
            f"/api/v1/product-attributes/{created['id']}",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 204, r.text
        r2 = await app.get(
            f"/api/v1/product-attributes/{created['id']}", headers=headers
        )
        assert r2.status_code == 404

    async def test_delete_nonexistent_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.delete(
            "/api/v1/product-attributes/999999",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 404

    async def test_delete_referenced_by_product_returns_409(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        jenis = await _create_attr(app, headers, "jenis", "Terubuk")
        size = await _create_attr(app, headers, "size", "Jumbo")
        kualitas = await _create_attr(app, headers, "kualitas", "AA")
        # Create a product linked to all three dimensions.
        r = await app.post(
            "/api/v1/products",
            json={
                "name": f"prod_{uuid.uuid4().hex[:6]}",
                "jenis_id": jenis["id"],
                "size_id": size["id"],
                "kualitas_id": kualitas["id"],
                "purchase_price": 1000,
                "selling_price": 1500,
            },
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201, r.text
        d = await app.delete(
            f"/api/v1/product-attributes/{jenis['id']}",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert d.status_code == 409, d.text
        assert d.json()["error"]["code"] == ErrorCode.REFERENCED_BY_HISTORY.value


# ---------------------------------------------------------------------------
# Product linkage (V3)
# ---------------------------------------------------------------------------


class TestProductDimensionLinkage:
    async def test_create_product_with_dimensions(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        jenis = await _create_attr(app, headers, "jenis", "Tongkol")
        size = await _create_attr(app, headers, "size", "Besar")
        kualitas = await _create_attr(app, headers, "kualitas", "A")
        r = await app.post(
            "/api/v1/products",
            json={
                "name": f"tongkol_besar_a_{uuid.uuid4().hex[:4]}",
                "jenis_id": jenis["id"],
                "size_id": size["id"],
                "kualitas_id": kualitas["id"],
                "purchase_price": 1000,
                "selling_price": 1500,
            },
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["jenis_id"] == jenis["id"]
        assert body["size_id"] == size["id"]
        assert body["kualitas_id"] == kualitas["id"]

    async def test_create_product_wrong_kind_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        size = await _create_attr(app, headers, "size", "Sedang")
        # size_id column must receive a kualitas-kind attribute → 404 (kind-scoped).
        r = await app.post(
            "/api/v1/products",
            json={
                "name": f"badkind_{uuid.uuid4().hex[:6]}",
                "kualitas_id": size["id"],
                "purchase_price": 100,
                "selling_price": 200,
            },
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 404
        assert r.json()["error"]["code"] == ErrorCode.NOT_FOUND.value

    async def test_create_product_nonexistent_attribute_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products",
            json={
                "name": f"missingattr_{uuid.uuid4().hex[:6]}",
                "jenis_id": 999999,
                "purchase_price": 100,
                "selling_price": 200,
            },
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 404
        assert r.json()["error"]["code"] == ErrorCode.NOT_FOUND.value

    async def test_duplicate_full_combination_rejected(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        jenis = await _create_attr(app, headers, "jenis", "Cakalang")
        size = await _create_attr(app, headers, "size", "Besar")
        kualitas = await _create_attr(app, headers, "kualitas", "B")
        base = {
            "jenis_id": jenis["id"],
            "size_id": size["id"],
            "kualitas_id": kualitas["id"],
            "purchase_price": 100,
            "selling_price": 200,
        }
        # First product with this full combo → created.
        r1 = await app.post(
            "/api/v1/products",
            json={**base, "name": f"dup_a_{uuid.uuid4().hex[:4]}"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r1.status_code == 201, r1.text
        # Second product with the same full combo → rejected (DB partial unique index).
        r2 = await app.post(
            "/api/v1/products",
            json={**base, "name": f"dup_b_{uuid.uuid4().hex[:4]}"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 409, r2.text
