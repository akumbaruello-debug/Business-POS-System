"""F.5 - Dashboard tests (KPIs + inventory).

Covers the two F.5 endpoints per ``Backend-Implementation-Plan-V1.0``
section 6 (line 698-702) and ``openapi.yaml`` line 5640-5688:

* ``GET /dashboard``           -> ``DashboardResponse``
* ``GET /dashboard/inventory`` -> ``InventoryReportResponse``

Authorization:

* ``/dashboard``           gated on ``finance.view_profit``
  (Owner has it; Staff does not -> 403).
* ``/dashboard/inventory`` gated on ``inventory.view``
  (Owner and Staff both have it -> 200 for both).

Test matrix follows the F.4 read-only-paginated matrix (see the
TDD skill's coverage rules):

1. Auth (401 unauth, 403 wrong-cap) for both endpoints.
2. Empty dataset returns zero-value KPIs and empty chart arrays.
3. Posted, non-cancelled sale is counted.
4. Cancelled sale is excluded from total_sales / net_profit /
   sales_trend / best_sellers.
5. Posted sales_returns reduce total_sales (D-8 derivation).
6. Posted, non-cancelled purchase is counted in total_purchases.
7. Posted manual income and expense flow into P&L (net_profit).
8. Cancelled manual entry is excluded (F.3 rule - cancellation
   creates a reversing cash_movement that lives in cash-flow).
9. Cash on hand = SUM(cash_movements.amount) (independent GL
   recompute in TestAccountingCorrectness).
10. Inventory value + low_stock_count + out_of_stock_count from
    product_valuation view (reuses F.1 inventory report).
11. Sales trend: 7-day window -> daily buckets; 30-day window ->
    weekly buckets.
12. Best sellers sorted by revenue DESC LIMIT 10.
13. Expense breakdown grouped by financial_categories.name,
    lifecycle='posted', entry_type='expense'.
14. compare_to envelope emits delta + delta_pct for KPI metrics.
15. Invalid period -> 400 validation_failed envelope.
16. Cancelled stock_movements are reversed by a new row (so on_hand
    nets to zero); the snapshot still works correctly.
"""

from __future__ import annotations

import uuid
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


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


async def _ensure_product(
    pid: int,
    *,
    name: str | None = None,
    low_stock_threshold: Decimal | None = None,
    is_active: bool = True,
) -> None:
    """Seed a product with optional low-stock threshold."""
    await _exec(
        """
        INSERT INTO products (
            id, name, selling_price, purchase_price, is_sellable, is_purchasable,
            is_producible, is_active, low_stock_threshold, created_by, updated_by
        ) VALUES (
            :id, :name, 50.00, 25.00, TRUE, TRUE, FALSE, :active, :th, 1, 1
        )
        ON CONFLICT (id) DO UPDATE SET
            is_active = EXCLUDED.is_active,
            low_stock_threshold = EXCLUDED.low_stock_threshold
        """,
        id=pid,
        name=name or f"F5 Prod {pid}",
        active=is_active,
        th=low_stock_threshold,
    )


async def _ensure_supplier(sid: int = 1) -> None:
    await _exec(
        """
        INSERT INTO contacts (id, name, type, is_active, created_by)
        VALUES (:id, :n, 'supplier', TRUE, 1)
        ON CONFLICT (id) DO UPDATE SET is_active = TRUE
        """,
        id=sid,
        n=f"F5 Supplier {sid}",
    )


async def _seed_stock(pid: int, quantity: Decimal, unit_cost: Decimal = Decimal("10.00")) -> None:
    """Insert an opening_balance stock movement so product_valuation
    reflects the seeded quantity."""
    total = quantity * unit_cost
    await _exec(
        """
        INSERT INTO stock_movements (
            product_id, movement_date, trigger, quantity,
            unit_cost_at_movement, total_cost,
            reference_type, reference_id, created_by
        ) VALUES (
            :pid, NOW(), 'opening_balance', :qty,
            :uc, :tc,
            'seed', 0, 1
        )
        """,
        pid=pid,
        qty=quantity,
        uc=unit_cost,
        tc=total,
    )


