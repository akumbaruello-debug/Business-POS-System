"""F.2 — Reports tests (sales-returns, purchase-returns, production).

Covers the three F.2 endpoints per ``Backend-Implementation-Plan-V1.0``
§6 (lines 677-682):

* ``GET /reports/sales-returns``  — paginated list of ``sales_returns``
  header rows (no new row model — same columns as the source table).
* ``GET /reports/purchase-returns`` — paginated list of ``purchase_returns``.
* ``GET /reports/production`` — paginated list of ``production_runs``.

All three endpoints accept only ``Period`` / ``From`` / ``To`` /
``CompareTo`` per the F.2 OpenAPI contract (line 5312-5376) — no
``page`` / ``per_page`` / ``sort`` / ``q`` / filters / ``include``
on the wire. Server-side default pagination is ``page=1``,
``per_page=50`` to match the F.1 convention.

The tests seed the source tables directly via SQL (same pattern as
F.1). All three ledgers are independent of the sale/purchase ledgers
for FK purposes (sales_returns references sales; purchase_returns
references purchases; production_runs references products), so the
seed helpers create just enough parent rows to satisfy the FKs.
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
# Ledger seed helpers
# ---------------------------------------------------------------------------


async def _ensure_product(pid: int = 1) -> None:
    """Seed product id=1 (the FK target for sales/purchases/production)."""
    await _exec(
        """
        INSERT INTO products (
            id, name, selling_price, purchase_price, is_sellable, is_purchasable,
            is_producible, is_active, created_by, updated_by
        ) VALUES (
            :id, :name, 50.00, 25.00, TRUE, TRUE, TRUE, TRUE, 1, 1
        )
        ON CONFLICT (id) DO UPDATE SET
            is_active = TRUE,
            is_producible = TRUE,
            is_sellable = TRUE,
            is_purchasable = TRUE
        """,
        id=pid,
        name=f"F.2 Prod {pid}",
    )


async def _insert_sale_header(sid: int, *, sale_date: datetime) -> None:
    """Minimal sale header — ``sales_returns`` only references ``sales.id``."""
    await _exec(
        """
        INSERT INTO sales (
            id, sale_date, total_amount, lifecycle_status, posted_at, created_by
        ) VALUES (
            :id, :sd, 100.00, 'posted', :sd, 1
        )
        """,
        id=sid,
        sd=sale_date,
    )


async def _insert_purchase_header(pid: int, *, purchase_date: datetime) -> None:
    """Minimal purchase header — ``purchase_returns`` only references ``purchases.id``."""
    await _exec(
        """
        INSERT INTO purchases (
            id, purchase_date, lifecycle_status, posted_at, created_by
        ) VALUES (
            :id, :pd, 'posted', :pd, 1
        )
        """,
        id=pid,
        pd=purchase_date,
    )


async def _insert_sales_return(
    rid: int,
    *,
    sale_id: int,
    return_date: datetime,
    selling: Decimal = Decimal("50.00"),
    cost: Decimal = Decimal("25.00"),
    cancelled: bool = False,
) -> None:
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
        id=rid,
        sid=sale_id,
        rd=return_date,
        ts=selling,
        tc=cost,
        cancelled=cancelled,
    )


async def _insert_purchase_return(
    rid: int,
    *,
    purchase_id: int,
    return_date: datetime,
    value: Decimal = Decimal("75.00"),
) -> None:
    await _exec(
        """
        INSERT INTO purchase_returns (
            id, purchase_id, return_date, total_value_returned,
            lifecycle_status, created_by
        ) VALUES (
            :id, :pid, :rd, :tv, 'posted', 1
        )
        """,
        id=rid,
        pid=purchase_id,
        rd=return_date,
        tv=value,
    )


async def _insert_production_run(
    rid: int,
    *,
    run_date: datetime,
    raw_cost: Decimal = Decimal("100.00"),
    overhead: Decimal = Decimal("50.00"),
    lifecycle: str = "posted",
    posted: bool = True,
) -> None:
    posted_at = run_date if posted else None
    await _exec(
        """
        INSERT INTO production_runs (
            id, run_date, output_product_id, output_quantity,
            finished_unit_cost, total_raw_cost, total_overhead_cost,
            lifecycle_status, posted_at, created_by
        ) VALUES (
            :id, :rd, 1, 10.0, 15.0, :rc, :oh, :lc, :pa, 1
        )
        """,
        id=rid,
        rd=run_date,
        rc=raw_cost,
        oh=overhead,
        lc=lifecycle,
        pa=posted_at,
    )


# ===========================================================================
# Authorization (all three endpoints)
# ===========================================================================


class TestF2Authorization:
    @pytest.mark.asyncio
    async def test_sales_returns_unauthenticated_401(self, app: AsyncClient) -> None:
        r = await app.get("/api/v1/reports/sales-returns")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_purchase_returns_unauthenticated_401(self, app: AsyncClient) -> None:
        r = await app.get("/api/v1/reports/purchase-returns")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_production_unauthenticated_401(self, app: AsyncClient) -> None:
        r = await app.get("/api/v1/reports/production")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_sales_returns_staff_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        h = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/reports/sales-returns", headers=h)
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_purchase_returns_staff_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        h = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/reports/purchase-returns", headers=h)
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_production_staff_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        h = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/reports/production", headers=h)
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_sales_returns_owner_200(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/sales-returns", headers=h, params={"period": "this_month"}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["data"] == []
        assert body["pagination"]["page"] == 1
        assert body["pagination"]["per_page"] == 50
        assert body["pagination"]["total"] == 0
        assert body.get("comparison") is None

    @pytest.mark.asyncio
    async def test_purchase_returns_owner_200(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/purchase-returns",
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
    async def test_production_owner_200(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/production", headers=h, params={"period": "this_month"}
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["data"] == []
        assert body["pagination"]["page"] == 1
        assert body["pagination"]["per_page"] == 50
        assert body["pagination"]["total"] == 0
        assert body.get("comparison") is None


# ===========================================================================
# /reports/sales-returns
# ===========================================================================


class TestSalesReturnsReport:
    @pytest.fixture(autouse=True)
    async def _seed_product(self, app: AsyncClient) -> AsyncGenerator[None, None]:
        await _ensure_product(1)
        yield

    @pytest.mark.asyncio
    async def test_one_posted_return(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        now = datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC)
        await _insert_sale_header(1, sale_date=now)
        await _insert_sales_return(
            1, sale_id=1, return_date=now,
            selling=Decimal("50.00"), cost=Decimal("25.00"),
        )
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/sales-returns",
            headers=h,
            params={"from": _iso(now), "to": _iso(now)},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pagination"]["total"] == 1
        assert len(body["data"]) == 1
        row = body["data"][0]
        assert row["sale_id"] == 1
        assert Decimal(str(row["total_selling_price_returned"])) == Decimal("50.00")
        assert Decimal(str(row["total_cost_returned"])) == Decimal("25.00")
        assert row["lifecycle_status"] == "posted"

    @pytest.mark.asyncio
    async def test_cancelled_return_still_listed(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Lifecycle is preserved in the row — clients filter client-side.

        The DB never hard-deletes a sales return (only UPDATE OF
        lifecycle_status per ``schema.sql`` §18.16), so the report
        MUST surface cancelled rows for traceability.
        """
        now = datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC)
        await _insert_sale_header(1, sale_date=now)
        await _insert_sales_return(
            1, sale_id=1, return_date=now, cancelled=True,
        )
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/sales-returns",
            headers=h,
            params={"from": _iso(now), "to": _iso(now)},
        )
        assert r.status_code == 200
        assert r.json()["pagination"]["total"] == 1
        assert r.json()["data"][0]["lifecycle_status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_inclusive_date_boundaries(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        a = datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC)
        b = datetime(2026, 9, 1, 23, 59, 59, 999999, tzinfo=UTC)
        await _insert_sale_header(1, sale_date=a)
        await _insert_sale_header(2, sale_date=b)
        await _insert_sales_return(1, sale_id=1, return_date=a)
        await _insert_sales_return(2, sale_id=2, return_date=b)
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/sales-returns",
            headers=h,
            params={"from": _iso(a), "to": _iso(b)},
        )
        assert r.status_code == 200
        assert r.json()["pagination"]["total"] == 2

    @pytest.mark.asyncio
    async def test_out_of_period_excluded(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        inside = datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC)
        outside = datetime(2026, 8, 1, 10, 0, 0, tzinfo=UTC)
        await _insert_sale_header(1, sale_date=inside)
        await _insert_sale_header(2, sale_date=outside)
        await _insert_sales_return(1, sale_id=1, return_date=inside)
        await _insert_sales_return(2, sale_id=2, return_date=outside)
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/sales-returns",
            headers=h,
            params={"from": _iso(inside), "to": _iso(inside)},
        )
        assert r.status_code == 200
        assert r.json()["pagination"]["total"] == 1

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
        await _insert_sales_return(
            1, sale_id=1, return_date=a,
            selling=Decimal("100.00"), cost=Decimal("50.00"),
        )
        await _insert_sale_header(2, sale_date=b)
        await _insert_sales_return(
            2, sale_id=2, return_date=b,
            selling=Decimal("50.00"), cost=Decimal("25.00"),
        )
        r = await app.get(
            "/api/v1/reports/sales-returns",
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
        # Current: 2 returns, 150.00 selling; previous: 0, 0.
        assert d["return_count"] == 2
        assert d["total_amount"] == 150.0
        # previous==0, current!=0 → delta_pct == 0.0 (per F.1 convention)
        assert body["comparison"]["delta_pct"]["return_count"] == 0.0
        assert body["comparison"]["delta_pct"]["total_amount"] == 0.0

    @pytest.mark.asyncio
    async def test_invalid_period_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/sales-returns",
            headers=h,
            params={"period": "bogus"},
        )
        assert r.status_code == 400


