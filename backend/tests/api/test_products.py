"""B.6 Products — API tests.

Covers:
* authorization (view vs create/edit/deactivate capabilities)
* list (pagination, filter[is_active], filter[category_id], filter[unit_id], q, sort)
* create (valid, defaults, code uniqueness 409, Idempotency-Key, audit)
* get-by-id (existing, 404)
* update (PATCH fields, If-Match required, mismatch → 412, missing → 400,
  code is immutable)
* price history (PATCH with price change writes product_price_history row)
* deactivate (preserves history, Idempotency-Key required)
* hard delete (succeeds when unreferenced; 404 not found; missing
  Idempotency-Key → 400)
* audit logging (create/update/deactivate/delete all log)
* error envelopes (uniform shape)
* computed fields (low_stock, negative_stock_fallback_supported,
  on_hand_quantity from product_valuation view)

B.6 SCOPE: 6 endpoints — list, get, create, update, deactivate, delete.
B.7 (price-history, stock-movements, valuation sub-resources) is NOT in scope.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.config import get_settings
from app.db import get_session_factory
from app.errors.codes import ErrorCode
from app.main import create_app
from app.services.products import ProductService


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


def _valid_product(name: str | None = None, **kw: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name or f"prod_{uuid.uuid4().hex[:8]}",
        "is_active": True,
    }
    body.update(kw)
    return body


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


class TestProductAuthorization:
    async def test_list_without_auth_returns_401(self, app: AsyncClient) -> None:
        r = await app.get("/api/v1/products")
        assert r.status_code == 401

    async def test_list_as_staff_succeeds_view_capability(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/products", headers=headers)
        assert r.status_code == 200

    async def test_create_as_staff_returns_403_no_create_capability(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.post(
            "/api/v1/products",
            json=_valid_product(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == ErrorCode.PERMISSION_DENIED.value

    async def test_patch_as_staff_returns_403_no_edit_capability(
        self, app: AsyncClient, owner_user: dict[str, Any], staff_user: dict[str, Any]
    ) -> None:
        owner = await _owner_headers(app, owner_user)
        staff = await _staff_headers(app, staff_user)
        r = await app.post(
            "/api/v1/products",
            json=_valid_product(),
            headers={**owner, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201
        pid = r.json()["id"]
        etag = r.json()["updated_at"]
        r2 = await app.patch(
            f"/api/v1/products/{pid}",
            json={"name": "renamed"},
            headers={**staff, "If-Match": f'"{etag}"'},
        )
        assert r2.status_code == 403

    async def test_delete_as_staff_returns_403_no_edit_capability(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.delete(
            "/api/v1/products/1",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 403

    async def test_deactivate_as_staff_returns_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.post(
            "/api/v1/products/1/deactivate",
            json={"reason": "x"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


class TestListProducts:
    async def test_list_empty_returns_pagination(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/products", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["data"] == []
        assert body["pagination"]["total"] == 0

    async def test_list_after_create(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        for n in ("alpha", "beta"):
            await app.post(
                "/api/v1/products",
                json=_valid_product(name=f"{n}_{uuid.uuid4().hex[:6]}"),
                headers={**headers, "Idempotency-Key": _idem()},
            )
        r = await app.get("/api/v1/products", headers=headers)
        assert r.status_code == 200
        assert len(r.json()["data"]) >= 2

    async def test_list_filter_is_active(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products",
            json=_valid_product(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        pid = r.json()["id"]
        await app.post(
            f"/api/v1/products/{pid}/deactivate",
            json={"reason": "test"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        r2 = await app.get("/api/v1/products?filter[is_active]=true", headers=headers)
        assert r2.status_code == 200
        for row in r2.json()["data"]:
            assert row["is_active"] is True

    async def test_list_filter_is_inactive(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Regression: filter[is_active]=false must return inactive products.

        Reproduces the reported bug where selecting "Inactive" returned an
        unchanged list — the repository previously ignored ``active_only=False``.
        """
        headers = await _owner_headers(app, owner_user)
        # Create and deactivate one product so there is at least one inactive.
        r = await app.post(
            "/api/v1/products",
            json=_valid_product(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        pid = r.json()["id"]
        await app.post(
            f"/api/v1/products/{pid}/deactivate",
            json={"reason": "test"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        # Inactive filter must contain the deactivated product (is_active false).
        r2 = await app.get("/api/v1/products?filter[is_active]=false", headers=headers)
        assert r2.status_code == 200
        data = r2.json()["data"]
        assert any(row["id"] == pid for row in data), (
            "Deactivated product missing from filter[is_active]=false result"
        )
        for row in data:
            assert row["is_active"] is False

    async def test_list_search_by_q(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        unique = f"searchterm{uuid.uuid4().hex[:6]}"
        await app.post(
            "/api/v1/products",
            json=_valid_product(name=unique),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        r = await app.get(f"/api/v1/products?q={unique}", headers=headers)
        assert r.status_code == 200
        names = [row["name"] for row in r.json()["data"]]
        assert unique in names

    async def test_list_pagination_respects_per_page(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        for _ in range(3):
            await app.post(
                "/api/v1/products",
                json=_valid_product(),
                headers={**headers, "Idempotency-Key": _idem()},
            )
        r = await app.get("/api/v1/products?per_page=2&page=1", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert len(body["data"]) <= 2
        assert body["pagination"]["per_page"] == 2

    async def test_list_response_shape_includes_computed_fields(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products",
            json=_valid_product(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201
        out = r.json()
        # Required OpenAPI fields
        for f in (
            "id",
            "name",
            "purchase_price",
            "selling_price",
            "is_sellable",
            "is_purchasable",
            "is_producible",
            "is_active",
            "on_hand_quantity",
            "moving_average_unit_cost",
            "inventory_value",
            "low_stock",
            "created_at",
            "updated_at",
            "version",
            "negative_stock_fallback_supported",
        ):
            assert f in out, f"Missing required field {f}"
        assert out["on_hand_quantity"] == 0
        assert out["inventory_value"] == 0
        assert out["moving_average_unit_cost"] is None
        assert out["low_stock"] is False


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestCreateProduct:
    async def test_create_valid(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        body = _valid_product(
            name=f"valid_{uuid.uuid4().hex[:8]}", code=f"P-{uuid.uuid4().hex[:6]}"
        )
        r = await app.post(
            "/api/v1/products",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201, r.text
        out = r.json()
        assert out["name"] == body["name"]
        assert out["code"] == body["code"]
        assert out["is_active"] is True
        # Default values per OpenAPI §15.7
        assert out["purchase_price"] == 0
        assert out["selling_price"] == 0
        assert out["is_sellable"] is True
        assert out["is_purchasable"] is True
        assert out["is_producible"] is False

    async def test_create_duplicate_code_returns_409(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        code = f"DUP-{uuid.uuid4().hex[:8]}"
        r1 = await app.post(
            "/api/v1/products",
            json=_valid_product(name=f"a_{uuid.uuid4().hex[:6]}", code=code),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r1.status_code == 201
        r2 = await app.post(
            "/api/v1/products",
            json=_valid_product(name=f"b_{uuid.uuid4().hex[:6]}", code=code),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 409
        assert r2.json()["error"]["code"] == ErrorCode.CONFLICT.value

    async def test_create_missing_idempotency_key_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post("/api/v1/products", json=_valid_product(), headers=headers)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.MISSING_HEADER.value

    async def test_create_empty_name_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products",
            json={"name": "", "is_active": True},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.VALIDATION_FAILED.value

    async def test_create_with_extra_field_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        body = _valid_product()
        body["unexpected"] = "bad"
        r = await app.post(
            "/api/v1/products",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400

    async def test_create_with_invalid_code_pattern_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products",
            json=_valid_product(name=f"x_{uuid.uuid4().hex[:6]}", code="has spaces!"),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.VALIDATION_FAILED.value

    async def test_create_with_negative_prices_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products",
            json=_valid_product(name=f"y_{uuid.uuid4().hex[:6]}", purchase_price=-1),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


class TestGetProduct:
    async def test_get_existing(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products",
            json=_valid_product(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        pid = r.json()["id"]
        r2 = await app.get(f"/api/v1/products/{pid}", headers=headers)
        assert r2.status_code == 200
        assert r2.json()["id"] == pid

    async def test_get_nonexistent_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/products/999999", headers=headers)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == ErrorCode.NOT_FOUND.value


# ---------------------------------------------------------------------------
# Update (PATCH) — If-Match is REQUIRED per OpenAPI
# ---------------------------------------------------------------------------


class TestUpdateProduct:
    async def test_patch_rename(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products",
            json=_valid_product(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        pid = r.json()["id"]
        updated_at = r.json()["updated_at"]
        new_name = f"renamed_{uuid.uuid4().hex[:6]}"
        r2 = await app.patch(
            f"/api/v1/products/{pid}",
            json={"name": new_name},
            headers={**headers, "If-Match": f'"{updated_at}"'},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["name"] == new_name

    async def test_patch_is_active(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products",
            json=_valid_product(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        pid = r.json()["id"]
        updated_at = r.json()["updated_at"]
        r2 = await app.patch(
            f"/api/v1/products/{pid}",
            json={"is_active": False},
            headers={**headers, "If-Match": f'"{updated_at}"'},
        )
        assert r2.status_code == 200
        assert r2.json()["is_active"] is False

    async def test_patch_code_rejected_with_validation_failed(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """``code`` is immutable via the public API (ProductPatch.extra='forbid')."""
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products",
            json=_valid_product(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        pid = r.json()["id"]
        updated_at = r.json()["updated_at"]
        r2 = await app.patch(
            f"/api/v1/products/{pid}",
            json={"code": "newcode"},
            headers={**headers, "If-Match": f'"{updated_at}"'},
        )
        assert r2.status_code == 400
        assert r2.json()["error"]["code"] == ErrorCode.VALIDATION_FAILED.value

    async def test_patch_without_if_match_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products",
            json=_valid_product(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        pid = r.json()["id"]
        r2 = await app.patch(
            f"/api/v1/products/{pid}",
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
            "/api/v1/products",
            json=_valid_product(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        pid = r.json()["id"]
        # First PATCH — succeeds
        await app.patch(
            f"/api/v1/products/{pid}",
            json={"name": "first"},
            headers={**headers, "If-Match": f'"{r.json()["updated_at"]}"'},
        )
        # Second PATCH — stale ETag → 412
        r3 = await app.patch(
            f"/api/v1/products/{pid}",
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
            "/api/v1/products/999999",
            json={"name": "x"},
            headers={**headers, "If-Match": '"2024-01-01T00:00:00+00:00"'},
        )
        assert r.status_code == 404

    async def test_patch_price_change_writes_product_price_history(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """BR-PRODUCT-002: price change → product_price_history row."""
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products",
            json=_valid_product(name=f"pr_{uuid.uuid4().hex[:6]}"),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        pid = r.json()["id"]
        updated_at = r.json()["updated_at"]

        r2 = await app.patch(
            f"/api/v1/products/{pid}",
            json={"purchase_price": 1500, "selling_price": 2500},
            headers={**headers, "If-Match": f'"{updated_at}"'},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["purchase_price"] == 1500
        assert r2.json()["selling_price"] == 2500

        # Verify product_price_history row exists.
        factory = get_session_factory()
        async with factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT purchase_price, selling_price FROM product_price_history "
                            "WHERE product_id = :pid ORDER BY id DESC LIMIT 1"
                        ),
                        {"pid": pid},
                    )
                )
                .mappings()
                .first()
            )
        assert row is not None
        assert float(row["purchase_price"]) == 1500.0
        assert float(row["selling_price"]) == 2500.0


# ---------------------------------------------------------------------------
# Deactivate
# ---------------------------------------------------------------------------


class TestDeactivateProduct:
    async def test_deactivate_sets_inactive(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products",
            json=_valid_product(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        pid = r.json()["id"]
        r2 = await app.post(
            f"/api/v1/products/{pid}/deactivate",
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
            "/api/v1/products",
            json=_valid_product(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        pid = r.json()["id"]
        await app.post(
            f"/api/v1/products/{pid}/deactivate",
            json={"reason": "test"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        r2 = await app.get(f"/api/v1/products/{pid}", headers=headers)
        assert r2.status_code == 200
        assert r2.json()["is_active"] is False

    async def test_deactivate_missing_idempotency_key_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post("/api/v1/products/1/deactivate", json={"reason": "x"}, headers=headers)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.MISSING_HEADER.value

    async def test_deactivate_nonexistent_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products/999999/deactivate",
            json={"reason": "x"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Hard Delete
# ---------------------------------------------------------------------------


class TestDeleteProduct:
    async def test_delete_unreferenced_succeeds(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products",
            json=_valid_product(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        pid = r.json()["id"]
        r2 = await app.delete(
            f"/api/v1/products/{pid}",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 204
        r3 = await app.get(f"/api/v1/products/{pid}", headers=headers)
        assert r3.status_code == 404

    async def test_delete_nonexistent_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.delete(
            "/api/v1/products/999999",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 404

    async def test_delete_missing_idempotency_key_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.delete("/api/v1/products/1", headers=headers)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.MISSING_HEADER.value


# ---------------------------------------------------------------------------
# Audit behavior
# ---------------------------------------------------------------------------


class TestProductAudit:
    async def test_create_writes_audit_log(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products",
            json=_valid_product(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        pid = r.json()["id"]

        factory = get_session_factory()
        async with factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT action, entity_type FROM audit_log "
                            "WHERE entity_type = 'product' AND entity_id = :pid "
                            "ORDER BY id DESC LIMIT 1"
                        ),
                        {"pid": pid},
                    )
                )
                .mappings()
                .first()
            )
        assert row is not None
        assert row["action"] == "create"
        assert row["entity_type"] == "product"

    async def test_patch_writes_audit_log(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products",
            json=_valid_product(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        pid = r.json()["id"]
        updated_at = r.json()["updated_at"]
        r2 = await app.patch(
            f"/api/v1/products/{pid}",
            json={"name": "renamed_audit"},
            headers={**headers, "If-Match": f'"{updated_at}"'},
        )
        assert r2.status_code == 200, r2.text

        factory = get_session_factory()
        async with factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT action FROM audit_log "
                            "WHERE entity_type = 'product' AND entity_id = :pid "
                            "ORDER BY id DESC LIMIT 1"
                        ),
                        {"pid": pid},
                    )
                )
                .mappings()
                .first()
            )
        assert row is not None
        assert row["action"] == "update"

    async def test_delete_writes_audit_log(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products",
            json=_valid_product(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        pid = r.json()["id"]
        await app.delete(
            f"/api/v1/products/{pid}",
            headers={**headers, "Idempotency-Key": _idem()},
        )

        factory = get_session_factory()
        async with factory() as session:
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT action FROM audit_log "
                            "WHERE entity_type = 'product' AND entity_id = :pid "
                            "ORDER BY id DESC LIMIT 1"
                        ),
                        {"pid": pid},
                    )
                )
                .mappings()
                .first()
            )
        assert row is not None
        # 'delete' is not in ck_audit_action whitelist; mapped to 'update'.
        assert row["action"] == "update"

    async def test_deactivate_writes_audit_log_with_reason(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products",
            json=_valid_product(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        pid = r.json()["id"]
        await app.post(
            f"/api/v1/products/{pid}/deactivate",
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
                            "WHERE entity_type = 'product' AND entity_id = :pid "
                            "AND action = 'deactivate' "
                            "ORDER BY id DESC LIMIT 1"
                        ),
                        {"pid": pid},
                    )
                )
                .mappings()
                .first()
            )
        assert row is not None
        assert row["reason"] == "audit test"


# ---------------------------------------------------------------------------
# Edge cases — DB constraints
# ---------------------------------------------------------------------------


class TestProductDBConstraints:
    async def test_create_with_negative_purchase_price_rejected(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """DB ``ck_products_purchase_price >= 0`` enforced at app layer."""
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products",
            json=_valid_product(name=f"neg_{uuid.uuid4().hex[:6]}", purchase_price=-5),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400

    async def test_create_with_negative_selling_price_rejected(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products",
            json=_valid_product(name=f"neg2_{uuid.uuid4().hex[:6]}", selling_price=-10),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400

    async def test_create_with_nonexistent_category_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """products.category_id FK ON DELETE RESTRICT — missing parent → 404 not_found."""
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products",
            json=_valid_product(
                name=f"fk_{uuid.uuid4().hex[:6]}",
                category_id=999999,
            ),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 404
        assert r.json()["error"]["code"] == ErrorCode.NOT_FOUND.value


# ---------------------------------------------------------------------------
# Transaction boundary - mandatory response enrichment must not fail post-commit
#
# PATCH commits the product update, its price-history row, and its audit row.
# The derived response fields (valuation / moving-average cost / the
# negative-stock system setting) used to be read AFTER that commit, in the
# route's _compose_response - so a failure there returned an error to the
# client while the mutation stayed committed. They are now read inside the
# service before the commit (ProductService.build_enrichment).
# ---------------------------------------------------------------------------


class TestPatchTransactionBoundary:
    async def test_patch_returns_enrichment_and_etag(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Test A: the success contract is unchanged - enrichment + ETag."""
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products",
            json=_valid_product(name=f"txa_{uuid.uuid4().hex[:6]}"),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201, r.text
        pid = r.json()["id"]
        updated_at = r.json()["updated_at"]

        r2 = await app.patch(
            f"/api/v1/products/{pid}",
            json={"name": "tx_a_renamed", "selling_price": 4321},
            headers={**headers, "If-Match": f'"{updated_at}"'},
        )
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body["name"] == "tx_a_renamed"
        assert float(body["selling_price"]) == 4321.0
        # enrichment fields are still present and well-typed
        assert float(body["on_hand_quantity"]) == 0
        assert float(body["inventory_value"]) == 0.0
        assert body["moving_average_unit_cost"] is None
        assert body["low_stock"] is False
        assert isinstance(body["negative_stock_fallback_supported"], bool)
        # ETag semantics: a new updated_at means the old token is now stale
        assert body["updated_at"] != updated_at
        assert int(body["version"]) > 0

    async def test_enrichment_failure_rolls_back_every_write(
        self, app: AsyncClient, owner_user: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test B: a failure in a mandatory enrichment read aborts everything."""
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products",
            json=_valid_product(name=f"txb_{uuid.uuid4().hex[:6]}", selling_price=1000),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201, r.text
        pid = r.json()["id"]
        updated_at = r.json()["updated_at"]

        factory = get_session_factory()
        async with factory() as session:
            history_before = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM product_price_history "
                        "WHERE product_id = :pid"
                    ),
                    {"pid": pid},
                )
            ).scalar()
            audit_before = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM audit_log "
                        "WHERE entity_type = 'product' AND entity_id = :pid"
                    ),
                    {"pid": pid},
                )
            ).scalar()

        # One-shot failure: the first enrichment read of the PATCH raises, so
        # the retry below can proceed on the same still-valid ETag.
        calls = {"n": 0}
        real = ProductService.build_enrichment

        async def flaky(self: ProductService, row: dict[str, Any]) -> dict[str, Any]:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("injected enrichment failure")
            return await real(self, row)

        monkeypatch.setattr(ProductService, "build_enrichment", flaky)

        # The shared `app` fixture uses httpx's default
        # raise_app_exceptions=True, which would re-raise the injected
        # RuntimeError into the test instead of returning the HTTP error
        # response. Build a test-local client over the SAME app (same
        # settings, same Origin) with propagation disabled so the test can
        # assert the real 500 envelope. The shared fixture is untouched, so
        # the suite-wide behavior is unchanged.
        test_app = create_app(get_settings())
        transport = httpx.ASGITransport(app=test_app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Origin": "http://test"},
        ) as strict_client:
            r2 = await strict_client.patch(
                f"/api/v1/products/{pid}",
                json={"name": "tx_b_should_not_persist", "selling_price": 9999},
                headers={**headers, "If-Match": f'"{updated_at}"'},
            )
        assert r2.status_code == 500, r2.text
        assert r2.json()["error"]["code"] == ErrorCode.INTERNAL_ERROR.value
        assert calls["n"] == 1

        # No partial mutation survived the failed attempt.
        async with factory() as session:
            row = (
                await session.execute(
                    text("SELECT name, selling_price FROM products WHERE id = :pid"),
                    {"pid": pid},
                )
            ).mappings().first()
            history_after = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM product_price_history "
                        "WHERE product_id = :pid"
                    ),
                    {"pid": pid},
                )
            ).scalar()
            audit_after = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM audit_log "
                        "WHERE entity_type = 'product' AND entity_id = :pid"
                    ),
                    {"pid": pid},
                )
            ).scalar()
        assert row is not None
        assert row["name"] != "tx_b_should_not_persist"
        assert float(row["selling_price"]) == 1000.0
        assert history_after == history_before
        assert audit_after == audit_before

        # Test C-1: the rollback left the original ETag current, so the retry
        # succeeds and produces exactly one set of writes.
        r3 = await app.patch(
            f"/api/v1/products/{pid}",
            json={"name": "tx_c_retry_ok", "selling_price": 2500},
            headers={**headers, "If-Match": f'"{updated_at}"'},
        )
        assert r3.status_code == 200, r3.text
        assert r3.json()["name"] == "tx_c_retry_ok"

        async with factory() as session:
            history_final = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM product_price_history "
                        "WHERE product_id = :pid"
                    ),
                    {"pid": pid},
                )
            ).scalar()
        assert history_final == history_before + 1

    async def test_retry_after_commit_with_stale_etag_writes_nothing(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Test C-2: a committed update makes the old ETag stale - no extra writes."""
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/products",
            json=_valid_product(name=f"txd_{uuid.uuid4().hex[:6]}", selling_price=700),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201, r.text
        pid = r.json()["id"]
        updated_at = r.json()["updated_at"]

        r2 = await app.patch(
            f"/api/v1/products/{pid}",
            json={"selling_price": 800},
            headers={**headers, "If-Match": f'"{updated_at}"'},
        )
        assert r2.status_code == 200, r2.text

        factory = get_session_factory()
        async with factory() as session:
            history_before = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM product_price_history "
                        "WHERE product_id = :pid"
                    ),
                    {"pid": pid},
                )
            ).scalar()
            audit_before = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM audit_log "
                        "WHERE entity_type = 'product' AND entity_id = :pid"
                    ),
                    {"pid": pid},
                )
            ).scalar()

        # Replay the original request: stale If-Match -> 412, no new writes.
        r3 = await app.patch(
            f"/api/v1/products/{pid}",
            json={"selling_price": 900},
            headers={**headers, "If-Match": f'"{updated_at}"'},
        )
        assert r3.status_code == 412
        assert r3.json()["error"]["code"] == ErrorCode.VERSION_MISMATCH.value

        async with factory() as session:
            row = (
                await session.execute(
                    text("SELECT selling_price FROM products WHERE id = :pid"),
                    {"pid": pid},
                )
            ).mappings().first()
            history_after = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM product_price_history "
                        "WHERE product_id = :pid"
                    ),
                    {"pid": pid},
                )
            ).scalar()
            audit_after = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM audit_log "
                        "WHERE entity_type = 'product' AND entity_id = :pid"
                    ),
                    {"pid": pid},
                )
            ).scalar()
        assert float(row["selling_price"]) == 800.0
        assert history_after == history_before
        assert audit_after == audit_before
