"""F.3 — Reports tests (manual-income, manual-expense, refunds, supplier-repayments, cash-flow).

Covers the five F.3 endpoints per ``Backend-Implementation-Plan-V1.0``
§6 (line 684-689):

* ``GET /reports/manual-income``      — paginated list of
  ``manual_finance_entries`` where ``financial_categories.entry_type
  = 'income'``.
* ``GET /reports/manual-expense``     — paginated list where
  ``entry_type = 'expense'``.
* ``GET /reports/refunds``            — paginated list of ``refunds``.
* ``GET /reports/supplier-repayments`` — paginated list of
  ``supplier_repayments`` (the F.3 plan says "repayments" but the
  OpenAPI declares ``supplier-repayments`` — this is the
  authoritative contract).
* ``GET /reports/cash-flow``          — paginated list of
  ``cash_movements``.

All five endpoints accept only ``Period`` / ``From`` / ``To`` /
``CompareTo`` per the F.3 OpenAPI contract (line 5390-5508) — no
``page`` / ``per_page`` / ``sort`` / ``q`` / filters / ``include``
on the wire. Server-side default pagination is ``page=1``,
``per_page=50`` to match the F.1/F.2 convention.

The tests seed the source tables directly via SQL (same pattern as
F.1/F.2). ``financial_categories`` and ``payment_methods`` are
seeded by the conftest ``db`` fixture; tests only seed the data
rows for the report.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.db import get_session_factory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


async def _exec(sql: str, **params: Any) -> None:
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text(sql), params)
        await session.commit()


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


# ---------------------------------------------------------------------------
# Source-table seed helpers
# ---------------------------------------------------------------------------


async def _ensure_product(pid: int = 1) -> None:
    """Seed product id=1 (the FK target for sales/purchases — needed
    for the refunds + supplier_repayments parents)."""
    await _exec(
        """
        INSERT INTO products (
            id, name, selling_price, purchase_price, is_sellable, is_purchasable,
            is_producible, is_active, created_by, updated_by
        ) VALUES (
            :id, :name, 50.00, 25.00, TRUE, TRUE, TRUE, TRUE, 1, 1
        )
        ON CONFLICT (id) DO UPDATE SET
            is_active = TRUE
        """,
        id=pid,
        name=f"F.3 Prod {pid}",
    )


async def _ensure_payment_method(pmid: int = 1) -> None:
    """The conftest db fixture seeds payment_methods 1-4. Touch one to
    guarantee id=1 exists for tests that don't seed via the public API."""
    await _exec(
        """
        INSERT INTO payment_methods (id, code, name, is_cash, is_active)
        VALUES (:id, 'cash', 'Cash', TRUE, TRUE)
        ON CONFLICT (id) DO UPDATE SET is_active = TRUE
        """,
        id=pmid,
    )


async def _insert_sale_header(sid: int, *, sale_date: datetime) -> None:
    """Minimal sale header — ``refunds`` only references ``sales.id``."""
    await _exec(
        """
        INSERT INTO sales (
            id, sale_date, total_amount, lifecycle_status, posted_at, created_by
        ) VALUES (
            :id, :sd, 100.00, 'posted', :sd, 1
        )
        ON CONFLICT (id) DO UPDATE SET sale_date = EXCLUDED.sale_date
        """,
        id=sid,
        sd=sale_date,
    )


async def _insert_purchase_header(pid: int, *, purchase_date: datetime) -> None:
    """Minimal purchase header — ``supplier_repayments`` only references
    ``purchases.id``."""
    await _exec(
        """
        INSERT INTO purchases (
            id, purchase_date, lifecycle_status, posted_at, created_by
        ) VALUES (
            :id, :pd, 'posted', :pd, 1
        )
        ON CONFLICT (id) DO UPDATE SET purchase_date = EXCLUDED.purchase_date
        """,
        id=pid,
        pd=purchase_date,
    )