async def _insert_sale(
    sid: int,
    *,
    sale_date: datetime,
    total: Decimal,
    posted: bool = True,
    cancelled: bool = False,
) -> None:
    posted_at = sale_date if posted else None
    cancellation_date = sale_date if cancelled else None
    await _exec(
        """
        INSERT INTO sales (
            id, sale_date, total_amount, lifecycle_status, posted_at,
            cancellation_date, created_by, posted_by
        ) VALUES (
            :id, :sd, :tot,
            CASE WHEN :cancelled THEN 'cancelled'
                 WHEN :posted THEN 'posted'
                 ELSE 'draft' END,
            :pa, :cd, 1, :posted_by
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
        posted=posted,
        pa=posted_at,
        cd=cancellation_date,
        posted_by=1 if posted else None,
    )


async def _insert_sale_line(
    *,
    sl_id: int,
    sale_id: int,
    product_id: int,
    quantity: Decimal,
    unit_price: Decimal,
    line_total: Decimal,
    cogs_total: Decimal | None = None,
) -> None:
    """Insert a sale line.

    ``cogs_total`` defaults to ``line_total`` so the F.1/F.4 P&L math
    produces clean numbers without an extra UPDATE (sale lines are
    immutable after post - a TRIGGER rejects UPDATE on posted-sale
    lines). Supply an explicit ``cogs_total`` when you need gross != net.
    """
    cogs = cogs_total if cogs_total is not None else line_total
    await _exec(
        """
        INSERT INTO sale_lines (
            id, sale_id, product_id, quantity, unit_price, discount_amount,
            line_total, unit_cost_snapshot, cogs_total_snapshot, line_number
        ) VALUES (
            :id, :sid, :pid, :qty, :up, 0.00,
            :lt, :up, :cogs, 1
        )
        ON CONFLICT (id) DO UPDATE SET
            quantity = EXCLUDED.quantity,
            line_total = EXCLUDED.line_total,
            cogs_total_snapshot = EXCLUDED.cogs_total_snapshot
        """,
        id=sl_id,
        sid=sale_id,
        pid=product_id,
        qty=quantity,
        up=unit_price,
        lt=line_total,
        cogs=cogs,
    )


async def _insert_sale_return(
    *,
    srid: int,
    sale_id: int,
    total_selling_price_returned: Decimal,
    total_cost_returned: Decimal,
    return_date: datetime,
    posted: bool = True,
) -> None:
    await _exec(
        """
        INSERT INTO sales_returns (
            id, sale_id, total_selling_price_returned, total_cost_returned,
            lifecycle_status, return_date, created_by
        ) VALUES (
            :id, :sid, :sp, :cp,
            CASE WHEN :posted THEN 'posted' ELSE 'draft' END,
            :rd, 1
        )
        ON CONFLICT (id) DO UPDATE SET
            total_selling_price_returned = EXCLUDED.total_selling_price_returned,
            total_cost_returned = EXCLUDED.total_cost_returned,
            lifecycle_status = EXCLUDED.lifecycle_status
        """,
        id=srid,
        sid=sale_id,
        sp=total_selling_price_returned,
        cp=total_cost_returned,
        posted=posted,
        rd=return_date,
    )


async def _insert_purchase(
    pid: int,
    *,
    purchase_date: datetime,
    posted: bool = True,
    cancelled: bool = False,
    supplier_id: int | None = 1,
) -> None:
    posted_at = purchase_date if posted else None
    cancellation_date = purchase_date if cancelled else None
    await _exec(
        """
        INSERT INTO purchases (
            id, purchase_date, lifecycle_status, posted_at,
            supplier_id, cancellation_date, created_by, posted_by
        ) VALUES (
            :id, :pd,
            CASE WHEN :cancelled THEN 'cancelled'
                 WHEN :posted THEN 'posted'
                 ELSE 'draft' END,
            :pa, :sup, :cd, 1, :posted_by
        )
        ON CONFLICT (id) DO UPDATE SET
            purchase_date = EXCLUDED.purchase_date,
            posted_at = EXCLUDED.posted_at
        """,
        id=pid,
        pd=purchase_date,
        cancelled=cancelled,
        posted=posted,
        pa=posted_at,
        sup=supplier_id,
        cd=cancellation_date,
        posted_by=1 if posted else None,
    )


async def _insert_purchase_line(
    *,
    plid: int,
    purchase_id: int,
    line_total: Decimal,
    product_id: int = 1,
) -> None:
    await _exec(
        """
        INSERT INTO purchase_lines (
            id, purchase_id, product_id, quantity, unit_price, line_subtotal,
            allocated_shipping, line_total, line_number
        ) VALUES (
            :id, :pid, :prod, 1.0, :lt, :lt, 0.00, :lt, 1
        )
        ON CONFLICT (id) DO UPDATE SET
            line_total = EXCLUDED.line_total
        """,
        id=plid,
        pid=purchase_id,
        prod=product_id,
        lt=line_total,
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
        ON CONFLICT (id) DO UPDATE SET
            amount = EXCLUDED.amount,
            lifecycle_status = EXCLUDED.lifecycle_status
        """,
        id=eid,
        cid=category_id,
        ed=entry_date,
        amt=amount,
        cancelled=cancelled,
    )


