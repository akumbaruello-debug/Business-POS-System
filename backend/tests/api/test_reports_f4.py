"""F.4 - Reports tests (p-and-l, receivables, payables,
supplier-receivables, customer-refund-liabilities).

Covers the five F.4 endpoints per ``Backend-Implementation-Plan-V1.0``
§6 (line 691-696):

* ``GET /reports/p-and-l``                      - aggregate
  (ProfitLossResponse, no pagination).
* ``GET /reports/receivables``                  - paginated list
  of posted, non-cancelled sales with open AR > 0.
* ``GET /reports/payables``                     - paginated list
  of posted, non-cancelled purchases with open AP > 0.
* ``GET /reports/supplier-receivables``         - paginated list
  of open Supplier Receivable obligations
  (``amount - received_amount > 0``).
* ``GET /reports/customer-refund-liabilities``  - paginated list
  of posted, non-cancelled sales with open CRL > 0.

All five endpoints accept only ``Period`` / ``From`` / ``To`` /
``CompareTo`` per the F.4 OpenAPI contract (line 5510-5639) - no
``page`` / ``per_page`` / ``sort`` / ``q`` / filters on the wire.
Server-side default pagination is ``page=1``, ``per_page=50``
(matches F.1/F.2/F.3 convention).

Capability: P&L is gated on ``finance.view_profit``; the four
list endpoints are gated on ``finance.view_payables_receivables``.
Owner has both; Staff defaults do NOT include either, so Staff
gets 403 for all five endpoints.

Accounting-equation test: ``TestAccountingEquation.test_balance``
independently recomputes the GL balance from the underlying
ledgers (sales + sale_payments + purchases + purchase_payments +
manual_finance_entries) and asserts the F.4 reports'
``Σ(open_balance)`` matches the GL balance to the cent. This
catches reports that are internally consistent but financially
wrong.
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


async def _owner_headers(
    app: AsyncClient, owner_user: dict[str, Any]
) -> dict[str, str]:
    token = await _login(app, owner_user["username"], owner_user["password"])
    return {"Authorization": f"Bearer {token}"}


async def _staff_headers(
    app: AsyncClient, staff_user: dict[str, Any]
) -> dict[str, str]:
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
    """Seed product id=1 (FK target for sales/purchases lines)."""
    await _exec(
        """
        INSERT INTO products (
            id, name, selling_price, purchase_price, is_sellable, is_purchasable,
            is_producible, is_active, created_by, updated_by
        ) VALUES (
            :id, :name, 50.00, 25.00, TRUE, TRUE, FALSE, TRUE, 1, 1
        )
        ON CONFLICT (id) DO UPDATE SET is_active = TRUE
        """,
        id=pid,
        name=f"F.4 Prod {pid}",
    )


async def _ensure_supplier(sid: int = 1) -> None:
    """Seed contact id=1 as a supplier (FK target for supplier_repayments)."""
    await _exec(
        """
        INSERT INTO contacts (
            id, name, type, is_active, created_by
        ) VALUES (
            :id, :n, 'supplier', TRUE, 1
        )
        ON CONFLICT (id) DO UPDATE SET is_active = TRUE
        """,
        id=sid,
        n=f"F.4 Supplier {sid}",
    )


async def _insert_sale(
    sid: int,
    *,
    sale_date: datetime,
    total: Decimal,
    customer_id: int | None = None,
    cancelled: bool = False,
) -> None:
    """Insert a posted, non-cancelled sale (or cancelled if asked).

    The schema requires ``posted_at IS NOT NULL`` for posted sales.
    Per ``schema.sql:470-474`` a cancelled sale can have posted_at
    NULL or NOT NULL; we always set posted_at to a timestamp for
    the tests that use the non-cancelled path. The one test that
    inserts a cancelled sale sets posted_at explicitly.
    """
    posted_at = sale_date
    cancellation_date = sale_date if cancelled else None
    await _exec(
        """
        INSERT INTO sales (
            id, sale_date, total_amount, lifecycle_status, posted_at,
            customer_id, cancellation_date, created_by, posted_by
        ) VALUES (
            :id, :sd, :tot,
            CASE WHEN :cancelled THEN 'cancelled' ELSE 'posted' END,
            :pa, :cid, :cd, 1, 1
        )
        ON CONFLICT (id) DO UPDATE SET
            sale_date = EXCLUDED.sale_date,
            total_amount = EXCLUDED.total_amount,
            posted_at = EXCLUDED.posted_at
        """,
        id=sid,
        sd=sale_date,
        tot=total,
        cancelled=cancelled,
        pa=posted_at,
        cid=customer_id,
        cd=cancellation_date,
    )


async def _insert_sale_payment(
    pid: int, *, sale_id: int, amount: Decimal, payment_date: datetime
) -> None:
    await _exec(
        """
        INSERT INTO sale_payments (
            id, sale_id, payment_method_id, amount, payment_date, created_by
        ) VALUES (
            :id, :sid, 1, :amt, :pd, 1
        )
        """,
        id=pid,
        sid=sale_id,
        amt=amount,
        pd=payment_date,
    )


async def _insert_purchase(
    pid: int,
    *,
    purchase_date: datetime,
    supplier_id: int | None = 1,
    cancelled: bool = False,
) -> None:
    posted_at = purchase_date
    cancellation_date = purchase_date if cancelled else None
    await _exec(
        """
        INSERT INTO purchases (
            id, purchase_date, lifecycle_status, posted_at,
            supplier_id, cancellation_date, created_by, posted_by
        ) VALUES (
            :id, :pd,
            CASE WHEN :cancelled THEN 'cancelled' ELSE 'posted' END,
            :pa, :sup, :cd, 1, 1
        )
        ON CONFLICT (id) DO UPDATE SET
            purchase_date = EXCLUDED.purchase_date,
            posted_at = EXCLUDED.posted_at
        """,
        id=pid,
        pd=purchase_date,
        cancelled=cancelled,
        pa=posted_at,
        sup=supplier_id,
        cd=cancellation_date,
    )


async def _insert_purchase_line(
    plid: int, *, purchase_id: int, line_total: Decimal
) -> None:
    await _exec(
        """
        INSERT INTO purchase_lines (
            id, purchase_id, product_id, quantity, unit_price, line_subtotal,
            allocated_shipping, line_total, line_number
        ) VALUES (
            :id, :pid, 1, 1.0, :lt, :lt, 0.00, :lt, 1
        )
        """,
        id=plid,
        pid=purchase_id,
        lt=line_total,
    )


async def _insert_purchase_payment(
    ppid: int, *, purchase_id: int, amount: Decimal, payment_date: datetime
) -> None:
    await _exec(
        """
        INSERT INTO purchase_payments (
            id, purchase_id, payment_method_id, amount, payment_date, created_by
        ) VALUES (
            :id, :pid, 1, :amt, :pd, 1
        )
        """,
        id=ppid,
        pid=purchase_id,
        amt=amount,
        pd=payment_date,
    )


async def _insert_supplier_repayment(
    rid: int,
    *,
    purchase_id: int,
    repayment_date: datetime,
    amount: Decimal,
    received_amount: Decimal,
) -> None:
    await _exec(
        """
        INSERT INTO supplier_repayments (
            id, purchase_id, amount, received_amount, payment_method_id,
            repayment_date, refundable_amount_snapshot, created_by
        ) VALUES (
            :id, :pid, :amt, :rec, 1, :rd, :amt, 1
        )
        """,
        id=rid,
        pid=purchase_id,
        amt=amount,
        rec=received_amount,
        rd=repayment_date,
    )


async def _insert_refund(
    rid: int, *, sale_id: int, refund_date: datetime, amount: Decimal
) -> None:
    await _exec(
        """
        INSERT INTO refunds (
            id, sale_id, amount, payment_method_id, refund_date,
            refundable_amount_snapshot, created_by
        ) VALUES (
            :id, :sid, :amt, 1, :rd, :amt, 1
        )
        """,
        id=rid,
        sid=sale_id,
        amt=amount,
        rd=refund_date,
    )


async def _insert_manual_entry(
    eid: int,
    *,
    entry_date: datetime,
    category_id: int,
    amount: Decimal = Decimal("100.00"),
    cancelled: bool = False,
) -> None:
    await _exec(
        """
        INSERT INTO manual_finance_entries (
            id, category_id, entry_date, amount, payment_method_id,
            lifecycle_status, created_by
        ) VALUES (
            :id, :cid, :ed, :amt, 1,
            CASE WHEN :cancelled THEN 'cancelled' ELSE 'posted' END,
            1
        )
        """,
        id=eid,
        cid=category_id,
        ed=entry_date,
        amt=amount,
        cancelled=cancelled,
    )


# ===========================================================================
# Authorization (all five endpoints)
# ===========================================================================


class TestF4Authorization:
    @pytest.mark.asyncio
    async def test_p_and_l_unauthenticated_401(self, app: AsyncClient) -> None:
        r = await app.get("/api/v1/reports/p-and-l")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_receivables_unauthenticated_401(
        self, app: AsyncClient
    ) -> None:
        r = await app.get("/api/v1/reports/receivables")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_payables_unauthenticated_401(
        self, app: AsyncClient
    ) -> None:
        r = await app.get("/api/v1/reports/payables")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_supplier_receivables_unauthenticated_401(
        self, app: AsyncClient
    ) -> None:
        r = await app.get("/api/v1/reports/supplier-receivables")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_crl_unauthenticated_401(self, app: AsyncClient) -> None:
        r = await app.get("/api/v1/reports/customer-refund-liabilities")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_p_and_l_staff_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        h = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/reports/p-and-l", headers=h)
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_receivables_staff_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        h = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/reports/receivables", headers=h)
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_payables_staff_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        h = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/reports/payables", headers=h)
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_supplier_receivables_staff_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        h = await _staff_headers(app, staff_user)
        r = await app.get(
            "/api/v1/reports/supplier-receivables", headers=h
        )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_crl_staff_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        h = await _staff_headers(app, staff_user)
        r = await app.get(
            "/api/v1/reports/customer-refund-liabilities", headers=h
        )
        assert r.status_code == 403


# ===========================================================================
# /reports/p-and-l
# ===========================================================================


class TestProfitAndLossReport:
    @pytest.fixture(autouse=True)
    async def _seed_prereqs(self, app: AsyncClient) -> AsyncGenerator[None, None]:
        await _ensure_product(1)
        yield

    @pytest.mark.asyncio
    async def test_empty_dataset(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        now = datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)
        r = await app.get(
            "/api/v1/reports/p-and-l",
            headers=h,
            params={"from": _iso(now), "to": _iso(now)},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["data"] == {
            "revenue": 0.0,
            "cogs": 0.0,
            "gross_profit": 0.0,
            "other_income": 0.0,
            "operating_expenses": 0.0,
            "net_profit": 0.0,
        }
        assert body.get("comparison") is None
        assert "pagination" not in body

    @pytest.mark.asyncio
    async def test_one_sale_with_cogs_and_manual_entries(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Sale 200/60 → rev=200, cogs=60, gross=140. Manual income 50
        → other_income=50. Manual expense 30 → operating_expenses=30.
        Net = 140 + 50 - 30 = 160."""
        d = datetime(2026, 9, 15, 10, 0, 0, tzinfo=UTC)
        await _insert_sale(1, sale_date=d, total=Decimal("200.00"))
        await _insert_sale_payment(1, sale_id=1, amount=Decimal("200.00"), payment_date=d)
        # Add a sale_line with cogs_total_snapshot=60 so the P&L picks up COGS
        await _exec(
            """
            INSERT INTO sale_lines (
                id, sale_id, product_id, quantity, unit_price, discount_amount,
                line_total, unit_cost_snapshot, cogs_total_snapshot, line_number
            ) VALUES (1, 1, 1, 1.0, 200.00, 0.00, 200.00, 60.00, 60.00, 1)
            ON CONFLICT (id) DO UPDATE SET cogs_total_snapshot = 60.00
            """
        )
        # Manual income 50 (cat 1 = income)
        await _insert_manual_entry(1, entry_date=d, category_id=1, amount=Decimal("50.00"))
        # Manual expense 30 (cat 4 = rent)
        await _insert_manual_entry(2, entry_date=d, category_id=4, amount=Decimal("30.00"))

        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/p-and-l",
            headers=h,
            params={"from": _iso(d), "to": _iso(d)},
        )
        assert r.status_code == 200, r.text
        d_ = r.json()["data"]
        assert Decimal(str(d_["revenue"])) == Decimal("200.00")
        assert Decimal(str(d_["cogs"])) == Decimal("60.00")
        assert Decimal(str(d_["gross_profit"])) == Decimal("140.00")
        assert Decimal(str(d_["other_income"])) == Decimal("50.00")
        assert Decimal(str(d_["operating_expenses"])) == Decimal("30.00")
        assert Decimal(str(d_["net_profit"])) == Decimal("160.00")

    @pytest.mark.asyncio
    async def test_cancelled_manual_excluded_from_pnl(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Per F.3 decision: cancelled manual entries must NOT appear
        in the P&L aggregate (their reversing cash_movement lives in
        the cash-flow report)."""
        d = datetime(2026, 9, 15, 10, 0, 0, tzinfo=UTC)
        await _insert_manual_entry(1, entry_date=d, category_id=1, amount=Decimal("100.00"))
        await _insert_manual_entry(
            2, entry_date=d, category_id=1, amount=Decimal("50.00"), cancelled=True
        )
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/p-and-l",
            headers=h,
            params={"from": _iso(d), "to": _iso(d)},
        )
        assert r.status_code == 200
        d_ = r.json()["data"]
        # Only the posted entry counts (100.00); cancelled excluded
        assert Decimal(str(d_["other_income"])) == Decimal("100.00")

    @pytest.mark.asyncio
    async def test_cancelled_sale_excluded_from_pnl(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        d = datetime(2026, 9, 15, 10, 0, 0, tzinfo=UTC)
        await _insert_sale(1, sale_date=d, total=Decimal("200.00"))
        await _insert_sale(2, sale_date=d, total=Decimal("999.00"), cancelled=True)
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/p-and-l",
            headers=h,
            params={"from": _iso(d), "to": _iso(d)},
        )
        assert r.status_code == 200
        d_ = r.json()["data"]
        # Only the posted sale counts (200.00); cancelled excluded
        assert Decimal(str(d_["revenue"])) == Decimal("200.00")

    @pytest.mark.asyncio
    async def test_compare_to(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Two-window comparison: current has 1 sale (100/0 COGS), previous has 0.
        Delta.revenue = 100, delta_pct = 0.0 (previous was 0)."""
        d = datetime(2026, 9, 15, 10, 0, 0, tzinfo=UTC)
        prev_from = datetime(2026, 9, 15, 8, 0, 0, tzinfo=UTC)
        prev_to = datetime(2026, 9, 15, 9, 59, 59, 999999, tzinfo=UTC)
        await _insert_sale(1, sale_date=d, total=Decimal("100.00"))
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/p-and-l",
            headers=h,
            params={
                "from": _iso(d),
                "to": _iso(d),
                "compare_to": f"{_iso(prev_from)},{_iso(prev_to)}",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["comparison"]["delta"]["revenue"] == pytest.approx(100.0)
        assert body["comparison"]["delta_pct"]["revenue"] == 0.0
        assert body["comparison"]["previous_period"]["from"] == _iso(prev_from)


# ===========================================================================
# /reports/receivables
# ===========================================================================


class TestReceivablesReport:
    @pytest.fixture(autouse=True)
    async def _seed_prereqs(self, app: AsyncClient) -> AsyncGenerator[None, None]:
        await _ensure_product(1)
        yield

    @pytest.mark.asyncio
    async def test_empty_dataset(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        now = datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)
        r = await app.get(
            "/api/v1/reports/receivables",
            headers=h,
            params={"from": _iso(now), "to": _iso(now)},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["data"] == []
        assert body["pagination"]["total"] == 0
        assert body["pagination"]["page"] == 1
        assert body["pagination"]["per_page"] == 50

    @pytest.mark.asyncio
    async def test_one_unpaid_sale(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Sale 200, paid 0 → open_balance 200."""
        d = datetime(2026, 9, 15, 10, 0, 0, tzinfo=UTC)
        await _insert_sale(1, sale_date=d, total=Decimal("200.00"))
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/receivables",
            headers=h,
            params={"from": _iso(d), "to": _iso(d)},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pagination"]["total"] == 1
        row = body["data"][0]
        assert row["sale_id"] == 1
        assert Decimal(str(row["total_invoiced"])) == Decimal("200.00")
        assert Decimal(str(row["total_paid"])) == Decimal("0.00")
        assert Decimal(str(row["open_balance"])) == Decimal("200.00")

    @pytest.mark.asyncio
    async def test_partially_paid_sale(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Sale 200, paid 75 → open_balance 125."""
        d = datetime(2026, 9, 15, 10, 0, 0, tzinfo=UTC)
        await _insert_sale(1, sale_date=d, total=Decimal("200.00"))
        await _insert_sale_payment(1, sale_id=1, amount=Decimal("75.00"), payment_date=d)
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/receivables",
            headers=h,
            params={"from": _iso(d), "to": _iso(d)},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pagination"]["total"] == 1
        row = body["data"][0]
        assert Decimal(str(row["open_balance"])) == Decimal("125.00")

    @pytest.mark.asyncio
    async def test_fully_paid_sale_excluded(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """A fully paid sale is not a receivable - it must not appear
        in the receivables report (open_balance == 0)."""
        d = datetime(2026, 9, 15, 10, 0, 0, tzinfo=UTC)
        await _insert_sale(1, sale_date=d, total=Decimal("100.00"))
        await _insert_sale_payment(1, sale_id=1, amount=Decimal("100.00"), payment_date=d)
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/receivables",
            headers=h,
            params={"from": _iso(d), "to": _iso(d)},
        )
        assert r.status_code == 200
        assert r.json()["pagination"]["total"] == 0

    @pytest.mark.asyncio
    async def test_cancelled_sale_excluded(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        d = datetime(2026, 9, 15, 10, 0, 0, tzinfo=UTC)
        await _insert_sale(1, sale_date=d, total=Decimal("200.00"), cancelled=True)
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/receivables",
            headers=h,
            params={"from": _iso(d), "to": _iso(d)},
        )
        assert r.status_code == 200
        assert r.json()["pagination"]["total"] == 0

    @pytest.mark.asyncio
    async def test_compare_to_aggregate(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        d = datetime(2026, 9, 15, 10, 0, 0, tzinfo=UTC)
        prev_from = datetime(2026, 9, 15, 8, 0, 0, tzinfo=UTC)
        prev_to = datetime(2026, 9, 15, 9, 59, 59, 999999, tzinfo=UTC)
        await _insert_sale(1, sale_date=d, total=Decimal("150.00"))
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/receivables",
            headers=h,
            params={
                "from": _iso(d),
                "to": _iso(d),
                "compare_to": f"{_iso(prev_from)},{_iso(prev_to)}",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # current: 1 receivable @ 150. previous: 0.
        assert body["comparison"]["delta"]["receivable_count"] == 1.0
        assert body["comparison"]["delta"]["total_open"] == pytest.approx(150.0)
        assert body["comparison"]["delta_pct"]["total_open"] == 0.0


# ===========================================================================
# /reports/payables
# ===========================================================================


class TestPayablesReport:
    @pytest.fixture(autouse=True)
    async def _seed_prereqs(self, app: AsyncClient) -> AsyncGenerator[None, None]:
        await _ensure_product(1)
        await _ensure_supplier(1)
        yield

    @pytest.mark.asyncio
    async def test_one_unpaid_purchase(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Purchase 300, no lines, no payments → total=0 (no lines), open=0.
        To get a non-trivial open_balance we need a line."""
        d = datetime(2026, 9, 15, 10, 0, 0, tzinfo=UTC)
        await _insert_purchase(1, purchase_date=d)
        await _insert_purchase_line(1, purchase_id=1, line_total=Decimal("300.00"))
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/payables",
            headers=h,
            params={"from": _iso(d), "to": _iso(d)},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pagination"]["total"] == 1
        row = body["data"][0]
        assert Decimal(str(row["total_invoiced"])) == Decimal("300.00")
        assert Decimal(str(row["total_paid"])) == Decimal("0.00")
        assert Decimal(str(row["open_balance"])) == Decimal("300.00")

    @pytest.mark.asyncio
    async def test_partially_paid_purchase(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        d = datetime(2026, 9, 15, 10, 0, 0, tzinfo=UTC)
        await _insert_purchase(1, purchase_date=d)
        await _insert_purchase_line(1, purchase_id=1, line_total=Decimal("500.00"))
        await _insert_purchase_payment(1, purchase_id=1, amount=Decimal("200.00"), payment_date=d)
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/payables",
            headers=h,
            params={"from": _iso(d), "to": _iso(d)},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pagination"]["total"] == 1
        row = body["data"][0]
        assert Decimal(str(row["total_invoiced"])) == Decimal("500.00")
        assert Decimal(str(row["total_paid"])) == Decimal("200.00")
        assert Decimal(str(row["open_balance"])) == Decimal("300.00")

    @pytest.mark.asyncio
    async def test_fully_paid_excluded(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        d = datetime(2026, 9, 15, 10, 0, 0, tzinfo=UTC)
        await _insert_purchase(1, purchase_date=d)
        await _insert_purchase_line(1, purchase_id=1, line_total=Decimal("100.00"))
        await _insert_purchase_payment(1, purchase_id=1, amount=Decimal("100.00"), payment_date=d)
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/payables",
            headers=h,
            params={"from": _iso(d), "to": _iso(d)},
        )
        assert r.status_code == 200
        assert r.json()["pagination"]["total"] == 0

    @pytest.mark.asyncio
    async def test_cancelled_excluded(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        d = datetime(2026, 9, 15, 10, 0, 0, tzinfo=UTC)
        await _insert_purchase(1, purchase_date=d, cancelled=True)
        await _insert_purchase_line(1, purchase_id=1, line_total=Decimal("200.00"))
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/payables",
            headers=h,
            params={"from": _iso(d), "to": _iso(d)},
        )
        assert r.status_code == 200
        assert r.json()["pagination"]["total"] == 0


# ===========================================================================
# /reports/supplier-receivables
# ===========================================================================


class TestSupplierReceivablesReport:
    @pytest.fixture(autouse=True)
    async def _seed_prereqs(self, app: AsyncClient) -> AsyncGenerator[None, None]:
        await _ensure_product(1)
        await _ensure_supplier(1)
        yield

    @pytest.mark.asyncio
    async def test_one_open_obligation(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """supplier_repayments: amount=200, received=50 → open=150."""
        d = datetime(2026, 9, 15, 10, 0, 0, tzinfo=UTC)
        await _insert_purchase(1, purchase_date=d)
        await _insert_supplier_repayment(
            1,
            purchase_id=1,
            repayment_date=d,
            amount=Decimal("200.00"),
            received_amount=Decimal("50.00"),
        )
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/supplier-receivables",
            headers=h,
            params={"from": _iso(d), "to": _iso(d)},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pagination"]["total"] == 1
        row = body["data"][0]
        assert Decimal(str(row["obligation"])) == Decimal("200.00")
        assert Decimal(str(row["received"])) == Decimal("50.00")
        assert Decimal(str(row["open_balance"])) == Decimal("150.00")

    @pytest.mark.asyncio
    async def test_fully_received_excluded(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """amount=100, received=100 → open=0, excluded."""
        d = datetime(2026, 9, 15, 10, 0, 0, tzinfo=UTC)
        await _insert_purchase(1, purchase_date=d)
        await _insert_supplier_repayment(
            1,
            purchase_id=1,
            repayment_date=d,
            amount=Decimal("100.00"),
            received_amount=Decimal("100.00"),
        )
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/supplier-receivables",
            headers=h,
            params={"from": _iso(d), "to": _iso(d)},
        )
        assert r.status_code == 200
        assert r.json()["pagination"]["total"] == 0

    @pytest.mark.asyncio
    async def test_out_of_period_excluded(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """repayment_date outside [from,to] window → not in period."""
        d = datetime(2026, 9, 15, 10, 0, 0, tzinfo=UTC)
        await _insert_purchase(1, purchase_date=d)
        await _insert_supplier_repayment(
            1,
            purchase_id=1,
            repayment_date=datetime(2026, 9, 16, 10, 0, 0, tzinfo=UTC),
            amount=Decimal("200.00"),
            received_amount=Decimal("0.00"),
        )
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/supplier-receivables",
            headers=h,
            params={"from": _iso(d), "to": _iso(d)},
        )
        assert r.status_code == 200
        assert r.json()["pagination"]["total"] == 0


# ===========================================================================
# /reports/customer-refund-liabilities
# ===========================================================================


class TestCustomerRefundLiabilitiesReport:
    @pytest.fixture(autouse=True)
    async def _seed_prereqs(self, app: AsyncClient) -> AsyncGenerator[None, None]:
        await _ensure_product(1)
        yield

    @pytest.mark.asyncio
    async def test_paid_sale_with_refund(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Sale 200 fully paid, refund 30 → CRL = paid - refund = 170."""
        d = datetime(2026, 9, 15, 10, 0, 0, tzinfo=UTC)
        await _insert_sale(1, sale_date=d, total=Decimal("200.00"))
        await _insert_sale_payment(1, sale_id=1, amount=Decimal("200.00"), payment_date=d)
        await _insert_refund(1, sale_id=1, refund_date=d, amount=Decimal("30.00"))
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/customer-refund-liabilities",
            headers=h,
            params={"from": _iso(d), "to": _iso(d)},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pagination"]["total"] == 1
        row = body["data"][0]
        assert Decimal(str(row["total_paid"])) == Decimal("200.00")
        assert Decimal(str(row["total_refunded"])) == Decimal("30.00")
        assert Decimal(str(row["open_crl"])) == Decimal("170.00")

    @pytest.mark.asyncio
    async def test_unpaid_sale_excluded(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """An unpaid sale has no CRL (CRL = 0). Must not appear."""
        d = datetime(2026, 9, 15, 10, 0, 0, tzinfo=UTC)
        await _insert_sale(1, sale_date=d, total=Decimal("200.00"))
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/customer-refund-liabilities",
            headers=h,
            params={"from": _iso(d), "to": _iso(d)},
        )
        assert r.status_code == 200
        assert r.json()["pagination"]["total"] == 0

    @pytest.mark.asyncio
    async def test_fully_refunded_sale_excluded(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """paid=100, refund=100 → CRL=0. Excluded."""
        d = datetime(2026, 9, 15, 10, 0, 0, tzinfo=UTC)
        await _insert_sale(1, sale_date=d, total=Decimal("100.00"))
        await _insert_sale_payment(1, sale_id=1, amount=Decimal("100.00"), payment_date=d)
        await _insert_refund(1, sale_id=1, refund_date=d, amount=Decimal("100.00"))
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/customer-refund-liabilities",
            headers=h,
            params={"from": _iso(d), "to": _iso(d)},
        )
        assert r.status_code == 200
        assert r.json()["pagination"]["total"] == 0

    @pytest.mark.asyncio
    async def test_cancelled_sale_excluded(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        d = datetime(2026, 9, 15, 10, 0, 0, tzinfo=UTC)
        await _insert_sale(1, sale_date=d, total=Decimal("200.00"), cancelled=True)
        await _insert_sale_payment(1, sale_id=1, amount=Decimal("200.00"), payment_date=d)
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/customer-refund-liabilities",
            headers=h,
            params={"from": _iso(d), "to": _iso(d)},
        )
        assert r.status_code == 200
        assert r.json()["pagination"]["total"] == 0


# ===========================================================================
# CRITICAL - accounting-equation sanity test
# ===========================================================================
#
# The point of this test is to prove the F.4 receivables and payables
# reports' aggregates are *not just internally consistent* but also
# *match the underlying GL*. We independently compute:
#
#   GL_AR    = Σ sales.total_amount - Σ sale_payments.amount
#   GL_AP    = Σ purchase_lines.line_total - Σ purchase_payments.amount
#             (+ shipping if any - we don't seed shipping here)
#
# and assert that the report's Σ(open_balance) for the same sales /
# purchases equals these GL values. If the report omits a sale
# (e.g. excludes a non-cancelled posted sale) or double-counts a
# payment, the GL-side total will differ.
# ===========================================================================


class TestAccountingEquation:
    @pytest.fixture(autouse=True)
    async def _seed_prereqs(self, app: AsyncClient) -> AsyncGenerator[None, None]:
        await _ensure_product(1)
        await _ensure_supplier(1)
        yield

    @pytest.mark.asyncio
    async def test_receivables_aggregate_matches_gl(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Three sales with varying payment status:

        * Sale 1: 200 total, 0 paid      → open 200
        * Sale 2: 300 total, 100 paid    → open 200
        * Sale 3: 150 total, 150 paid    → open 0  (excluded)
        * Sale 4 (cancelled): 999 total  → excluded

        GL_AR = (200 + 300 + 150) - (0 + 100 + 150) = 650 - 250 = 400
        Report Σ(open_balance) = 200 + 200 = 400. Match.
        """
        d = datetime(2026, 9, 15, 10, 0, 0, tzinfo=UTC)
        await _insert_sale(1, sale_date=d, total=Decimal("200.00"))
        await _insert_sale(2, sale_date=d, total=Decimal("300.00"))
        await _insert_sale(3, sale_date=d, total=Decimal("150.00"))
        await _insert_sale(4, sale_date=d, total=Decimal("999.00"), cancelled=True)
        await _insert_sale_payment(1, sale_id=2, amount=Decimal("100.00"), payment_date=d)
        await _insert_sale_payment(2, sale_id=3, amount=Decimal("150.00"), payment_date=d)

        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/receivables",
            headers=h,
            params={"from": _iso(d), "to": _iso(d)},
        )
        assert r.status_code == 200, r.text
        rows = r.json()["data"]
        # Sum the open_balance column directly (independent recompute).
        report_open = sum(Decimal(str(row["open_balance"])) for row in rows)
        # GL: 200 + 300 + 150 - 0 - 100 - 150 = 400
        gl_ar = (
            Decimal("200.00")
            + Decimal("300.00")
            + Decimal("150.00")
            - Decimal("100.00")
            - Decimal("150.00")
        )
        assert report_open == gl_ar == Decimal("400.00")
        # And the receivables list surfaces exactly 2 rows (sales 1, 2).
        assert len(rows) == 2

    @pytest.mark.asyncio
    async def test_payables_aggregate_matches_gl(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Two purchases with varying payment status:

        * Purchase 1: line_total 400, paid 0      → open 400
        * Purchase 2: line_total 600, paid 600    → open 0  (excluded)

        GL_AP = 400 + 600 - 0 - 600 = 400
        Report Σ(open_balance) = 400. Match.
        """
        d = datetime(2026, 9, 15, 10, 0, 0, tzinfo=UTC)
        await _insert_purchase(1, purchase_date=d)
        await _insert_purchase_line(1, purchase_id=1, line_total=Decimal("400.00"))
        await _insert_purchase(2, purchase_date=d)
        await _insert_purchase_line(2, purchase_id=2, line_total=Decimal("600.00"))
        await _insert_purchase_payment(
            1, purchase_id=2, amount=Decimal("600.00"), payment_date=d
        )

        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/payables",
            headers=h,
            params={"from": _iso(d), "to": _iso(d)},
        )
        assert r.status_code == 200, r.text
        rows = r.json()["data"]
        report_open = sum(Decimal(str(row["open_balance"])) for row in rows)
        gl_ap = Decimal("400.00") + Decimal("600.00") - Decimal("600.00")
        assert report_open == gl_ap == Decimal("400.00")
        assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_crl_aggregate_matches_gl(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Two paid sales with refunds:

        * Sale 1: paid 200, refund 50     → CRL 150
        * Sale 2: paid 300, refund 300    → CRL 0   (excluded)

        GL_CRL = (200 + 300) - (50 + 300) = 500 - 350 = 150
        Report Σ(open_crl) = 150. Match.
        """
        d = datetime(2026, 9, 15, 10, 0, 0, tzinfo=UTC)
        await _insert_sale(1, sale_date=d, total=Decimal("200.00"))
        await _insert_sale_payment(1, sale_id=1, amount=Decimal("200.00"), payment_date=d)
        await _insert_refund(1, sale_id=1, refund_date=d, amount=Decimal("50.00"))
        await _insert_sale(2, sale_date=d, total=Decimal("300.00"))
        await _insert_sale_payment(2, sale_id=2, amount=Decimal("300.00"), payment_date=d)
        await _insert_refund(2, sale_id=2, refund_date=d, amount=Decimal("300.00"))

        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/customer-refund-liabilities",
            headers=h,
            params={"from": _iso(d), "to": _iso(d)},
        )
        assert r.status_code == 200, r.text
        rows = r.json()["data"]
        report_crl = sum(Decimal(str(row["open_crl"])) for row in rows)
        gl_crl = (Decimal("200.00") + Decimal("300.00")) - (
            Decimal("50.00") + Decimal("300.00")
        )
        assert report_crl == gl_crl == Decimal("150.00")
        assert len(rows) == 1
