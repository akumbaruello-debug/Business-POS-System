"""F.1 — Reports tests.

Covers the four F.1 endpoints (``GET /reports/{sales,purchases,inventory,
inventory-movements}``) per the audit's test matrix. The tests
seed ``sales`` / ``purchases`` / ``sales_returns`` / ``products`` /
``stock_movements`` directly via SQL — these ledgers have no public
write API suitable for unit-style seeding (the public sale/purchase
endpoints are end-to-end integration flows that drag in payments,
cancellations, etc.; the reports are pure reads so we just
materialize the data they need).

D-8 evidence: ``sales.total_amount`` is **gross** and is preserved
on every return lifecycle transition (the only column the lifecycle
trigger updates is ``lifecycle_status``; the create-return service
uses ``set_lifecycle_status``). Therefore the F.1 revenue formula is
``SUM(s.total_amount) - SUM(sr.total_selling_price_returned)`` over
posted, non-cancelled sales in the period. Cancelled returns
(``sales_returns.lifecycle_status = 'cancelled'``) are excluded.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
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


async def _execmany(sql: str, rows: list[dict[str, Any]]) -> None:
    factory = get_session_factory()
    async with factory() as session:
        for row in rows:
            await session.execute(text(sql), row)
        await session.commit()


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _user_id(user: dict[str, Any]) -> int:
    return int(user["user_id"])


# ---------------------------------------------------------------------------
# Sales fixtures
# ---------------------------------------------------------------------------


async def _insert_product(
    pid: int,
    *,
    threshold: int | None = None,
    created_by: int = 1,
) -> None:
    await _exec(
        f"""
        INSERT INTO products (
            id, name, selling_price, purchase_price, is_sellable, is_purchasable,
            is_producible, is_active, low_stock_threshold, created_by, updated_by
        ) VALUES (
            :id, :name, 50.00, 25.00, TRUE, TRUE, FALSE, TRUE, :th, {created_by}, {created_by}
        )
        ON CONFLICT (id) DO UPDATE SET
            is_active = TRUE,
            low_stock_threshold = EXCLUDED.low_stock_threshold
        """,
        id=pid,
        name=f"F.1 Prod {pid}",
        th=threshold,
    )


async def _insert_sale(
    sid: int,
    *,
    sale_date: datetime,
    total: Decimal,
    cogs_per_line: Decimal,
    num_lines: int = 1,
    posted: bool = True,
    cancelled: bool = False,
    return_total: Decimal | None = None,
    return_cogs: Decimal | None = None,
    return_cancelled: bool = False,
) -> None:
    """Insert a posted (or draft) sale with N lines and an optional sales return.

    All amounts use NUMERIC(15,2)/(15,4) to match the schema.

    A default product (id=1) must exist before this is called (use
    ``_insert_product(1)``); the sale lines reference it.
    """
    posted_at = sale_date if posted else None
    cancellation_date = sale_date if cancelled else None
    await _exec(
        """
        INSERT INTO sales (
            id, sale_date, total_amount, lifecycle_status, posted_at,
            cancellation_date, created_by, posted_by
        ) VALUES (
            :id, :sd, :tot,
            CASE WHEN :cancelled THEN 'cancelled' WHEN :posted THEN 'posted' ELSE 'draft' END,
            :pa, :cd, 1, :posted_by
        )
        """,
        id=sid,
        sd=sale_date,
        tot=total,
        posted=posted,
        cancelled=cancelled,
        pa=posted_at,
        cd=cancellation_date,
        posted_by=1 if posted else None,
    )
    # Lines
    line_total = (total / Decimal(num_lines)).quantize(Decimal("0.01"))
    for i in range(1, num_lines + 1):
        await _exec(
            """
            INSERT INTO sale_lines (
                id, sale_id, product_id, quantity, unit_price, discount_amount,
                line_total, unit_cost_snapshot, cogs_total_snapshot, line_number
            ) VALUES (
                :id, :sid, 1, 1.0, :up, 0.00,
                :lt, :uc, :cogs, :ln
            )
            """,
            id=(sid * 100) + i,
            sid=sid,
            up=line_total,
            lt=line_total,
            uc=cogs_per_line,
            cogs=cogs_per_line,
            ln=i,
        )
    # Optional return
    if return_total is not None:
        await _exec(
            """
            INSERT INTO sales_returns (
                id, sale_id, return_date, total_selling_price_returned,
                total_cost_returned, lifecycle_status, created_by
            ) VALUES (
                :id, :sid, :rd, :ts, :tc,
                CASE WHEN :cancelled THEN 'cancelled' ELSE 'posted' END,
                1
            )
            """,
            id=sid * 10,
            sid=sid,
            rd=sale_date,
            ts=return_total,
            tc=return_cogs or Decimal("0.00"),
            cancelled=return_cancelled,
        )


# ---------------------------------------------------------------------------
# Purchases fixtures
# ---------------------------------------------------------------------------


async def _insert_purchase(
    pid: int,
    *,
    purchase_date: datetime,
    line_total: Decimal = Decimal("100.00"),
    posted: bool = True,
    cancelled: bool = False,
    supplier_id: int | None = None,
) -> None:
    posted_at = purchase_date if posted else None
    cancellation_date = purchase_date if cancelled else None
    await _exec(
        """
        INSERT INTO purchases (
            id, purchase_date, lifecycle_status, posted_at, posted_by,
            cancellation_date, supplier_id, created_by
        ) VALUES (
            :id, :pd,
            CASE WHEN :cancelled THEN 'cancelled' WHEN :posted THEN 'posted' ELSE 'draft' END,
            :pa, :posted_by, :cd, :sid, 1
        )
        """,
        id=pid,
        pd=purchase_date,
        posted=posted,
        cancelled=cancelled,
        pa=posted_at,
        posted_by=1 if posted else None,
        cd=cancellation_date,
        sid=supplier_id,
    )
    await _exec(
        """
        INSERT INTO purchase_lines (
            id, purchase_id, product_id, quantity, unit_price,
            line_subtotal, allocated_shipping, line_total, line_number
        ) VALUES (:id, :pid, 1, 1.0, :up, :ls, 0.00, :lt, 1)
        """,
        id=pid * 10,
        pid=pid,
        up=line_total,
        ls=line_total,
        lt=line_total,
    )


# ---------------------------------------------------------------------------
# Stock movement fixture (for inventory-movements)
# ---------------------------------------------------------------------------


async def _insert_movement(
    mid: int,
    product_id: int,
    *,
    movement_date: datetime,
    quantity: Decimal = Decimal("1.0000"),
    trigger: str = "opening_balance",
    unit_cost: Decimal = Decimal("1.0000"),
    reference_type: str | None = None,
    reference_id: int | None = None,
    reason: str | None = "seed",
) -> None:
    """Insert a stock_movements row.

    ``total_cost`` is derived as ``quantity * unit_cost`` (carrying
    the sign of quantity) so the ``ck_sm_total_cost_nonzero`` check
    constraint is satisfied for non-``value_adjustment`` triggers.
    """
    total_cost = (quantity * unit_cost).quantize(Decimal("0.0000"))
    await _exec(
        """
        INSERT INTO stock_movements (
            id, product_id, movement_date, trigger, quantity,
            unit_cost_at_movement, total_cost, reference_type, reference_id,
            created_by, reason
        ) VALUES (
            :id, :pid, :md, :tr, :q,
            :uc, :tc, :rt, :rid, 1, :reason
        )
        """,
        id=mid,
        pid=product_id,
        md=movement_date,
        tr=trigger,
        q=quantity,
        uc=unit_cost,
        tc=total_cost,
        rt=reference_type,
        rid=reference_id,
        reason=reason,
    )


# ===========================================================================
# Authorization
# ===========================================================================


class TestF1Authorization:
    @pytest.mark.asyncio
    async def test_sales_unauthenticated_401(self, app: AsyncClient) -> None:
        r = await app.get("/api/v1/reports/sales")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_purchases_unauthenticated_401(self, app: AsyncClient) -> None:
        r = await app.get("/api/v1/reports/purchases")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_inventory_unauthenticated_401(self, app: AsyncClient) -> None:
        r = await app.get("/api/v1/reports/inventory")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_inventory_movements_unauthenticated_401(
        self, app: AsyncClient
    ) -> None:
        r = await app.get("/api/v1/reports/inventory-movements")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_sales_staff_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        h = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/reports/sales", headers=h)
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_purchases_staff_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        h = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/reports/purchases", headers=h)
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_inventory_staff_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        h = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/reports/inventory", headers=h)
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_inventory_movements_staff_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        h = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/reports/inventory-movements", headers=h)
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_sales_owner_200(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/sales",
            headers=h,
            params={"period": "this_month"},
        )
        assert r.status_code == 200, r.text

    @pytest.mark.asyncio
    async def test_purchases_owner_200(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/reports/purchases", headers=h)
        assert r.status_code == 200, r.text

    @pytest.mark.asyncio
    async def test_inventory_owner_200(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/reports/inventory", headers=h)
        assert r.status_code == 200, r.text

    @pytest.mark.asyncio
    async def test_inventory_movements_owner_200(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/reports/inventory-movements", headers=h)
        assert r.status_code == 200, r.text


# ===========================================================================
# /reports/sales
# ===========================================================================


class TestSalesReport:
    @pytest.fixture(autouse=True)
    async def _seed_product(
        self, app: AsyncClient
    ) -> AsyncGenerator[None, None]:
        """Seed product 1 so the sale_lines FK is satisfied.

        Function-scope autouse that depends on ``app`` so the
        conftest's function-scope ``db`` fixture runs first — its
        TRUNCATE clears ``products`` before we re-insert. The
        product ID is reused (id=1) since ``db`` does
        ``RESTART IDENTITY CASCADE`` on every test.
        """
        await _insert_product(1)
        yield

    @pytest.mark.asyncio
    async def test_empty_db_all_zero(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/sales", headers=h, params={"period": "this_month"}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["data"]["revenue"] == 0
        assert body["data"]["cogs"] == 0
        assert body["data"]["gross_profit"] == 0
        assert body["data"]["sales_count"] == 0
        assert body["data"]["average_ticket"] == 0
        assert body.get("comparison") is None

    @pytest.mark.asyncio
    async def test_one_posted_sale(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        now = datetime(2026, 9, 2, 10, 0, 0, tzinfo=timezone.utc)
        await _insert_sale(
            1, sale_date=now, total=Decimal("100.00"), cogs_per_line=Decimal("40.00")
        )
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/sales",
            headers=h,
            params={"from": _iso(now), "to": _iso(now)},
        )
        assert r.status_code == 200
        d = r.json()["data"]
        assert d["revenue"] == 100.0
        assert d["cogs"] == 40.0
        assert d["gross_profit"] == 60.0
        assert d["sales_count"] == 1
        assert d["average_ticket"] == 100.0

    @pytest.mark.asyncio
    async def test_cancelled_sale_excluded(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        now = datetime(2026, 9, 2, 10, 0, 0, tzinfo=timezone.utc)
        await _insert_sale(
            1, sale_date=now, total=Decimal("100.00"),
            cogs_per_line=Decimal("40.00"), cancelled=True,
        )
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/sales",
            headers=h,
            params={"from": _iso(now), "to": _iso(now)},
        )
        assert r.status_code == 200
        d = r.json()["data"]
        assert d["sales_count"] == 0
        assert d["revenue"] == 0

    @pytest.mark.asyncio
    async def test_draft_sale_excluded(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        now = datetime(2026, 9, 2, 10, 0, 0, tzinfo=timezone.utc)
        await _insert_sale(
            1, sale_date=now, total=Decimal("100.00"),
            cogs_per_line=Decimal("40.00"), posted=False,
        )
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/sales",
            headers=h,
            params={"from": _iso(now), "to": _iso(now)},
        )
        assert r.status_code == 200
        d = r.json()["data"]
        assert d["sales_count"] == 0

    @pytest.mark.asyncio
    async def test_inclusive_date_boundaries(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        # Sale at exactly the from and to boundaries must be included.
        a = datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)
        b = datetime(2026, 9, 1, 23, 59, 59, 999999, tzinfo=timezone.utc)
        await _insert_sale(
            1, sale_date=a, total=Decimal("50.00"), cogs_per_line=Decimal("20.00")
        )
        await _insert_sale(
            2, sale_date=b, total=Decimal("70.00"), cogs_per_line=Decimal("30.00")
        )
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/sales",
            headers=h,
            params={"from": _iso(a), "to": _iso(b)},
        )
        assert r.status_code == 200
        d = r.json()["data"]
        assert d["sales_count"] == 2
        assert d["revenue"] == 120.0

    @pytest.mark.asyncio
    async def test_period_this_month(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        # A sale in the current month must be included.
        now = datetime.now().astimezone()
        await _insert_sale(
            1, sale_date=now, total=Decimal("75.00"), cogs_per_line=Decimal("30.00")
        )
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/sales", headers=h, params={"period": "this_month"}
        )
        assert r.status_code == 200
        d = r.json()["data"]
        assert d["sales_count"] == 1
        assert d["revenue"] == 75.0

    @pytest.mark.asyncio
    async def test_compare_to(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        # Two sales in two distinct windows; compare_to computes the
        # delta. Window of one hour each, adjacent.
        h = await _owner_headers(app, owner_user)
        a = datetime(2026, 9, 2, 10, 0, 0, tzinfo=timezone.utc)
        b = datetime(2026, 9, 2, 11, 0, 0, tzinfo=timezone.utc)
        c = datetime(2026, 9, 2, 12, 0, 0, tzinfo=timezone.utc)
        await _insert_sale(
            1, sale_date=a, total=Decimal("100.00"), cogs_per_line=Decimal("40.00")
        )
        await _insert_sale(
            2, sale_date=b, total=Decimal("50.00"), cogs_per_line=Decimal("20.00")
        )
        # Comparison window: 08:00 - 09:59:59.999999 (empty)
        compare_from = datetime(2026, 9, 2, 8, 0, 0, tzinfo=timezone.utc)
        compare_to = datetime(2026, 9, 2, 9, 59, 59, 999999, tzinfo=timezone.utc)
        r = await app.get(
            "/api/v1/reports/sales",
            headers=h,
            params={
                "from": _iso(a),
                "to": _iso(b),
                "compare_to": f"{_iso(compare_from)},{_iso(compare_to)}",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["comparison"] is not None
        d = body["comparison"]["delta"]
        assert d["revenue"] == 150.0  # current 150 - previous 0
        assert d["sales_count"] == 2.0
        # delta_pct: previous=0, current!=0 → 0.0 (per the service logic)
        assert body["comparison"]["delta_pct"]["revenue"] == 0.0

    @pytest.mark.asyncio
    async def test_no_compare_to_no_comparison_block(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/sales", headers=h, params={"period": "this_month"}
        )
        assert r.status_code == 200
        assert r.json().get("comparison") is None

    @pytest.mark.asyncio
    async def test_return_deducted_from_revenue_and_cogs(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        # D-8 evidence: sales.total_amount is GROSS. A return of
        # $30 (selling) and $12 (cost) against a $100 sale must
        # yield revenue=70, cogs=28, gp=42.
        now = datetime(2026, 9, 2, 10, 0, 0, tzinfo=timezone.utc)
        await _insert_sale(
            1,
            sale_date=now,
            total=Decimal("100.00"),
            cogs_per_line=Decimal("40.00"),
            return_total=Decimal("30.00"),
            return_cogs=Decimal("12.00"),
        )
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/sales",
            headers=h,
            params={"from": _iso(now), "to": _iso(now)},
        )
        assert r.status_code == 200
        d = r.json()["data"]
        assert d["revenue"] == 70.0
        assert d["cogs"] == 28.0
        assert d["gross_profit"] == 42.0
        assert d["sales_count"] == 1

    @pytest.mark.asyncio
    async def test_cancelled_return_not_deducted(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        now = datetime(2026, 9, 2, 10, 0, 0, tzinfo=timezone.utc)
        await _insert_sale(
            1,
            sale_date=now,
            total=Decimal("100.00"),
            cogs_per_line=Decimal("40.00"),
            return_total=Decimal("30.00"),
            return_cogs=Decimal("12.00"),
            return_cancelled=True,
        )
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/sales",
            headers=h,
            params={"from": _iso(now), "to": _iso(now)},
        )
        d = r.json()["data"]
        assert d["revenue"] == 100.0
        assert d["cogs"] == 40.0

    @pytest.mark.asyncio
    async def test_invalid_period_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/sales", headers=h, params={"period": "yesterday"}
        )
        assert r.status_code == 400


# ===========================================================================
# /reports/purchases
# ===========================================================================


class TestPurchasesReport:
    @pytest.fixture(autouse=True)
    async def _seed_product(
        self, app: AsyncClient
    ) -> AsyncGenerator[None, None]:
        """Seed product 1 so ``purchase_lines.product_id`` is valid."""
        await _insert_product(1)
        yield

    @pytest.mark.asyncio
    async def test_empty_result(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/reports/purchases", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["data"] == []
        assert body["pagination"]["total"] == 0
        assert body["pagination"]["total_pages"] == 0
        assert body.get("comparison") is None

    @pytest.mark.asyncio
    async def test_pagination(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        now = datetime(2026, 9, 2, 10, 0, 0, tzinfo=timezone.utc)
        for i in range(1, 6):
            await _insert_purchase(
                i, purchase_date=now, line_total=Decimal(f"{i * 10}.00")
            )
        r = await app.get(
            "/api/v1/reports/purchases",
            headers=h,
            params={"page": 1, "per_page": 2},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["pagination"]["total"] == 5
        assert body["pagination"]["total_pages"] == 3
        assert len(body["data"]) == 2

    @pytest.mark.asyncio
    async def test_per_page_501_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/purchases",
            headers=h,
            params={"per_page": 501},
        )
        assert r.status_code == 400  # Query bounds → 422, custom validation handler maps to 400

    @pytest.mark.asyncio
    async def test_invalid_sort_falls_back(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        # The repo whitelist falls back to ``id`` for unknown sort
        # values; the route layer accepts any string. So the request
        # succeeds and the response is still 200.
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/purchases", headers=h, params={"sort": "not_a_field"}
        )
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_sort_desc_purchase_date(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        d1 = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
        d2 = datetime(2026, 9, 2, 10, 0, 0, tzinfo=timezone.utc)
        await _insert_purchase(1, purchase_date=d1)
        await _insert_purchase(2, purchase_date=d2)
        r = await app.get(
            "/api/v1/reports/purchases",
            headers=h,
            params={"sort": "-purchase_date"},
        )
        body = r.json()
        assert body["data"][0]["id"] == 2
        assert body["data"][1]["id"] == 1

    @pytest.mark.asyncio
    async def test_cancelled_purchase_excluded(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        now = datetime(2026, 9, 2, 10, 0, 0, tzinfo=timezone.utc)
        await _insert_purchase(1, purchase_date=now, cancelled=True)
        await _insert_purchase(2, purchase_date=now)
        r = await app.get("/api/v1/reports/purchases", headers=h)
        body = r.json()
        assert body["pagination"]["total"] == 1
        assert body["data"][0]["id"] == 2

    @pytest.mark.asyncio
    async def test_date_filter(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        a = datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)
        b = datetime(2026, 9, 30, 23, 59, 59, tzinfo=timezone.utc)
        await _insert_purchase(1, purchase_date=a)
        await _insert_purchase(2, purchase_date=b)
        r = await app.get(
            "/api/v1/reports/purchases",
            headers=h,
            params={"from": _iso(a), "to": _iso(b)},
        )
        assert r.json()["pagination"]["total"] == 2

    @pytest.mark.asyncio
    async def test_compare_to_block(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        now = datetime(2026, 9, 2, 10, 0, 0, tzinfo=timezone.utc)
        await _insert_purchase(1, purchase_date=now, line_total=Decimal("100.00"))
        compare_from = datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)
        compare_to = datetime(2026, 9, 1, 23, 59, 59, tzinfo=timezone.utc)
        r = await app.get(
            "/api/v1/reports/purchases",
            headers=h,
            params={
                "from": _iso(now),
                "to": _iso(now),
                "compare_to": f"{_iso(compare_from)},{_iso(compare_to)}",
            },
        )
        assert r.status_code == 200
        comp = r.json().get("comparison")
        assert comp is not None
        assert comp["delta"]["purchase_count"] == 1.0
        assert comp["delta"]["total_amount"] == 100.0


# ===========================================================================
# /reports/inventory
# ===========================================================================


class TestInventoryReport:
    @pytest.mark.asyncio
    async def test_empty_db(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/reports/inventory", headers=h)
        assert r.status_code == 200
        d = r.json()["data"]
        assert d["total_inventory_value"] == 0
        assert d["products_count"] == 0
        assert d["low_stock_count"] == 0
        assert d["out_of_stock_count"] == 0

    @pytest.mark.asyncio
    async def test_low_stock_and_out_of_stock(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        # 3 products: one above threshold, one below threshold (low
        # stock), one with zero stock (out of stock, also low if
        # threshold is set).
        await _insert_product(901, threshold=10)  # opening 5
        await _insert_product(902, threshold=10)  # opening 5
        await _insert_product(903, threshold=10)  # opening 0
        # Movements to set on-hand
        now = datetime(2026, 9, 2, 10, 0, 0, tzinfo=timezone.utc)
        await _insert_movement(901, 901, movement_date=now, quantity=Decimal("5"))
        await _insert_movement(902, 902, movement_date=now, quantity=Decimal("5"))
        # 903: no opening movement → on_hand=0
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/reports/inventory", headers=h)
        d = r.json()["data"]
        assert d["products_count"] == 3
        # 902 has 5 <= threshold 10 → low_stock
        # 903 has 0 <= threshold 10 → low_stock + out_of_stock
        # 901 has 5 <= threshold 10 → low_stock
        assert d["low_stock_count"] == 3
        assert d["out_of_stock_count"] == 1

    @pytest.mark.asyncio
    async def test_inactive_excluded_from_counts(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        # 1 active product with 0 stock + 1 inactive product with 0 stock.
        await _insert_product(911, threshold=10)
        # 912: insert active, then deactivate
        await _insert_product(912, threshold=10)
        await _exec(
            "UPDATE products SET is_active = FALSE WHERE id = 912"
        )
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/reports/inventory", headers=h)
        d = r.json()["data"]
        # Both have on_hand=0 and threshold=10, but only 911 is
        # active → only 911 counts in low_stock and out_of_stock.
        # 912 is still in products_count (active + inactive per B.7).
        assert d["products_count"] == 2
        assert d["low_stock_count"] == 1
        assert d["out_of_stock_count"] == 1

    @pytest.mark.asyncio
    async def test_zero_stock_counted_as_out_of_stock(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        # A product with no stock_movements rows has on_hand_quantity=0
        # by the COALESCE in the ``product_valuation`` view. With a
        # non-null threshold it is both low_stock AND out_of_stock.
        await _insert_product(921, threshold=10)
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/reports/inventory", headers=h)
        d = r.json()["data"]
        assert d["out_of_stock_count"] == 1
        assert d["low_stock_count"] == 1

    @pytest.mark.asyncio
    async def test_no_comparison_block(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        # The inventory response is a flat object with no ``comparison``
        # field. Sending ``compare_to`` must be accepted (for
        # contract parity) and ignored.
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/inventory",
            headers=h,
            params={"compare_to": "previous_month"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "comparison" not in body


# ===========================================================================
# /reports/inventory-movements
# ===========================================================================


class TestInventoryMovementsReport:
    @pytest.mark.asyncio
    async def test_empty(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/reports/inventory-movements", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["data"] == []
        assert body["pagination"]["total"] == 0

    @pytest.mark.asyncio
    async def test_inclusive_date_filter(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        await _insert_product(931)
        a = datetime(2026, 9, 1, 0, 0, 0, tzinfo=timezone.utc)
        b = datetime(2026, 9, 1, 23, 59, 59, 999999, tzinfo=timezone.utc)
        await _insert_movement(931, 931, movement_date=a)
        await _insert_movement(932, 931, movement_date=b)
        # Outside the window
        await _insert_movement(
            933,
            931,
            movement_date=datetime(2026, 9, 2, 0, 0, 0, tzinfo=timezone.utc),
        )
        r = await app.get(
            "/api/v1/reports/inventory-movements",
            headers=h,
            params={"from": _iso(a), "to": _iso(b)},
        )
        body = r.json()
        assert body["pagination"]["total"] == 2

    @pytest.mark.asyncio
    async def test_pagination(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        await _insert_product(941)
        now = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
        for i in range(25):
            await _insert_movement(
                1000 + i, 941, movement_date=now, quantity=Decimal(str(i + 1))
            )
        r = await app.get(
            "/api/v1/reports/inventory-movements",
            headers=h,
            params={"page": 1, "per_page": 10},
        )
        body = r.json()
        assert body["pagination"]["total"] == 25
        assert body["pagination"]["total_pages"] == 3
        assert len(body["data"]) == 10

    @pytest.mark.asyncio
    async def test_sort_desc_movement_date(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        await _insert_product(951)
        a = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
        b = datetime(2026, 9, 2, 10, 0, 0, tzinfo=timezone.utc)
        await _insert_movement(951, 951, movement_date=a)
        await _insert_movement(952, 951, movement_date=b)
        r = await app.get(
            "/api/v1/reports/inventory-movements",
            headers=h,
            params={"sort": "-movement_date"},
        )
        body = r.json()
        assert body["data"][0]["movement_date"] > body["data"][1]["movement_date"]

    @pytest.mark.asyncio
    async def test_filter_product_id(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        await _insert_product(961)
        await _insert_product(962)
        now = datetime(2026, 9, 2, 10, 0, 0, tzinfo=timezone.utc)
        await _insert_movement(961, 961, movement_date=now)
        await _insert_movement(962, 962, movement_date=now)
        r = await app.get(
            "/api/v1/reports/inventory-movements",
            headers=h,
            params={"filter[product_id]": 961},
        )
        body = r.json()
        assert body["pagination"]["total"] == 1
        assert body["data"][0]["product_id"] == 961

    @pytest.mark.asyncio
    async def test_per_page_501_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/inventory-movements",
            headers=h,
            params={"per_page": 501},
        )
        assert r.status_code == 400  # Query bounds → 422, custom validation handler maps to 400