# ===========================================================================
# /reports/purchase-returns
# ===========================================================================


class TestPurchaseReturnsReport:
    @pytest.fixture(autouse=True)
    async def _seed_product(self, app: AsyncClient) -> AsyncGenerator[None, None]:
        await _ensure_product(1)
        yield

    @pytest.mark.asyncio
    async def test_one_posted_return(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        now = datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC)
        await _insert_purchase_header(1, purchase_date=now)
        await _insert_purchase_return(1, purchase_id=1, return_date=now)
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/purchase-returns",
            headers=h,
            params={"from": _iso(now), "to": _iso(now)},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pagination"]["total"] == 1
        assert len(body["data"]) == 1
        row = body["data"][0]
        assert row["purchase_id"] == 1
        assert Decimal(str(row["total_value_returned"])) == Decimal("75.00")
        assert row["lifecycle_status"] == "posted"

    @pytest.mark.asyncio
    async def test_compare_to(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        a = datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC)
        b = datetime(2026, 9, 2, 11, 0, 0, tzinfo=UTC)
        compare_from = datetime(2026, 9, 2, 8, 0, 0, tzinfo=UTC)
        compare_to = datetime(2026, 9, 2, 9, 59, 59, 999999, tzinfo=UTC)
        await _insert_purchase_header(1, purchase_date=a)
        await _insert_purchase_return(
            1, purchase_id=1, return_date=a, value=Decimal("100.00"),
        )
        await _insert_purchase_header(2, purchase_date=b)
        await _insert_purchase_return(
            2, purchase_id=2, return_date=b, value=Decimal("200.00"),
        )
        r = await app.get(
            "/api/v1/reports/purchase-returns",
            headers=h,
            params={
                "from": _iso(a),
                "to": _iso(b),
                "compare_to": f"{_iso(compare_from)},{_iso(compare_to)}",
            },
        )
        assert r.status_code == 200, r.text
        d = r.json()["comparison"]["delta"]
        assert d["return_count"] == 2
        assert d["total_amount"] == 300.0

    @pytest.mark.asyncio
    async def test_period_this_month(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        now = datetime.now().astimezone()
        await _insert_purchase_header(1, purchase_date=now)
        await _insert_purchase_return(1, purchase_id=1, return_date=now)
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/purchase-returns",
            headers=h,
            params={"period": "this_month"},
        )
        assert r.status_code == 200
        assert r.json()["pagination"]["total"] == 1


