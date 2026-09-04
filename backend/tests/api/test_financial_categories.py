"""M2 foundation — financial_categories API tests.

Covers:
* create valid category
* duplicate code prevention (409)
* invalid data rejection (Pydantic)
* create as staff (403)
* update behavior (PATCH)
* deactivate behavior
* delete (hard delete when unreferenced; 404 otherwise)

The blacklist rule itself is tested exhaustively in test_blacklist.py.
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


def _make_body(**overrides: Any) -> dict[str, Any]:
    return {
        "code": f"cat_{uuid.uuid4().hex[:8]}",
        "name": "Misc",
        "entry_type": "expense",
        "is_active": True,
        **overrides,
    }


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


class TestListFinancialCategories:
    async def test_list_as_owner_returns_seed_rows(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Owner sees seeded categories."""
        headers = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/financial-categories/", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        names = {row["name"] for row in body["data"]}
        assert {"Rent", "Capital Injection", "Tax"} <= names
        assert body["pagination"]["total"] >= 11

    async def test_list_without_auth_returns_401(self, app: AsyncClient) -> None:
        r = await app.get("/api/v1/financial-categories/")
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestCreateFinancialCategory:
    async def test_create_income(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        body = _make_body(name="Side gig income", entry_type="income")
        r = await app.post(
            "/api/v1/financial-categories/",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201, r.text
        out = r.json()
        assert out["name"] == "Side gig income"
        assert out["entry_type"] == "income"
        assert out["is_active"] is True

    async def test_create_expense(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        body = _make_body(name="Office supplies", entry_type="expense")
        r = await app.post(
            "/api/v1/financial-categories/",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201, r.text

    async def test_create_invalid_entry_type_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        body = _make_body(entry_type="revenue")  # not income|expense
        r = await app.post(
            "/api/v1/financial-categories/",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == ErrorCode.VALIDATION_FAILED.value

    async def test_create_duplicate_code_returns_409(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Duplicate code → 409 conflict."""
        headers = await _owner_headers(app, owner_user)
        # Use a fixed code to force a duplicate.
        body = {
            "code": f"dup_{uuid.uuid4().hex[:8]}",
            "name": "First",
            "entry_type": "income",
            "is_active": True,
        }
        r1 = await app.post(
            "/api/v1/financial-categories/",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r1.status_code == 201, r1.text

        body2 = {
            "code": body["code"],
            "name": "Second",
            "entry_type": "income",
            "is_active": True,
        }
        r2 = await app.post(
            "/api/v1/financial-categories/",
            json=body2,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 409, r2.text
        assert r2.json()["error"]["code"] == ErrorCode.CONFLICT.value

    async def test_create_missing_idempotency_key_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/financial-categories/",
            json=_make_body(),
            headers=headers,
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.MISSING_HEADER.value

    async def test_create_as_staff_returns_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.post(
            "/api/v1/financial-categories/",
            json=_make_body(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == ErrorCode.PERMISSION_DENIED.value


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


class TestGetFinancialCategory:
    async def test_get_existing(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        # 'Rent' is the 4th seeded financial category (income: 1-3, expense: 4=rent)
        # Verify by reading the list first.
        r = await app.get("/api/v1/financial-categories/", headers=headers)
        cats = r.json()["data"]
        rent = next(c for c in cats if c["name"] == "Rent")
        r2 = await app.get(f"/api/v1/financial-categories/{rent['id']}", headers=headers)
        assert r2.status_code == 200
        assert r2.json()["name"] == "Rent"


# ---------------------------------------------------------------------------
# Update (PATCH)
# ---------------------------------------------------------------------------


class TestUpdateFinancialCategory:
    async def test_update_name(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        body = _make_body(name="Original", entry_type="expense")
        r = await app.post(
            "/api/v1/financial-categories/",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201
        cat_id = r.json()["id"]
        updated_at = r.json()["updated_at"]

        r2 = await app.patch(
            f"/api/v1/financial-categories/{cat_id}",
            json={"name": "Updated name"},
            headers={**headers, "If-Match": f'"{updated_at}"'},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["name"] == "Updated name"

    async def test_update_code_is_ignored_or_rejected(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """code is immutable — FinancialCategoryPatch does not even expose it."""
        headers = await _owner_headers(app, owner_user)
        body = _make_body(name="Test code imm", entry_type="expense")
        r = await app.post(
            "/api/v1/financial-categories/",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cat_id = r.json()["id"]
        updated_at = r.json()["updated_at"]

        # Patch with code → 400 (extra field forbidden)
        r2 = await app.patch(
            f"/api/v1/financial-categories/{cat_id}",
            json={"code": "new_code"},
            headers={**headers, "If-Match": f'"{updated_at}"'},
        )
        assert r2.status_code == 400

    async def test_update_nonexistent_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.patch(
            "/api/v1/financial-categories/999999",
            json={"name": "X"},
            headers={**headers, "If-Match": '"2024-01-01T00:00:00+00:00"'},
        )
        assert r.status_code == 404

    async def test_update_without_if_match_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """If-Match is REQUIRED on PATCH per OpenAPI §15.7.5."""
        headers = await _owner_headers(app, owner_user)
        body = _make_body(name="no_if_match", entry_type="expense")
        r = await app.post(
            "/api/v1/financial-categories/",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cat_id = r.json()["id"]

        r2 = await app.patch(
            f"/api/v1/financial-categories/{cat_id}",
            json={"name": "ignored"},
            headers=headers,
        )
        assert r2.status_code == 400
        assert r2.json()["error"]["code"] == ErrorCode.MISSING_HEADER.value

    async def test_update_with_invalid_if_match_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        body = _make_body(name="bad_if_match", entry_type="expense")
        r = await app.post(
            "/api/v1/financial-categories/",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cat_id = r.json()["id"]

        r2 = await app.patch(
            f"/api/v1/financial-categories/{cat_id}",
            json={"name": "ignored"},
            headers={**headers, "If-Match": "not-a-quoted-etag"},
        )
        assert r2.status_code == 400

    async def test_update_with_stale_if_match_returns_412(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Concurrent-update detection: stale ETag → 412 version_mismatch."""
        headers = await _owner_headers(app, owner_user)
        body = _make_body(name="stale_if_match", entry_type="expense")
        r = await app.post(
            "/api/v1/financial-categories/",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cat_id = r.json()["id"]
        original_etag = r.json()["updated_at"]

        # First PATCH succeeds — advances updated_at.
        r2 = await app.patch(
            f"/api/v1/financial-categories/{cat_id}",
            json={"name": "renamed"},
            headers={**headers, "If-Match": f'"{original_etag}"'},
        )
        assert r2.status_code == 200, r2.text

        # Second PATCH with the now-stale ETag → 412.
        r3 = await app.patch(
            f"/api/v1/financial-categories/{cat_id}",
            json={"name": "renamed_again"},
            headers={**headers, "If-Match": f'"{original_etag}"'},
        )
        assert r3.status_code == 412
        assert r3.json()["error"]["code"] == ErrorCode.VERSION_MISMATCH.value


# ---------------------------------------------------------------------------
# Deactivate
# ---------------------------------------------------------------------------


class TestDeactivateFinancialCategory:
    async def test_deactivate_sets_inactive(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        body = _make_body(name="Deact Test", entry_type="expense")
        r = await app.post(
            "/api/v1/financial-categories/",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201
        cat_id = r.json()["id"]

        r2 = await app.post(
            f"/api/v1/financial-categories/{cat_id}/deactivate",
            json={"reason": "done"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["is_active"] is False

        # History preserved — GET still returns it
        r3 = await app.get(f"/api/v1/financial-categories/{cat_id}", headers=headers)
        assert r3.status_code == 200
        assert r3.json()["is_active"] is False


# ---------------------------------------------------------------------------
# Delete (hard)
# ---------------------------------------------------------------------------


class TestDeleteFinancialCategory:
    async def test_delete_unreferenced_succeeds(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        body = _make_body(name="Delete Me", entry_type="expense")
        r = await app.post(
            "/api/v1/financial-categories/",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201
        cat_id = r.json()["id"]

        r2 = await app.delete(
            f"/api/v1/financial-categories/{cat_id}",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 204, r2.text

        r3 = await app.get(f"/api/v1/financial-categories/{cat_id}", headers=headers)
        assert r3.status_code == 404