async def _insert_manual_entry(
    eid: int,
    *,
    entry_date: datetime,
    category_id: int,
    amount: Decimal = Decimal("100.00"),
    payment_method_id: int = 1,
    cancelled: bool = False,
) -> None:
    """Insert a manual_finance_entries row.

    The DB CHECK (``ck_mfe_lifecycle``) permits ``posted|cancelled``;
    we always insert ``posted`` (or ``cancelled``) so the FK + lifecycle
    constraints are satisfied.
    """
    await _exec(
        """
        INSERT INTO manual_finance_entries (
            id, category_id, entry_date, amount, payment_method_id,
            lifecycle_status, created_by
        ) VALUES (
            :id, :cid, :ed, :amt, :pm,
            CASE WHEN :cancelled THEN 'cancelled' ELSE 'posted' END,
            1
        )
        """,
        id=eid,
        cid=category_id,
        ed=entry_date,
        amt=amount,
        pm=payment_method_id,
        cancelled=cancelled,
    )


async def _insert_refund(
    rid: int,
    *,
    sale_id: int,
    refund_date: datetime,
    amount: Decimal = Decimal("50.00"),
    payment_method_id: int = 1,
) -> None:
    await _exec(
        """
        INSERT INTO refunds (
            id, sale_id, amount, payment_method_id, refund_date,
            refundable_amount_snapshot, created_by
        ) VALUES (
            :id, :sid, :amt, :pm, :rd, :amt, 1
        )
        """,
        id=rid,
        sid=sale_id,
        amt=amount,
        pm=payment_method_id,
        rd=refund_date,
    )


async def _insert_supplier_repayment(
    rid: int,
    *,
    purchase_id: int,
    repayment_date: datetime,
    amount: Decimal = Decimal("100.00"),
    received_amount: Decimal = Decimal("100.00"),
    payment_method_id: int = 1,
) -> None:
    await _exec(
        """
        INSERT INTO supplier_repayments (
            id, purchase_id, amount, received_amount, payment_method_id,
            repayment_date, refundable_amount_snapshot, created_by
        ) VALUES (
            :id, :pid, :amt, :rec, :pm, :rd, :amt, 1
        )
        """,
        id=rid,
        pid=purchase_id,
        amt=amount,
        rec=received_amount,
        pm=payment_method_id,
        rd=repayment_date,
    )


async def _insert_cash_movement_in(
    mid: int,
    *,
    movement_date: datetime,
    amount: Decimal = Decimal("100.00"),
    trigger: str = "manual_income",
    payment_method_id: int = 1,
    reference_type: str | None = None,
    reference_id: int | None = None,
) -> None:
    """Insert a direction='in' cash_movement (amount must be > 0 per
    the DB CHECK). Must keep SUM(amount) >= 0 (INV-03) — caller is
    responsible for ordering. To stay safe we always pair ``in`` with
    a sufficient balance or insert via the application's workflow.
    """
    assert amount > 0, "direction='in' requires positive amount"
    await _exec(
        """
        INSERT INTO cash_movements (
            id, movement_date, amount, direction, trigger,
            payment_method_id, reference_type, reference_id, created_by
        ) VALUES (
            :id, :md, :amt, 'in', :tr, :pm, :rt, :rid, 1
        )
        """,
        id=mid,
        md=movement_date,
        amt=amount,
        tr=trigger,
        pm=payment_method_id,
        rt=reference_type,
        rid=reference_id,
    )


# ===========================================================================
# Authorization (all five endpoints)
# ===========================================================================