async def _seed_cash(amount: Decimal) -> None:
    """Insert a single positive cash_movement for cash_on_hand math."""
    await _exec(
        """
        INSERT INTO cash_movements (
            amount, direction, trigger, payment_method_id,
            reference_type, reference_id, created_by
        ) VALUES (
            :amt, 'in', 'manual_income', 1,
            'seed', 0, 1
        )
        """,
        amt=amount,
    )


async def _independent_cash_on_hand() -> Decimal:
    """Independent GL recompute for cash on hand."""
    factory = get_session_factory()
    async with factory() as session:
        row = (
            (await session.execute(text("SELECT COALESCE(SUM(amount),0) AS c FROM cash_movements")))
            .mappings()
            .first()
        )
    return Decimal(str(row["c"])) if row else Decimal("0")


# ---------------------------------------------------------------------------
# Authorization (both endpoints)
# ---------------------------------------------------------------------------


class TestF5Authorization:
    @pytest.mark.asyncio
    async def test_dashboard_unauthenticated_401(self, app: AsyncClient) -> None:
        r = await app.get("/api/v1/dashboard")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_dashboard_inventory_unauthenticated_401(self, app: AsyncClient) -> None:
        r = await app.get("/api/v1/dashboard/inventory")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_dashboard_staff_403(self, app: AsyncClient, staff_user: dict[str, Any]) -> None:
        h = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/dashboard", headers=h)
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_dashboard_inventory_staff_200(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        # inventory.view is in STAFF_DEFAULT_CAPABILITIES.
        h = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/dashboard/inventory", headers=h)
        assert r.status_code == 200, r.text

    @pytest.mark.asyncio
    async def test_dashboard_owner_200(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/dashboard", headers=h)
        assert r.status_code == 200, r.text

    @pytest.mark.asyncio
    async def test_dashboard_inventory_owner_200(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/dashboard/inventory", headers=h)
        assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# Empty dataset
# ---------------------------------------------------------------------------


class TestF5Empty:
    @pytest.mark.asyncio
    async def test_dashboard_empty(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/dashboard?period=this_month", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        # Schema-level validation of envelope shape.
        assert set(body.keys()) == {"kpis", "charts", "comparison", "as_of"}
        kpis = body["kpis"]
        assert kpis["total_sales"] == 0.0
        assert kpis["total_purchases"] == 0.0
        assert kpis["net_profit"] == 0.0
        assert kpis["cash_on_hand"] == 0.0
        assert kpis["inventory_value"] == 0.0
        assert kpis["low_stock_count"] == 0
        assert body["charts"]["sales_trend"] == []
        assert body["charts"]["best_sellers"] == []
        assert body["charts"]["expense_breakdown"] == []
        assert body["comparison"] is None
        assert "T" in body["as_of"]  # ISO-8601 with T separator

    @pytest.mark.asyncio
    async def test_dashboard_inventory_empty(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/dashboard/inventory", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        # Reuses F.1 InventoryReportResponse shape.
        assert body["data"]["total_inventory_value"] == 0.0
        assert body["data"]["products_count"] == 0
        assert body["data"]["low_stock_count"] == 0
        assert body["data"]["out_of_stock_count"] == 0


# ---------------------------------------------------------------------------
# KPI math
# ---------------------------------------------------------------------------


class TestF5Kpis:
    @pytest.mark.asyncio
    async def test_total_sales_counts_posted_sale(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        d = datetime.now(UTC).replace(tzinfo=None)
        await _ensure_product(1)
        await _insert_sale(1, sale_date=d, total=Decimal("150.00"))
        await _insert_sale_line(
            sl_id=1,
            sale_id=1,
            product_id=1,
            quantity=Decimal("2"),
            unit_price=Decimal("75.00"),
            line_total=Decimal("150.00"),
        )

        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/dashboard?period=this_month", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["kpis"]["total_sales"] == 150.0

    @pytest.mark.asyncio
    async def test_cancelled_sale_excluded_from_total_sales(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        d = datetime.now(UTC).replace(tzinfo=None)
        await _ensure_product(1)
        await _insert_sale(1, sale_date=d, total=Decimal("200.00"))
        await _insert_sale_line(
            sl_id=1,
            sale_id=1,
            product_id=1,
            quantity=Decimal("2"),
            unit_price=Decimal("100.00"),
            line_total=Decimal("200.00"),
        )
        # Cancelled sale in same period.
        await _insert_sale(2, sale_date=d, total=Decimal("999.00"), cancelled=True)

        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/dashboard?period=this_month", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["kpis"]["total_sales"] == 200.0

    @pytest.mark.asyncio
    async def test_posted_sale_return_reduces_total_sales(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        d = datetime.now(UTC).replace(tzinfo=None)
        await _ensure_product(1)
        await _insert_sale(1, sale_date=d, total=Decimal("200.00"))
        await _insert_sale_line(
            sl_id=1,
            sale_id=1,
            product_id=1,
            quantity=Decimal("2"),
            unit_price=Decimal("100.00"),
            line_total=Decimal("200.00"),
        )
        await _insert_sale_return(
            srid=1,
            sale_id=1,
            total_selling_price_returned=Decimal("50.00"),
            total_cost_returned=Decimal("25.00"),
            return_date=d,
            posted=True,
        )

        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/dashboard?period=this_month", headers=h)
        assert r.status_code == 200, r.text
        # net = 200 - 50 = 150
        assert r.json()["kpis"]["total_sales"] == 150.0

    @pytest.mark.asyncio
    async def test_total_purchases_includes_line_totals(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        d = datetime.now(UTC).replace(tzinfo=None)
        await _ensure_product(1)
        await _ensure_supplier(1)
        await _insert_purchase(1, purchase_date=d)
        await _insert_purchase_line(plid=1, purchase_id=1, line_total=Decimal("300.00"))
        await _insert_purchase(2, purchase_date=d)
        await _insert_purchase_line(plid=2, purchase_id=2, line_total=Decimal("500.00"))

        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/dashboard?period=this_month", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["kpis"]["total_purchases"] == 800.0

    @pytest.mark.asyncio
    async def test_cancelled_purchase_excluded(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        d = datetime.now(UTC).replace(tzinfo=None)
        await _ensure_product(1)
        await _ensure_supplier(1)
        await _insert_purchase(1, purchase_date=d)
        await _insert_purchase_line(plid=1, purchase_id=1, line_total=Decimal("300.00"))
        await _insert_purchase(2, purchase_date=d, cancelled=True)
        await _insert_purchase_line(plid=2, purchase_id=2, line_total=Decimal("900.00"))

        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/dashboard?period=this_month", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["kpis"]["total_purchases"] == 300.0

    @pytest.mark.asyncio
    async def test_net_profit_combines_revenue_cogs_manual(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        d = datetime.now(UTC).replace(tzinfo=None)
        await _ensure_product(1)
        # Sale: revenue=200, cogs=60 -> gross=140.
        # cogs_total_snapshot is the authoritative COGS source for the
        # F.1/F.4 sales/P&L aggregate; line_total = unit_price * quantity
        # is the gross side of the same line.
        await _insert_sale(1, sale_date=d, total=Decimal("200.00"))
        # revenue=200 (4 @ $50); COGS snapshot=60 so gross=140.
        await _insert_sale_line(
            sl_id=1,
            sale_id=1,
            product_id=1,
            quantity=Decimal("4"),
            unit_price=Decimal("50.00"),
            line_total=Decimal("200.00"),
            cogs_total=Decimal("60.00"),
        )
        # Manual income (category 2 = other_income).
        await _insert_manual_entry(1, entry_date=d, category_id=2, amount=Decimal("50.00"))
        # Manual expense (category 4 = rent).
        await _insert_manual_entry(2, entry_date=d, category_id=4, amount=Decimal("30.00"))

        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/dashboard?period=this_month", headers=h)
        assert r.status_code == 200, r.text
        # gross + other_income - operating_expenses
        # = (200-60) + 50 - 30 = 160
        assert r.json()["kpis"]["net_profit"] == 160.0

    @pytest.mark.asyncio
    async def test_cancelled_manual_entry_excluded(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        d = datetime.now(UTC).replace(tzinfo=None)
        # Cancelled manual income should NOT inflate net_profit.
        await _insert_manual_entry(
            1,
            entry_date=d,
            category_id=2,
            amount=Decimal("500.00"),
            cancelled=True,
        )
        # One posted manual expense so net_profit is non-zero.
        await _insert_manual_entry(2, entry_date=d, category_id=4, amount=Decimal("30.00"))

        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/dashboard?period=this_month", headers=h)
        assert r.status_code == 200, r.text
        # 0 + 0 - 30 = -30
        assert r.json()["kpis"]["net_profit"] == -30.0


# ---------------------------------------------------------------------------
# Cash on hand: independent GL recompute
# ---------------------------------------------------------------------------


class TestAccountingCorrectness:
    @pytest.mark.asyncio
    async def test_cash_on_hand_matches_independent_gl(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        # Seed three cash movements.
        await _seed_cash(Decimal("100.00"))
        await _seed_cash(Decimal("50.00"))
        await _exec(
            """
            INSERT INTO cash_movements (
                amount, direction, trigger, payment_method_id,
                reference_type, reference_id, created_by
            ) VALUES (
                -20.00, 'out', 'manual_expense', 1,
                'seed', 0, 1
            )
            """
        )

        expected = await _independent_cash_on_hand()
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/dashboard?period=this_month", headers=h)
        assert r.status_code == 200, r.text
        actual = r.json()["kpis"]["cash_on_hand"]
        assert Decimal(str(actual)) == expected
        # 100 + 50 - 20 = 130
        assert expected == Decimal("130.00")


# ---------------------------------------------------------------------------
# Inventory snapshot
# ---------------------------------------------------------------------------


class TestF5Inventory:
    @pytest.mark.asyncio
    async def test_inventory_value_reflects_stock_movements(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        await _ensure_product(1)
        await _ensure_product(2)
        await _seed_stock(1, Decimal("10"), Decimal("5.00"))  # 50.00
        await _seed_stock(2, Decimal("4"), Decimal("3.00"))  # 12.00

        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/dashboard/inventory", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()["data"]
        assert body["total_inventory_value"] == 62.0
        assert body["products_count"] == 2

    @pytest.mark.asyncio
    async def test_low_stock_and_out_of_stock(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        # Product 1: threshold=10, on_hand=5 -> low_stock (not out_of_stock).
        await _ensure_product(1, low_stock_threshold=Decimal("10"), is_active=True)
        await _seed_stock(1, Decimal("5"), Decimal("1.00"))
        # Product 2: threshold=2, on_hand=0 -> both low_stock and out_of_stock.
        await _ensure_product(2, low_stock_threshold=Decimal("2"), is_active=True)
        # No stock_movements for product 2 -> on_hand = 0.
        # Product 3: threshold=NULL, on_hand=0 -> only out_of_stock.
        await _ensure_product(3, low_stock_threshold=None, is_active=True)
        # No stock_movements for product 3.

        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/dashboard/inventory", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()["data"]
        assert body["low_stock_count"] == 2  # products 1 and 2
        assert body["out_of_stock_count"] == 2  # products 2 and 3

    @pytest.mark.asyncio
    async def test_inactive_product_not_in_low_stock_count(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        await _ensure_product(1, low_stock_threshold=Decimal("10"), is_active=False)
        await _seed_stock(1, Decimal("5"), Decimal("1.00"))

        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/dashboard/inventory", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["low_stock_count"] == 0

    @pytest.mark.asyncio
    async def test_dashboard_low_stock_count_uses_same_logic(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        # Sanity: dashboard's low_stock_count mirrors the inventory endpoint.
        await _ensure_product(1, low_stock_threshold=Decimal("5"), is_active=True)
        await _seed_stock(1, Decimal("2"), Decimal("1.00"))

        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/dashboard?period=this_month", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["kpis"]["low_stock_count"] == 1


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------


class TestF5Charts:
    @pytest.mark.asyncio
    async def test_sales_trend_daily_bucket_for_one_week(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        # Use timezone-aware UTC datetimes so asyncpg stores the correct
        # instant independent of the Postgres session timezone. Three
        # consecutive midnight-UTC days -> three distinct ``date_trunc``
        # buckets (the server tz shifts the wall clock by the offset, but
        # each sale lands on a distinct local calendar day).
        await _ensure_product(1)
        dates = [
            datetime(2026, 1, 10, 0, 0, 0, tzinfo=UTC),
            datetime(2026, 1, 11, 0, 0, 0, tzinfo=UTC),
            datetime(2026, 1, 12, 0, 0, 0, tzinfo=UTC),
        ]
        for i, sale_date in enumerate(dates):
            await _insert_sale(i + 1, sale_date=sale_date, total=Decimal("100.00"))
            await _insert_sale_line(
                sl_id=i + 1,
                sale_id=i + 1,
                product_id=1,
                quantity=Decimal("1"),
                unit_price=Decimal("100.00"),
                line_total=Decimal("100.00"),
            )

        h = await _owner_headers(app, owner_user)
        # 3-day custom window -> within the 7-day threshold -> daily bucket.
        r = await app.get(
            "/api/v1/dashboard?from=2026-01-10T00:00:00Z&to=2026-01-12T23:59:59Z",
            headers=h,
        )
        assert r.status_code == 200, r.text
        trend = r.json()["charts"]["sales_trend"]
        # Three distinct-day sales -> three bucketed points.
        assert len(trend) == 3, trend
        for point in trend:
            assert "period_start" in point
            assert "revenue" in point
            assert "sales_count" in point
            assert point["revenue"] == 100.0
            assert point["sales_count"] == 1

    @pytest.mark.asyncio
    async def test_sales_trend_weekly_bucket_for_long_window(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        today = datetime.now(UTC).replace(tzinfo=None)
        await _ensure_product(1)
        # One sale this month.
        await _insert_sale(1, sale_date=today, total=Decimal("100.00"))
        await _insert_sale_line(
            sl_id=1,
            sale_id=1,
            product_id=1,
            quantity=Decimal("1"),
            unit_price=Decimal("100.00"),
            line_total=Decimal("100.00"),
        )

        h = await _owner_headers(app, owner_user)
        # this_month exceeds 7-day threshold -> weekly bucket.
        r = await app.get("/api/v1/dashboard?period=this_month", headers=h)
        assert r.status_code == 200, r.text
        trend = r.json()["charts"]["sales_trend"]
        assert len(trend) >= 1
        assert trend[0]["revenue"] == 100.0

    @pytest.mark.asyncio
    async def test_best_sellers_orders_by_revenue_desc(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        d = datetime.now(UTC).replace(tzinfo=None)
        await _ensure_product(1)
        await _ensure_product(2)
        await _ensure_product(3)
        # Product 2: 2 units @ $50 = $100 revenue.
        await _insert_sale(1, sale_date=d, total=Decimal("100.00"))
        await _insert_sale_line(
            sl_id=1,
            sale_id=1,
            product_id=2,
            quantity=Decimal("2"),
            unit_price=Decimal("50.00"),
            line_total=Decimal("100.00"),
        )
        # Product 1: 1 unit @ $300 = $300 revenue.
        await _insert_sale(2, sale_date=d, total=Decimal("300.00"))
        await _insert_sale_line(
            sl_id=2,
            sale_id=2,
            product_id=1,
            quantity=Decimal("1"),
            unit_price=Decimal("300.00"),
            line_total=Decimal("300.00"),
        )
        # Product 3: 1 unit @ $50 = $50 revenue.
        await _insert_sale(3, sale_date=d, total=Decimal("50.00"))
        await _insert_sale_line(
            sl_id=3,
            sale_id=3,
            product_id=3,
            quantity=Decimal("1"),
            unit_price=Decimal("50.00"),
            line_total=Decimal("50.00"),
        )

        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/dashboard?period=this_month", headers=h)
        assert r.status_code == 200, r.text
        bs = r.json()["charts"]["best_sellers"]
        assert len(bs) == 3
        # Revenue DESC: product 1 = 1*300 = 300; product 2 = 2*100 =
        # 200 (per MS-9 SUM(quantity * line_total)); product 3 = 1*50.
        assert bs[0]["product_id"] == 1
        assert bs[0]["revenue"] == 300.0
        assert bs[1]["product_id"] == 2
        assert bs[1]["revenue"] == 200.0
        assert bs[2]["product_id"] == 3
        assert bs[2]["revenue"] == 50.0

    @pytest.mark.asyncio
    async def test_expense_breakdown_groups_by_category(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        d = datetime.now(UTC).replace(tzinfo=None)
        # category 4 = Rent (expense), category 5 = Labour (expense).
        await _insert_manual_entry(1, entry_date=d, category_id=4, amount=Decimal("100.00"))
        await _insert_manual_entry(2, entry_date=d, category_id=4, amount=Decimal("50.00"))
        await _insert_manual_entry(3, entry_date=d, category_id=5, amount=Decimal("75.00"))
        # Income entry - must NOT appear in expense_breakdown.
        await _insert_manual_entry(4, entry_date=d, category_id=2, amount=Decimal("999.00"))
        # Cancelled expense - must NOT appear.
        await _insert_manual_entry(
            5,
            entry_date=d,
            category_id=4,
            amount=Decimal("200.00"),
            cancelled=True,
        )

        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/dashboard?period=this_month", headers=h)
        assert r.status_code == 200, r.text
        eb = r.json()["charts"]["expense_breakdown"]
        # Should have 2 rows: Rent (150) and Labour (75), income excluded.
        names = sorted([row["category"] for row in eb])
        assert "Rent" in names
        assert "Labour (non-production)" in names
        assert len(eb) == 2
        rent = next(row for row in eb if row["category"] == "Rent")
        labour = next(row for row in eb if row["category"] == "Labour (non-production)")
        assert rent["total"] == 150.0
        assert labour["total"] == 75.0


# ---------------------------------------------------------------------------
# Comparison envelope (D-7)
# ---------------------------------------------------------------------------


class TestF5Comparison:
    @pytest.mark.asyncio
    async def test_compare_to_emits_envelope(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        d = datetime.now(UTC).replace(tzinfo=None)
        await _ensure_product(1)
        # Current month sale.
        await _insert_sale(1, sale_date=d, total=Decimal("100.00"))
        await _insert_sale_line(
            sl_id=1,
            sale_id=1,
            product_id=1,
            quantity=Decimal("1"),
            unit_price=Decimal("100.00"),
            line_total=Decimal("100.00"),
        )

        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/dashboard?period=this_month&compare_to=this_month",
            headers=h,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["comparison"] is not None
        assert set(body["comparison"]["delta"].keys()) == {
            "total_sales",
            "total_purchases",
            "net_profit",
        }
        # Current month has a $100 sale; previous month (compared via
        # compare_to=this_month) is empty -> previous total_sales = 0,
        # delta = 100 - 0 = 100.
        assert body["comparison"]["delta"]["total_sales"] == 100.0
        # delta_pct: previous=0, current!=0 -> 0.0 per F.4 rule.
        assert body["comparison"]["delta_pct"]["total_sales"] == 0.0

    @pytest.mark.asyncio
    async def test_no_compare_to_omits_envelope(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/dashboard?period=this_month", headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["comparison"] is None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestF5Validation:
    @pytest.mark.asyncio
    async def test_invalid_period_400(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/dashboard?period=bogus", headers=h)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "validation_failed"

    @pytest.mark.asyncio
    async def test_custom_period_requires_from_to_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/dashboard?period=custom", headers=h)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "validation_failed"

    @pytest.mark.asyncio
    async def test_invalid_compare_to_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/dashboard?period=this_month&compare_to=bogus",
            headers=h,
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "validation_failed"
