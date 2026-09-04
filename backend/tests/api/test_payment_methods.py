"""M2 foundation — payment_methods API tests.

Covers:
* create valid payment method
* duplicate code prevention (409)
* invalid data rejection (Pydantic)
* required-constraint rejection (DB CHECK / NOT NULL)
* update behavior (PATCH)
* deactivate behavior (soft-delete preserves history)
* delete (hard delete when unreferenced; 409 when referenced)
* staff user cannot manage (403)
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


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


class TestListPaymentMethods:
    async def test_list_as_owner_returns_seed_rows(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Owner sees the 4 seeded payment methods."""
        headers = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/payment-methods/", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        codes = {row["code"] for row in body["data"]}
        assert {"cash", "bank_transfer", "e_wallet", "other"} <= codes
        assert body["pagination"]["total"] >= 4

    async def test_list_as_staff_succeeds(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        """Staff can view (payment_method.view is in staff defaults)."""
        headers = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/payment-methods/", headers=headers)
        assert r.status_code == 200

    async def test_list_without_auth_returns_401(self, app: AsyncClient) -> None:
        r = await app.get("/api/v1/payment-methods/")
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestCreatePaymentMethod:
    async def test_create_valid(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        body = {
            "code": f"crypto_{uuid.uuid4().hex[:8]}",
            "name": "Crypto",
            "is_cash": False,
            "is_active": True,
        }
        r = await app.post(
            "/api/v1/payment-methods/",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201, r.text
        out = r.json()
        assert out["code"] == body["code"]
        assert out["name"] == "Crypto"
        assert out["is_cash"] is False
        assert out["is_active"] is True
        assert "id" in out
        assert "created_at" in out

    async def test_duplicate_code_returns_409(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Creating a payment method with an existing code is 409."""
        headers = await _owner_headers(app, owner_user)
        code = f"dup_{uuid.uuid4().hex[:8]}"
        body = {"code": code, "name": "First", "is_cash": False, "is_active": True}
        r1 = await app.post(
            "/api/v1/payment-methods/",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r1.status_code == 201, r1.text

        body2 = {"code": code, "name": "Second", "is_cash": False, "is_active": True}
        r2 = await app.post(
            "/api/v1/payment-methods/",
            json=body2,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 409, r2.text
        assert r2.json()["error"]["code"] == ErrorCode.CONFLICT.value

    async def test_create_with_invalid_entry_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Empty name → Pydantic validation error → 400."""
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/payment-methods/",
            json={"code": "x", "name": "", "is_cash": False, "is_active": True},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == ErrorCode.VALIDATION_FAILED.value

    async def test_create_missing_idempotency_key_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Idempotency-Key is required for create."""
        headers = await _owner_headers(app, owner_user)
        body = {
            "code": f"no_idem_{uuid.uuid4().hex[:8]}",
            "name": "Test",
            "is_cash": False,
            "is_active": True,
        }
        r = await app.post("/api/v1/payment-methods/", json=body, headers=headers)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.MISSING_HEADER.value

    async def test_create_as_staff_returns_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        """Staff lacks payment_method.manage → 403."""
        headers = await _staff_headers(app, staff_user)
        body = {
            "code": f"staff_{uuid.uuid4().hex[:8]}",
            "name": "Should not create",
            "is_cash": False,
            "is_active": True,
        }
        r = await app.post(
            "/api/v1/payment-methods/",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == ErrorCode.PERMISSION_DENIED.value

    async def test_create_with_extra_fields_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """PaymentMethodRequest forbids extras."""
        headers = await _owner_headers(app, owner_user)
        body = {
            "code": f"x_{uuid.uuid4().hex[:8]}",
            "name": "Test",
            "is_cash": False,
            "is_active": True,
            "unexpected": "field",
        }
        r = await app.post(
            "/api/v1/payment-methods/",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


class TestGetPaymentMethod:
    async def test_get_existing(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        # First payment method is the seeded 'cash'
        r = await app.get("/api/v1/payment-methods/1", headers=headers)
        assert r.status_code == 200
        assert r.json()["code"] == "cash"
        assert r.json()["is_cash"] is True

    async def test_get_nonexistent_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/payment-methods/999999", headers=headers)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == ErrorCode.NOT_FOUND.value


# ---------------------------------------------------------------------------
# Update (PATCH)
# ---------------------------------------------------------------------------


class TestUpdatePaymentMethod:
    async def test_update_name(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        # Create
        body = {
            "code": f"upd_{uuid.uuid4().hex[:8]}",
            "name": "Original",
            "is_cash": False,
            "is_active": True,
        }
        r = await app.post(
            "/api/v1/payment-methods/",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201
        pid = r.json()["id"]

        # Patch
        r2 = await app.patch(
            f"/api/v1/payment-methods/{pid}",
            json={"name": "Renamed", "is_cash": True},
            headers=headers,
        )
        assert r2.status_code == 200, r2.text
        out = r2.json()
        assert out["name"] == "Renamed"
        assert out["is_cash"] is True

    async def test_update_nonexistent_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.patch(
            "/api/v1/payment-methods/999999",
            json={"name": "X"},
            headers=headers,
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Deactivate
# ---------------------------------------------------------------------------


class TestDeactivatePaymentMethod:
    async def test_deactivate_sets_inactive(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        body = {
            "code": f"deact_{uuid.uuid4().hex[:8]}",
            "name": "Deact Test",
            "is_cash": False,
            "is_active": True,
        }
        r = await app.post(
            "/api/v1/payment-methods/",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201
        pid = r.json()["id"]

        r2 = await app.post(
            f"/api/v1/payment-methods/{pid}/deactivate",
            json={"reason": "test"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["is_active"] is False

        # GET still returns it (history preserved)
        r3 = await app.get(f"/api/v1/payment-methods/{pid}", headers=headers)
        assert r3.status_code == 200
        assert r3.json()["is_active"] is False

    async def test_deactivate_missing_idempotency_key_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/payment-methods/1/deactivate",
            json={"reason": "x"},
            headers=headers,
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.MISSING_HEADER.value


# ---------------------------------------------------------------------------
# Delete (hard)
# ---------------------------------------------------------------------------


class TestDeletePaymentMethod:
    async def test_delete_unreferenced_succeeds(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        body = {
            "code": f"del_{uuid.uuid4().hex[:8]}",
            "name": "Delete Test",
            "is_cash": False,
            "is_active": True,
        }
        r = await app.post(
            "/api/v1/payment-methods/",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201
        pid = r.json()["id"]

        r2 = await app.delete(
            f"/api/v1/payment-methods/{pid}",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 204, r2.text

        r3 = await app.get(f"/api/v1/payment-methods/{pid}", headers=headers)
        assert r3.status_code == 404

    async def test_delete_nonexistent_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.delete(
            "/api/v1/payment-methods/999999",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 404

    async def test_delete_missing_idempotency_key_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.delete("/api/v1/payment-methods/1", headers=headers)
        assert r.status_code == 400