class TestF3Authorization:
    @pytest.mark.asyncio
    async def test_manual_income_unauthenticated_401(self, app: AsyncClient) -> None:
        r = await app.get("/api/v1/reports/manual-income")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_manual_expense_unauthenticated_401(self, app: AsyncClient) -> None:
        r = await app.get("/api/v1/reports/manual-expense")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_refunds_unauthenticated_401(self, app: AsyncClient) -> None:
        r = await app.get("/api/v1/reports/refunds")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_supplier_repayments_unauthenticated_401(
        self, app: AsyncClient
    ) -> None:
        r = await app.get("/api/v1/reports/supplier-repayments")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_cash_flow_unauthenticated_401(self, app: AsyncClient) -> None:
        r = await app.get("/api/v1/reports/cash-flow")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_manual_income_staff_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        h = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/reports/manual-income", headers=h)
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_manual_expense_staff_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        h = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/reports/manual-expense", headers=h)
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_refunds_staff_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        h = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/reports/refunds", headers=h)
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_supplier_repayments_staff_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        h = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/reports/supplier-repayments", headers=h)
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_cash_flow_staff_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        h = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/reports/cash-flow", headers=h)
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_manual_income_owner_200(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/manual-income",
            headers=h,
            params={"period": "this_month"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["data"] == []
        assert body["pagination"]["page"] == 1
        assert body["pagination"]["per_page"] == 50
        assert body["pagination"]["total"] == 0
        assert body.get("comparison") is None

    @pytest.mark.asyncio
    async def test_manual_expense_owner_200(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/manual-expense",
            headers=h,
            params={"period": "this_month"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["data"] == []
        assert body["pagination"]["page"] == 1
        assert body["pagination"]["per_page"] == 50
        assert body["pagination"]["total"] == 0

    @pytest.mark.asyncio
    async def test_refunds_owner_200(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/refunds", headers=h, params={"period": "this_month"}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["data"] == []
        assert body["pagination"]["total"] == 0

    @pytest.mark.asyncio
    async def test_supplier_repayments_owner_200(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/supplier-repayments",
            headers=h,
            params={"period": "this_month"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["data"] == []
        assert body["pagination"]["total"] == 0

    @pytest.mark.asyncio
    async def test_cash_flow_owner_200(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/cash-flow", headers=h, params={"period": "this_month"}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["data"] == []
        assert body["pagination"]["total"] == 0


# ===========================================================================
# /reports/manual-income
# ===========================================================================


class TestManualIncomeReport:
    @pytest.fixture(autouse=True)
    async def _seed_prereqs(self, app: AsyncClient) -> AsyncGenerator[None, None]:
        await _ensure_product(1)
        await _ensure_payment_method(1)
        yield

    @pytest.mark.asyncio
    async def test_one_posted_entry(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        now = datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC)
        # financial_categories id=1 is 'capital_injection' (income) — seeded
        await _insert_manual_entry(
            1, entry_date=now, category_id=1, amount=Decimal("200.00")
        )
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/manual-income",
            headers=h,
            params={"from": _iso(now), "to": _iso(now)},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pagination"]["total"] == 1
        row = body["data"][0]
        assert row["entry_type"] == "income"
        assert row["category_id"] == 1
        assert row["lifecycle_status"] == "posted"
        assert Decimal(str(row["amount"])) == Decimal("200.00")

    @pytest.mark.asyncio
    async def test_income_excludes_expense_entries(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """The income report filters by entry_type='income' — even
        within the same period, expense-category entries must NOT
        appear in the income report's list."""
        now = datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC)
        await _insert_manual_entry(1, entry_date=now, category_id=1)  # income
        await _insert_manual_entry(
            2, entry_date=now, category_id=4
        )  # expense — rent
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/manual-income",
            headers=h,
            params={"from": _iso(now), "to": _iso(now)},
        )
        assert r.status_code == 200
        assert r.json()["pagination"]["total"] == 1
        assert r.json()["data"][0]["entry_type"] == "income"

    @pytest.mark.asyncio
    async def test_cancelled_entry_listed_but_excluded_from_aggregate(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Per the aggregate decision: cancelled entries appear in
        the list (lifecycle column preserved) but the comparison
        aggregate excludes them (the cancel inserts a reversing
        cash_movement which is captured by the cash-flow report)."""
        h = await _owner_headers(app, owner_user)
        a = datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC)
        b = datetime(2026, 9, 2, 11, 0, 0, tzinfo=UTC)
        compare_from = datetime(2026, 9, 2, 8, 0, 0, tzinfo=UTC)
        compare_to = datetime(2026, 9, 2, 9, 59, 59, 999999, tzinfo=UTC)
        # Primary: 1 posted (100) + 1 cancelled (50) — aggregate = 1/100
        await _insert_manual_entry(
            1, entry_date=a, category_id=1, amount=Decimal("100.00")
        )
        await _insert_manual_entry(
            2, entry_date=b, category_id=1,
            amount=Decimal("50.00"), cancelled=True,
        )
        r = await app.get(
            "/api/v1/reports/manual-income",
            headers=h,
            params={
                "from": _iso(a),
                "to": _iso(b),
                "compare_to": f"{_iso(compare_from)},{_iso(compare_to)}",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pagination"]["total"] == 2
        d = body["comparison"]["delta"]
        # Aggregate sees 1 entry, 100.00 (cancelled excluded). Previous = 0.
        assert d["entry_count"] == 1
        assert d["total_amount"] == 100.0
        # Previous=0, current!=0 → delta_pct is 0.0 (F.1 convention)
        assert body["comparison"]["delta_pct"]["entry_count"] == 0.0
        assert body["comparison"]["delta_pct"]["total_amount"] == 0.0

    @pytest.mark.asyncio
    async def test_inclusive_date_boundaries(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        a = datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC)
        b = datetime(2026, 9, 1, 23, 59, 59, 999999, tzinfo=UTC)
        await _insert_manual_entry(1, entry_date=a, category_id=1)
        await _insert_manual_entry(2, entry_date=b, category_id=1)
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/manual-income",
            headers=h,
            params={"from": _iso(a), "to": _iso(b)},
        )
        assert r.status_code == 200
        assert r.json()["pagination"]["total"] == 2


# ===========================================================================
# /reports/manual-expense
# ===========================================================================


class TestManualExpenseReport:
    @pytest.fixture(autouse=True)
    async def _seed_prereqs(self, app: AsyncClient) -> AsyncGenerator[None, None]:
        await _ensure_product(1)
        await _ensure_payment_method(1)
        yield

    @pytest.mark.asyncio
    async def test_one_posted_entry(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        now = datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC)
        # financial_categories id=4 is 'rent' (expense) — seeded
        await _insert_manual_entry(
            1, entry_date=now, category_id=4, amount=Decimal("150.00")
        )
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/manual-expense",
            headers=h,
            params={"from": _iso(now), "to": _iso(now)},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pagination"]["total"] == 1
        row = body["data"][0]
        assert row["entry_type"] == "expense"
        assert row["category_id"] == 4
        assert Decimal(str(row["amount"])) == Decimal("150.00")

    @pytest.mark.asyncio
    async def test_expense_excludes_income_entries(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        now = datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC)
        await _insert_manual_entry(1, entry_date=now, category_id=1)  # income
        await _insert_manual_entry(2, entry_date=now, category_id=4)  # expense
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/manual-expense",
            headers=h,
            params={"from": _iso(now), "to": _iso(now)},
        )
        assert r.status_code == 200
        assert r.json()["pagination"]["total"] == 1
        assert r.json()["data"][0]["entry_type"] == "expense"


# ===========================================================================
# /reports/refunds
# ===========================================================================


class TestRefundsReport:
    @pytest.fixture(autouse=True)
    async def _seed_prereqs(self, app: AsyncClient) -> AsyncGenerator[None, None]:
        await _ensure_product(1)
        await _ensure_payment_method(1)
        yield

    @pytest.mark.asyncio
    async def test_one_refund(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        now = datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC)
        await _insert_sale_header(1, sale_date=now)
        await _insert_refund(1, sale_id=1, refund_date=now)
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/refunds",
            headers=h,
            params={"from": _iso(now), "to": _iso(now)},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pagination"]["total"] == 1
        row = body["data"][0]
        assert row["sale_id"] == 1
        assert Decimal(str(row["amount"])) == Decimal("50.00")

    @pytest.mark.asyncio
    async def test_compare_to(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        a = datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC)
        b = datetime(2026, 9, 2, 11, 0, 0, tzinfo=UTC)
        compare_from = datetime(2026, 9, 2, 8, 0, 0, tzinfo=UTC)
        compare_to = datetime(2026, 9, 2, 9, 59, 59, 999999, tzinfo=UTC)
        await _insert_sale_header(1, sale_date=a)
        await _insert_sale_header(2, sale_date=b)
        await _insert_refund(1, sale_id=1, refund_date=a, amount=Decimal("100.00"))
        await _insert_refund(2, sale_id=2, refund_date=b, amount=Decimal("50.00"))
        r = await app.get(
            "/api/v1/reports/refunds",
            headers=h,
            params={
                "from": _iso(a),
                "to": _iso(b),
                "compare_to": f"{_iso(compare_from)},{_iso(compare_to)}",
            },
        )
        assert r.status_code == 200, r.text
        d = r.json()["comparison"]["delta"]
        assert d["refund_count"] == 2
        assert d["total_amount"] == 150.0

    @pytest.mark.asyncio
    async def test_out_of_period_excluded(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        inside = datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC)
        outside = datetime(2026, 8, 1, 10, 0, 0, tzinfo=UTC)
        await _insert_sale_header(1, sale_date=inside)
        await _insert_sale_header(2, sale_date=outside)
        await _insert_refund(1, sale_id=1, refund_date=inside)
        await _insert_refund(2, sale_id=2, refund_date=outside)
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/refunds",
            headers=h,
            params={"from": _iso(inside), "to": _iso(inside)},
        )
        assert r.status_code == 200
        assert r.json()["pagination"]["total"] == 1


# ===========================================================================
# /reports/supplier-repayments
# ===========================================================================


class TestSupplierRepaymentsReport:
    @pytest.fixture(autouse=True)
    async def _seed_prereqs(self, app: AsyncClient) -> AsyncGenerator[None, None]:
        await _ensure_product(1)
        await _ensure_payment_method(1)
        yield

    @pytest.mark.asyncio
    async def test_one_repayment(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        now = datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC)
        await _insert_purchase_header(1, purchase_date=now)
        await _insert_supplier_repayment(1, purchase_id=1, repayment_date=now)
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/supplier-repayments",
            headers=h,
            params={"from": _iso(now), "to": _iso(now)},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pagination"]["total"] == 1
        row = body["data"][0]
        assert row["purchase_id"] == 1
        assert Decimal(str(row["received_amount"])) == Decimal("100.00")

    @pytest.mark.asyncio
    async def test_compare_to_uses_received_not_obligated(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """The aggregate must use ``received_amount`` (cash flow),
        not ``amount`` (the obligation ceiling). This test seeds a
        row where ``amount`` and ``received_amount`` differ — the
        aggregate's ``total_received`` should reflect only
        ``received_amount``."""
        h = await _owner_headers(app, owner_user)
        a = datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC)
        b = datetime(2026, 9, 2, 11, 0, 0, tzinfo=UTC)
        compare_from = datetime(2026, 9, 2, 8, 0, 0, tzinfo=UTC)
        compare_to = datetime(2026, 9, 2, 9, 59, 59, 999999, tzinfo=UTC)
        await _insert_purchase_header(1, purchase_date=a)
        await _insert_supplier_repayment(
            1,
            purchase_id=1,
            repayment_date=a,
            amount=Decimal("1000.00"),  # obligation ceiling
            received_amount=Decimal("500.00"),  # actual cash received
        )
        await _insert_purchase_header(2, purchase_date=b)
        await _insert_supplier_repayment(
            2,
            purchase_id=2,
            repayment_date=b,
            amount=Decimal("200.00"),
            received_amount=Decimal("200.00"),
        )
        r = await app.get(
            "/api/v1/reports/supplier-repayments",
            headers=h,
            params={
                "from": _iso(a),
                "to": _iso(b),
                "compare_to": f"{_iso(compare_from)},{_iso(compare_to)}",
            },
        )
        assert r.status_code == 200, r.text
        d = r.json()["comparison"]["delta"]
        # 500 + 200 = 700 (received), NOT 1000 + 200 = 1200 (obligated)
        assert d["repayment_count"] == 2
        assert d["total_received"] == 700.0


# ===========================================================================
# /reports/cash-flow
# ===========================================================================


class TestCashFlowReport:
    @pytest.fixture(autouse=True)
    async def _seed_prereqs(self, app: AsyncClient) -> AsyncGenerator[None, None]:
        await _ensure_product(1)
        await _ensure_payment_method(1)
        yield

    @pytest.mark.asyncio
    async def test_one_in_movement(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        now = datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC)
        await _insert_cash_movement_in(
            1, movement_date=now, amount=Decimal("500.00"),
        )
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/cash-flow",
            headers=h,
            params={"from": _iso(now), "to": _iso(now)},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pagination"]["total"] == 1
        row = body["data"][0]
        assert row["direction"] == "in"
        assert Decimal(str(row["amount"])) == Decimal("500.00")

    @pytest.mark.asyncio
    async def test_compare_to_signed_amounts(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Cash-flow aggregate: ``cash_in`` = SUM(amount WHERE in),
        ``cash_out`` = SUM(amount WHERE out), ``net`` = SUM(amount).
        The DB constraints ensure in is positive and out is negative,
        so ``net = cash_in + cash_out`` (out is already negative)."""
        h = await _owner_headers(app, owner_user)
        a = datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC)
        b = datetime(2026, 9, 2, 11, 0, 0, tzinfo=UTC)
        compare_from = datetime(2026, 9, 2, 8, 0, 0, tzinfo=UTC)
        compare_to = datetime(2026, 9, 2, 9, 59, 59, 999999, tzinfo=UTC)
        # Primary: 1 in (500) + 1 out (-200)
        await _insert_cash_movement_in(
            1, movement_date=a, amount=Decimal("500.00"),
        )
        await _exec(
            """
            INSERT INTO cash_movements (
                id, movement_date, amount, direction, trigger,
                payment_method_id, created_by
            ) VALUES (:id, :md, :amt, 'out', 'manual_expense', 1, 1)
            """,
            id=2,
            md=b,
            amt=Decimal("-200.00"),
        )
        # Previous: 1 in (100)
        prev = datetime(2026, 9, 2, 8, 30, 0, tzinfo=UTC)
        await _insert_cash_movement_in(3, movement_date=prev, amount=Decimal("100.00"))
        r = await app.get(
            "/api/v1/reports/cash-flow",
            headers=h,
            params={
                "from": _iso(a),
                "to": _iso(b),
                "compare_to": f"{_iso(compare_from)},{_iso(compare_to)}",
            },
        )
        assert r.status_code == 200, r.text
        d = r.json()["comparison"]["delta"]
        # Current: 2 movements, in=500, out=-200, net=300
        # Previous: 1 movement, in=100, out=0, net=100
        # delta = current - previous = (1, 400, -200, 200)
        assert d["movement_count"] == 1
        assert d["cash_in"] == 400.0
        assert d["cash_out"] == -200.0
        assert d["net"] == 200.0

    @pytest.mark.asyncio
    async def test_inclusive_date_boundaries(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        a = datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC)
        b = datetime(2026, 9, 1, 23, 59, 59, 999999, tzinfo=UTC)
        await _insert_cash_movement_in(1, movement_date=a)
        await _insert_cash_movement_in(2, movement_date=b)
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/cash-flow",
            headers=h,
            params={"from": _iso(a), "to": _iso(b)},
        )
        assert r.status_code == 200
        assert r.json()["pagination"]["total"] == 2


# ===========================================================================
# Cross-endpoint: response envelope keys
# ===========================================================================


class TestF3ResponseEnvelope:
    @pytest.mark.asyncio
    async def test_manual_income_envelope_shape(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/manual-income",
            headers=h,
            params={"period": "this_month"},
        )
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) >= {"data", "pagination"}
        assert set(body["pagination"].keys()) == {
            "page", "per_page", "total", "total_pages",
        }

    @pytest.mark.asyncio
    async def test_refunds_envelope_shape(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/refunds",
            headers=h,
            params={"period": "this_month"},
        )
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) >= {"data", "pagination"}

    @pytest.mark.asyncio
    async def test_cash_flow_envelope_shape(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/cash-flow",
            headers=h,
            params={"period": "this_month"},
        )
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) >= {"data", "pagination"}
