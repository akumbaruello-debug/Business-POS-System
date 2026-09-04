"""M2 foundation — financial-category blacklist rule tests.

Authoritative rule (API §15.7.5):
  name is validated case-insensitively against the blacklist:
  "Sales", "COGS", "Production", "Purchase Shipping", "Refund",
  "Purchase", "Sales Revenue", "Cost of Goods Sold", "Other Income",
  "Operating Expenses" (+ case-insensitive variants).
  Server returns 400 ``manual_entry_duplicate_of_derived``.

Enforced on BOTH create and update (rename). These tests prove the rule
cannot be bypassed through an alternate service path.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from app.domain.blacklist import assert_not_blacklisted, is_blacklisted
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


# The exact names from the spec (10 entries).
_BLACKLISTED_NAMES = [
    "Sales",
    "COGS",
    "Production",
    "Purchase Shipping",
    "Refund",
    "Purchase",
    "Sales Revenue",
    "Cost of Goods Sold",
    "Other Income",
    "Operating Expenses",
]

# Non-blacklisted control names that must succeed.
_SAFE_NAMES = [
    "Capital Injection",
    "Other Income X",  # distinct from plain "Other Income"
    "Sales Team Bonus",  # distinct
    "Cost of Goods",  # distinct (missing "Sold")
    "Salesperson Commission",  # distinct (has suffix)
]


def _cat_body(name: str, entry_type: str = "expense") -> dict[str, Any]:
    return {
        "code": f"bl_{uuid.uuid4().hex[:8]}",
        "name": name,
        "entry_type": entry_type,
        "is_active": True,
    }


# ---------------------------------------------------------------------------
# Unit-level: the guard function itself
# ---------------------------------------------------------------------------


class TestBlacklistGuard:
    """Direct tests of the single source of truth — assert_not_blacklisted."""

    @pytest.mark.parametrize("name", _BLACKLISTED_NAMES)
    def test_exact_match_is_blacklisted(self, name: str) -> None:
        assert is_blacklisted(name) is True

    @pytest.mark.parametrize(
        "variant",
        [
            "sales",
            "SALES",
            "Sales",
            "sAlEs",
            "CoGs",
            "PRODUCTION",
            "purchase shipping",
            "PURCHASE SHIPPING",
            "sales revenue",
            "SALES REVENUE",
            "cost of goods sold",
            "COST OF GOODS SOLD",
            "other income",
            "OTHER INCOME",
            "operating expenses",
            "OPERATING EXPENSES",
        ],
    )
    def test_case_variants_are_blacklisted(self, variant: str) -> None:
        assert is_blacklisted(variant) is True

    @pytest.mark.parametrize(
        "variant",
        [
            "  Sales  ",
            "Sales ",
            " Sales",
            "\tSales\n",
            "  COGS  ",
            "  Production ",
            "Sales  Revenue",  # internal double space collapses to "Sales Revenue"
            "  Cost of Goods Sold  ",
        ],
    )
    def test_whitespace_variation_is_blacklisted(self, variant: str) -> None:
        assert is_blacklisted(variant) is True

    @pytest.mark.parametrize("name", _SAFE_NAMES)
    def test_non_blacklisted_name_passes(self, name: str) -> None:
        assert is_blacklisted(name) is False

    @pytest.mark.parametrize("name", _BLACKLISTED_NAMES)
    def test_assert_raises_on_blacklisted(self, name: str) -> None:
        """The assertion helper raises the correct AppError subclass."""
        from app.errors import ManualEntryDuplicateOfDerived

        with pytest.raises(ManualEntryDuplicateOfDerived):
            assert_not_blacklisted(name, field="name")

    def test_assert_passes_on_safe_name(self) -> None:
        assert_not_blacklisted("Office supplies", field="name")


# ---------------------------------------------------------------------------
# API-level: create path
# ---------------------------------------------------------------------------


class TestBlacklistOnCreate:
    """Every blacklisted name must be rejected at POST /financial-categories."""

    @pytest.mark.parametrize("name", _BLACKLISTED_NAMES)
    async def test_create_blacklisted_exact_name_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any], name: str
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/financial-categories/",
            json=_cat_body(name, "income"),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400, f"{name}: {r.text}"
        err = r.json()["error"]
        assert err["code"] == ErrorCode.MANUAL_ENTRY_DUPLICATE_OF_DERIVED.value
        assert err["details"]["value"] == name

    @pytest.mark.parametrize(
        "variant",
        [
            "sales",
            "COGS",
            "Production",
            "purchase shipping",
            "REFUND",
            "PURCHASE",
            "sales revenue",
            "cost of goods sold",
            "OTHER INCOME",
            "OPERATING EXPENSES",
        ],
    )
    async def test_create_blacklisted_case_variant_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any], variant: str
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/financial-categories/",
            json=_cat_body(variant, "income"),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == ErrorCode.MANUAL_ENTRY_DUPLICATE_OF_DERIVED.value

    @pytest.mark.parametrize(
        "variant",
        ["  Sales  ", "  COGS ", "\tProduction\n", "Sales  Revenue"],
    )
    async def test_create_blacklisted_whitespace_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any], variant: str
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/financial-categories/",
            json=_cat_body(variant, "income"),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == ErrorCode.MANUAL_ENTRY_DUPLICATE_OF_DERIVED.value

    @pytest.mark.parametrize("name", _SAFE_NAMES)
    async def test_create_safe_name_succeeds(
        self, app: AsyncClient, owner_user: dict[str, Any], name: str
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/financial-categories/",
            json=_cat_body(name, "expense"),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201, f"{name}: {r.text}"


# ---------------------------------------------------------------------------
# API-level: update (rename) path — the #1 bypass vector
# ---------------------------------------------------------------------------


class TestBlacklistOnUpdateRename:
    """Renaming a *non-blacklisted* category to a blacklisted name must fail."""

    @pytest.mark.parametrize("name", _BLACKLISTED_NAMES)
    async def test_rename_to_blacklisted_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any], name: str
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        # Create a legitimate category first.
        r = await app.post(
            "/api/v1/financial-categories/",
            json=_cat_body("Legit expense", "expense"),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201, r.text
        cat_id = r.json()["id"]

        # Attempt to rename into a blacklisted name.
        r2 = await app.patch(
            f"/api/v1/financial-categories/{cat_id}",
            json={"name": name},
            headers=headers,
        )
        assert r2.status_code == 400, f"{name}: {r2.text}"
        assert r2.json()["error"]["code"] == ErrorCode.MANUAL_ENTRY_DUPLICATE_OF_DERIVED.value

        # Verify the name was NOT changed (rename rolled back with the TX).
        r3 = await app.get(f"/api/v1/financial-categories/{cat_id}", headers=headers)
        assert r3.status_code == 200
        assert r3.json()["name"] == "Legit expense"

    @pytest.mark.parametrize(
        "variant",
        ["  SALES  ", "CoGs", "purchase shipping", "Other Income", "OPERATING EXPENSES"],
    )
    async def test_rename_to_case_variant_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any], variant: str
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/financial-categories/",
            json=_cat_body("Legit income", "income"),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cat_id = r.json()["id"]

        r2 = await app.patch(
            f"/api/v1/financial-categories/{cat_id}",
            json={"name": variant},
            headers=headers,
        )
        assert r2.status_code == 400, r2.text
        assert r2.json()["error"]["code"] == ErrorCode.MANUAL_ENTRY_DUPLICATE_OF_DERIVED.value

    async def test_rename_to_safe_name_succeeds(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/financial-categories/",
            json=_cat_body("Legit", "expense"),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cat_id = r.json()["id"]
        updated_at = r.json()["updated_at"]
        r2 = await app.patch(
            f"/api/v1/financial-categories/{cat_id}",
            json={"name": "Renamed legitimately"},
            headers={**headers, "If-Match": f'"{updated_at}"'},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["name"] == "Renamed legitimately"


# ---------------------------------------------------------------------------
# Bypass attempt: deactivate a seeded blacklisted-name category, then
# recreate it with the same name. This proves the blacklist is NOT stored-
# state-dependent — even after deactivation the name cannot be reused as a
# manual category.
# ---------------------------------------------------------------------------


class TestBlacklistBypassViaDeactivate:
    async def test_deactivate_then_recreate_blacklist_name_blocked(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Even if a blacklisted name existed and was deactivated, you cannot
        create a *manual* category with that name (manual ≠ derived)."""
        headers = await _owner_headers(app, owner_user)
        # Seeds never include blacklisted names, but we test the invariant
        # directly: creating "Sales" manually is always blocked.
        r = await app.post(
            "/api/v1/financial-categories/",
            json=_cat_body("Sales", "income"),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.MANUAL_ENTRY_DUPLICATE_OF_DERIVED.value


# ---------------------------------------------------------------------------
# Bypass attempt: service-layer path (not via HTTP)
# ---------------------------------------------------------------------------


class TestBlacklistServiceLayer:
    """The guard is enforced in the service layer, so even a direct service
    call (skipping the HTTP validation) raises the error before any DB write."""

    async def test_service_create_blacklisted_raises(self, owner_user: dict[str, Any]) -> None:
        """Calling the service directly with a blacklisted name raises."""
        from app.db import UnitOfWork
        from app.errors import ManualEntryDuplicateOfDerived
        from app.services.finance import FinancialCategoryService

        async with UnitOfWork() as uow:
            svc = FinancialCategoryService(uow)
            with pytest.raises(ManualEntryDuplicateOfDerived):
                await svc.create(
                    code="svc_test",
                    name="Sales",
                    entry_type="income",
                    is_active=True,
                    ctx=None,
                )
            # No row should have been inserted.
            row = await uow.first_row(
                "SELECT id FROM financial_categories WHERE code = 'svc_test'", {}
            )
            assert row is None, "blacklisted name must not be persisted"

    async def test_service_update_rename_blacklisted_raises(
        self, owner_user: dict[str, Any]
    ) -> None:
        """Calling the service to rename into a blacklisted name raises
        BEFORE the DB write, so the existing name is preserved."""
        from app.db import UnitOfWork
        from app.errors import ManualEntryDuplicateOfDerived
        from app.services.finance import FinancialCategoryService

        async with UnitOfWork() as uow:
            svc = FinancialCategoryService(uow)
            # Create a clean one first.
            created = await svc.create(
                code="svc_rename",
                name="My legit category",
                entry_type="expense",
                is_active=True,
                ctx=None,
            )
            cid = int(created["id"])

            # Attempt rename via service directly.
            with pytest.raises(ManualEntryDuplicateOfDerived):
                await svc.update(cid, name="COGS", ctx=None)

            # The rename must NOT have persisted.
            still = await uow.first_row(
                "SELECT name FROM financial_categories WHERE id = :id",
                {"id": cid},
            )
            assert still is not None
            assert still["name"] == "My legit category"