# ===========================================================================
# /reports/production
# ===========================================================================


class TestProductionReport:
    @pytest.fixture(autouse=True)
    async def _seed_product(self, app: AsyncClient) -> AsyncGenerator[None, None]:
        await _ensure_product(1)
        yield

    @pytest.mark.asyncio
    async def test_one_posted_run(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        now = datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC)
        await _insert_production_run(1, run_date=now)
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/production",
            headers=h,
            params={"from": _iso(now), "to": _iso(now)},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pagination"]["total"] == 1
        row = body["data"][0]
        assert row["lifecycle_status"] == "posted"
        assert row["output_product_id"] == 1

    @pytest.mark.asyncio
    async def test_all_lifecycles_listed(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """All four lifecycle states (draft / posted / completed / cancelled)
        are surfaced — the lifecycle column is preserved so clients can
        filter client-side.
        """
        now = datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC)
        await _insert_production_run(1, run_date=now, lifecycle="draft", posted=False)
        await _insert_production_run(2, run_date=now, lifecycle="posted")
        await _insert_production_run(3, run_date=now, lifecycle="completed")
        await _insert_production_run(4, run_date=now, lifecycle="cancelled")
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/production",
            headers=h,
            params={"from": _iso(now), "to": _iso(now)},
        )
        assert r.status_code == 200
        assert r.json()["pagination"]["total"] == 4
        statuses = {row["lifecycle_status"] for row in r.json()["data"]}
        assert statuses == {"draft", "posted", "completed", "cancelled"}

    @pytest.mark.asyncio
    async def test_compare_to_excludes_drafts(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Comparison aggregate counts only posted/completed/cancelled.

        Draft runs have unsettled costs (the production post lifecycle
        is the financial cutover — per E.3), so they are NOT in the
        comparison envelope. The list still shows them.

        Setup: primary window (10:00-11:00) has 1 posted run (run #1,
        150.00) + 1 draft run (run #2, excluded from aggregate).
        Previous window (08:00-09:59) has 1 completed run (run #3,
        280.00). The comparison envelope therefore reflects
        current=1/150 vs previous=1/280 → delta=0/-130.
        """
        h = await _owner_headers(app, owner_user)
        a = datetime(2026, 9, 2, 10, 0, 0, tzinfo=UTC)
        b = datetime(2026, 9, 2, 11, 0, 0, tzinfo=UTC)
        compare_from = datetime(2026, 9, 2, 8, 0, 0, tzinfo=UTC)
        compare_to = datetime(2026, 9, 2, 9, 59, 59, 999999, tzinfo=UTC)
        # Primary: 1 posted (100+50=150) + 1 draft (excluded from agg)
        await _insert_production_run(
            1, run_date=a, raw_cost=Decimal("100.00"),
            overhead=Decimal("50.00"), lifecycle="posted",
        )
        await _insert_production_run(
            2, run_date=b, raw_cost=Decimal("40.00"),
            overhead=Decimal("20.00"), lifecycle="draft", posted=False,
        )
        # Previous: 1 completed (200+80=280)
        prev_a = datetime(2026, 9, 2, 8, 30, 0, tzinfo=UTC)
        await _insert_production_run(
            3, run_date=prev_a, raw_cost=Decimal("200.00"),
            overhead=Decimal("80.00"), lifecycle="completed",
        )
        r = await app.get(
            "/api/v1/reports/production",
            headers=h,
            params={
                "from": _iso(a),
                "to": _iso(b),
                "compare_to": f"{_iso(compare_from)},{_iso(compare_to)}",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pagination"]["total"] == 2  # both runs listed (posted + draft)
        d = body["comparison"]["delta"]
        # Current (posted only): 1 run, 150.00; previous (completed): 1, 280.00
        assert d["run_count"] == 0.0  # 1 - 1
        assert d["total_amount"] == -130.0  # 150 - 280
        # Both periods non-zero but count delta is 0 → delta_pct is 0.0
        # (per F.1 convention: previous != 0 and current != 0 → delta/previous)
        assert body["comparison"]["delta_pct"]["run_count"] == 0.0
        assert (
            abs(body["comparison"]["delta_pct"]["total_amount"] - (-130.0 / 280.0))
            < 1e-9
        )

    @pytest.mark.asyncio
    async def test_inclusive_date_boundaries(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        a = datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC)
        b = datetime(2026, 9, 1, 23, 59, 59, 999999, tzinfo=UTC)
        await _insert_production_run(1, run_date=a)
        await _insert_production_run(2, run_date=b)
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/production",
            headers=h,
            params={"from": _iso(a), "to": _iso(b)},
        )
        assert r.status_code == 200
        assert r.json()["pagination"]["total"] == 2

    @pytest.mark.asyncio
    async def test_invalid_period_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/production",
            headers=h,
            params={"period": "garbage"},
        )
        assert r.status_code == 400


# ===========================================================================
# Cross-endpoint: response envelope keys
# ===========================================================================


class TestF2ResponseEnvelope:
    @pytest.mark.asyncio
    async def test_sales_returns_envelope_shape(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/sales-returns",
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
    async def test_purchase_returns_envelope_shape(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/purchase-returns",
            headers=h,
            params={"period": "this_month"},
        )
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) >= {"data", "pagination"}

    @pytest.mark.asyncio
    async def test_production_envelope_shape(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/reports/production",
            headers=h,
            params={"period": "this_month"},
        )
        assert r.status_code == 200
        body = r.json()
        assert set(body.keys()) >= {"data", "pagination"}
