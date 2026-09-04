"""E.9 — Cash movements read-endpoint tests.

Covers the two E.9 OpenAPI operations from ``openapi.yaml`` §15.2
(lines 5053-5133):

* ``GET  /cash-movements``          — ``listCashMovements``  (paginated)
* ``GET  /cash-movements/balance``  — ``getCashBalance``     (aggregate)

Both are READ-ONLY: no Idempotency-Key / If-Match / body and no audit
log row. Capability: ``finance.view_cash``.

Staff default capabilities do NOT include ``finance.view_cash`` (per
``app.authz.caps.STAFF_DEFAULT_CAPABILITIES``), so Staff gets 403.
Only Owner (who holds every capability) is allowed.

Tests seed ``cash_movements`` rows directly via SQL INSERT - the ledger
is append-only and cash movements are created only by server-side
business actions in E.3-E.8, so there is no public write API to use.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.db import get_session_factory

CASH = "cash_movements"


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


def _valid_movement(
    *,
    amount: float,
    direction: str,
    trigger: str = "manual_income",
    payment_method_id: int = 1,
    reference_type: str | None = None,
    reference_id: int | None = None,
    movement_date: str = "2026-09-02T10:00:00+00:00",
    created_by: int = 1,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "amount": amount,
        "direction": direction,
        "trigger": trigger,
        "payment_method_id": payment_method_id,
        "movement_date": datetime.fromisoformat(movement_date),
        "created_by": created_by,
    }
    if reference_type is not None:
        payload["reference_type"] = reference_type
        payload["reference_id"] = reference_id
    return payload


async def _insert_movement(**kw: Any) -> int:
    """Insert a cash_movements row directly via SQL (read endpoint tests)."""
    factory = get_session_factory()
    async with factory() as session:
        cols = ", ".join(kw.keys())
        placeholders = ", ".join(f":{k}" for k in kw.keys())
        row = (
            await session.execute(
                text(
                    f"INSERT INTO cash_movements ({cols}) "
                    f"VALUES ({placeholders}) RETURNING id"
                ),
                kw,
            )
        ).mappings().first()
        await session.commit()
        assert row is not None
        return int(row["id"])


async def _count_movements() -> int:
    factory = get_session_factory()
    async with factory() as session:
        v = await session.scalar(text(f"SELECT COUNT(*) FROM {CASH}"))
        return int(v or 0)


async def _cash_balance() -> float:
    factory = get_session_factory()
    async with factory() as session:
        v = await session.scalar(text("SELECT COALESCE(SUM(amount), 0) FROM cash_movements"))
        return float(v or 0)


async def _truncate_cash() -> None:
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text(f"TRUNCATE TABLE {CASH} RESTART IDENTITY CASCADE"))
        await session.commit()


# ---------------------------------------------------------------------------
# Seed fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _seed_cash(db: None) -> AsyncGenerator[None, None]:
    """Seed a deterministic set of cash movements for read tests."""
    await _truncate_cash()

    # Deterministic movements at known timestamps.
    # Inflows (positive) + outflows (negative).
    await _insert_movement(
        amount=1000.00, direction="in", trigger="manual_income",
        payment_method_id=1,
        reference_type="seed", reference_id=1,
        created_by=1,
        movement_date=datetime.fromisoformat("2026-09-01T09:00:00+00:00"),
    )
    await _insert_movement(
        amount=-200.00, direction="out", trigger="manual_expense",
        payment_method_id=1,
        reference_type="seed", reference_id=2,
        created_by=1,
        movement_date=datetime.fromisoformat("2026-09-02T10:00:00+00:00"),
    )
    await _insert_movement(
        amount=500.00, direction="in", trigger="sale_payment",
        payment_method_id=1,
        reference_type="sale", reference_id=101,
        created_by=1,
        movement_date=datetime.fromisoformat("2026-09-03T11:00:00+00:00"),
    )
    await _insert_movement(
        amount=-150.00, direction="out", trigger="refund",
        payment_method_id=1,
        reference_type="sale", reference_id=101,
        created_by=1,
        movement_date=datetime.fromisoformat("2026-09-03T12:00:00+00:00"),
    )
    await _insert_movement(
        amount=300.00, direction="in", trigger="supplier_repayment",
        payment_method_id=1,
        reference_type="purchase", reference_id=201,
        created_by=1,
        movement_date=datetime.fromisoformat("2026-09-04T08:00:00+00:00"),
    )

    yield
    await _truncate_cash()


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


class TestE9Authorization:
    async def test_list_unauthenticated_returns_401(
        self, app: AsyncClient
    ) -> None:
        r = await app.get("/api/v1/cash-movements")
        assert r.status_code == 401

    async def test_balance_unauthenticated_returns_401(
        self, app: AsyncClient
    ) -> None:
        r = await app.get("/api/v1/cash-movements/balance")
        assert r.status_code == 401

    async def test_list_staff_without_finance_view_cash_returns_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        """Staff defaults do NOT include finance.view_cash → 403."""
        h = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/cash-movements", headers=h)
        assert r.status_code == 403

    async def test_balance_staff_without_finance_view_cash_returns_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        h = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/cash-movements/balance", headers=h)
        assert r.status_code == 403

    async def test_list_owner_allowed(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/cash-movements", headers=h)
        assert r.status_code == 200, r.text

    async def test_balance_owner_allowed(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/cash-movements/balance", headers=h)
        assert r.status_code == 200, r.text


# ---------------------------------------------------------------------------
# GET /cash-movements (listCashMovements)
# ---------------------------------------------------------------------------


class TestListCashMovements:
    async def test_list_happy_path(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/cash-movements", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "data" in body
        assert "pagination" in body
        assert len(body["data"]) == 5

    async def test_list_response_envelope(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/cash-movements",
            headers=h,
            params={"page": 1, "per_page": 2},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        pag = body["pagination"]
        assert pag["page"] == 1
        assert pag["per_page"] == 2
        assert pag["total"] == 5
        assert pag["total_pages"] == 3  # ceil(5/2)

    async def test_list_response_field_types(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/cash-movements", headers=h)
        assert r.status_code == 200
        m = r.json()["data"][0]
        required = {
            "id", "movement_date", "amount", "direction", "trigger",
            "payment_method_id", "created_at", "created_by",
        }
        assert required.issubset(m.keys()), f"missing: {required - set(m.keys())}"
        assert isinstance(m["id"], int)
        assert isinstance(m["amount"], (int, float))
        assert isinstance(m["direction"], str)
        assert isinstance(m["trigger"], str)
        assert isinstance(m["payment_method_id"], int)
        assert isinstance(m["created_by"], int)
        # reference_type / reference_id are optional
        assert "reference_type" in m
        assert "reference_id" in m

    async def test_list_empty_result(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        await _truncate_cash()
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/cash-movements", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["data"] == []
        assert body["pagination"]["total"] == 0
        assert body["pagination"]["total_pages"] == 0

    async def test_list_multiple_rows(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/cash-movements", headers=h)
        assert r.status_code == 200
        assert len(r.json()["data"]) == 5


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


class TestCashMovementFilters:
    async def test_filter_by_trigger(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/cash-movements", headers=h,
            params={"filter[trigger]": "manual_income"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pagination"]["total"] == 1
        assert all(m["trigger"] == "manual_income" for m in body["data"])

    async def test_filter_by_payment_method_id(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/cash-movements", headers=h,
            params={"filter[payment_method_id]": 1},
        )
        assert r.status_code == 200
        # All seeded movements use payment_method_id=1.
        assert r.json()["pagination"]["total"] == 5

    async def test_filter_by_reference_type(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/cash-movements", headers=h,
            params={"filter[reference_type]": "sale"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["pagination"]["total"] == 2
        assert all(m["reference_type"] == "sale" for m in body["data"])

    async def test_filter_by_reference_id(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/cash-movements", headers=h,
            params={"filter[reference_id]": 101},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["pagination"]["total"] == 2
        assert all(m["reference_id"] == 101 for m in body["data"])

    async def test_combined_filters(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/cash-movements", headers=h,
            params={
                "filter[trigger]": "sale_payment",
                "filter[reference_type]": "sale",
                "filter[reference_id]": 101,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pagination"]["total"] == 1
        assert body["data"][0]["trigger"] == "sale_payment"

    async def test_filter_no_match(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/cash-movements", headers=h,
            params={"filter[trigger]": "nonexistent"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["pagination"]["total"] == 0
        assert body["data"] == []


# ---------------------------------------------------------------------------
# Date range
# ---------------------------------------------------------------------------


class TestCashMovementDateRange:
    async def test_filter_from(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/cash-movements", headers=h,
            params={"from": "2026-09-03T00:00:00+00:00"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # Movements on Sep 3 (2 rows) and Sep 4 (1 row) → 3 rows
        assert body["pagination"]["total"] == 3

    async def test_filter_to(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/cash-movements", headers=h,
            params={"to": "2026-09-02T23:59:59+00:00"},
        )
        assert r.status_code == 200
        body = r.json()
        # Sep 1 (income), Sep 2 (expense) → 2 rows
        assert body["pagination"]["total"] == 2

    async def test_filter_from_and_to(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/cash-movements", headers=h,
            params={
                "from": "2026-09-02T00:00:00+00:00",
                "to": "2026-09-02T23:59:59+00:00",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # Only the Sep 2 expense movement.
        assert body["pagination"]["total"] == 1


# ---------------------------------------------------------------------------
# Search (Q)
# ---------------------------------------------------------------------------


class TestCashMovementSearch:
    async def test_q_matches_trigger(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/cash-movements", headers=h,
            params={"q": "manual"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        # manual_income + manual_expense → 2 rows
        assert body["pagination"]["total"] == 2
        assert all("manual" in m["trigger"] for m in body["data"])

    async def test_q_matches_reference_type(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/cash-movements", headers=h,
            params={"q": "sale"},
        )
        assert r.status_code == 200
        body = r.json()
        # "sale_payment" trigger (1) + "refund" with ref_type="sale" (1) = 2
        assert body["pagination"]["total"] == 2

    async def test_q_case_insensitive(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/cash-movements", headers=h,
            params={"q": "MANUAL"},
        )
        assert r.status_code == 200
        assert r.json()["pagination"]["total"] == 2

    async def test_q_no_match(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/cash-movements", headers=h,
            params={"q": "nonexistent_term"},
        )
        assert r.status_code == 200
        assert r.json()["pagination"]["total"] == 0


# ---------------------------------------------------------------------------
# Sorting
# ---------------------------------------------------------------------------


class TestCashMovementSorting:
    async def test_sort_default_is_id_asc(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/cash-movements", headers=h)
        assert r.status_code == 200
        ids = [m["id"] for m in r.json()["data"]]
        assert ids == sorted(ids)

    async def test_sort_desc(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/cash-movements", headers=h, params={"sort": "-id"}
        )
        assert r.status_code == 200, r.text
        ids = [m["id"] for m in r.json()["data"]]
        assert ids == sorted(ids, reverse=True)

    async def test_sort_movement_date_asc(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/cash-movements", headers=h, params={"sort": "movement_date"}
        )
        assert r.status_code == 200, r.text
        dates = [m["movement_date"] for m in r.json()["data"]]
        assert dates == sorted(dates)

    async def test_sort_movement_date_desc(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/cash-movements", headers=h, params={"sort": "-movement_date"}
        )
        assert r.status_code == 200, r.text
        dates = [m["movement_date"] for m in r.json()["data"]]
        assert dates == sorted(dates, reverse=True)

    async def test_sort_unknown_falls_back_to_id_asc(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/cash-movements", headers=h, params={"sort": "bogus"}
        )
        assert r.status_code == 200
        # Falls back to stable id ASC ordering.
        ids = [m["id"] for m in r.json()["data"]]
        assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class TestCashMovementPagination:
    async def test_first_page(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/cash-movements", headers=h,
            params={"page": 1, "per_page": 2},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["pagination"]["page"] == 1
        assert body["pagination"]["per_page"] == 2
        assert len(body["data"]) == 2

    async def test_second_page(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r1 = await app.get(
            "/api/v1/cash-movements", headers=h, params={"page": 1, "per_page": 2}
        )
        r2 = await app.get(
            "/api/v1/cash-movements", headers=h, params={"page": 2, "per_page": 2}
        )
        assert r1.status_code == 200 and r2.status_code == 200
        first_ids = {m["id"] for m in r1.json()["data"]}
        second_ids = {m["id"] for m in r2.json()["data"]}
        assert first_ids.isdisjoint(second_ids), "pages must not overlap"

    async def test_total_and_total_pages(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/cash-movements", headers=h, params={"page": 1, "per_page": 2}
        )
        assert r.status_code == 200
        pag = r.json()["pagination"]
        assert pag["total"] == 5
        assert pag["total_pages"] == 3  # ceil(5/2)


# ---------------------------------------------------------------------------
# GET /cash-movements/balance (getCashBalance)
# ---------------------------------------------------------------------------


class TestGetCashBalance:
    async def test_balance_matches_sum(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/cash-movements/balance", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        expected = await _cash_balance()
        assert body["balance"] == pytest.approx(expected)
        # 1000 - 200 + 500 - 150 + 300 = 1450
        assert body["balance"] == pytest.approx(1450.00)

    async def test_balance_as_of_present_and_valid(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/cash-movements/balance", headers=h)
        assert r.status_code == 200
        as_of = r.json()["as_of"]
        assert as_of is not None
        # Must be a parseable ISO datetime string.
        datetime.fromisoformat(as_of)

    async def test_balance_empty_ledger(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        await _truncate_cash()
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/cash-movements/balance", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["balance"] == 0.0 or body["balance"] == 0

    async def test_balance_unauthenticated_returns_401(
        self, app: AsyncClient
    ) -> None:
        r = await app.get("/api/v1/cash-movements/balance")
        assert r.status_code == 401

    async def test_balance_staff_returns_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        h = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/cash-movements/balance", headers=h)
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Read-only guarantee
# ---------------------------------------------------------------------------


class TestReadOnlyGuarantee:
    async def test_list_does_not_mutate_ledger(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        before = await _count_movements()
        await app.get("/api/v1/cash-movements", headers=h)
        await app.get(
            "/api/v1/cash-movements", headers=h,
            params={"sort": "-movement_date", "filter[trigger]": "manual_income"},
        )
        after = await _count_movements()
        assert before == after, "list endpoint must not mutate cash_movements"

    async def test_balance_does_not_mutate_ledger(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        before = await _count_movements()
        await app.get("/api/v1/cash-movements/balance", headers=h)
        after = await _count_movements()
        assert before == after, "balance endpoint must not mutate cash_movements"
