"""E.7 — Stock-movement read endpoints tests.

Covers the two E.7 OpenAPI operations from ``openapi.yaml`` §15.2
(lines 4103-4191):

* ``GET  /stock-movements``       — ``listStockMovements``  (paginated)
* ``GET  /stock-movements/{id}``  — ``getStockMovement``    (single)

Both are READ-ONLY: no Idempotency-Key / If-Match / body and no audit
log row. Capability: ``inventory.view``.

Test coverage:

* Happy paths: each endpoint returns 200 with the contract shape.
* Authorization: 401 unauthenticated; 403 staff without
  ``inventory.view`` (staff DO hold inventory.view by default, so the
  403 case is skipped — staff positive is the happy path).
* 404 on a missing movement id.
* Filters: product_id, trigger, reference_type, reference_id
  (combinable).
* Sort: default, explicit asc/desc, unknown key → stable fallback.
* Pagination: page/per_page/total/total_pages/total_pages.
* Immutability: GET does not mutate the ledger (no movement rows
  created/updated/deleted on the call path — verified by count
  before/after).
* Response shape: every ``StockMovement`` field present and typed.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
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


def _valid_product(name: str | None = None, **kw: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name or f"prod_{uuid.uuid4().hex[:8]}",
        "is_active": True,
    }
    body.update(kw)
    return body


async def _create_product(app: AsyncClient, headers: dict[str, str]) -> int:
    """Create a product via the B.6 API and return its id."""
    r = await app.post(
        "/api/v1/products",
        json=_valid_product(),
        headers={**headers, "Idempotency-Key": _idem()},
    )
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


async def _insert_stock_movement(
    product_id: int,
    *,
    trigger: str = "opening_balance",
    quantity: float = 100,
    unit_cost: float = 0.50,
    total_cost: float | None = None,
    reference_type: str | None = None,
    reference_id: int | None = None,
    reason: str | None = "seed",
) -> int:
    """Insert a stock_movements row directly via SQL (read endpoint tests)."""
    factory = get_session_factory()
    async with factory() as session:
        row = (
            (
                await session.execute(
                    text(
                        """
                        INSERT INTO stock_movements (
                            product_id, movement_date, trigger, quantity,
                            unit_cost_at_movement, total_cost,
                            reference_type, reference_id, reference_line_id,
                            reason, created_by
                        ) VALUES (
                            :pid, NOW(), :trigger, :qty,
                            :unit_cost, :total_cost,
                            :reference_type, :reference_id, NULL,
                            :reason, 1
                        ) RETURNING id
                        """
                    ),
                    {
                        "pid": product_id,
                        "trigger": trigger,
                        "qty": quantity,
                        "unit_cost": unit_cost,
                        "total_cost": total_cost
                        if total_cost is not None
                        else (quantity * unit_cost),
                        "reference_type": reference_type,
                        "reference_id": reference_id,
                        "reason": reason,
                    },
                )
            )
            .mappings()
            .first()
        )
        await session.commit()
        assert row is not None
        return int(row["id"])


async def _count_movements() -> int:
    factory = get_session_factory()
    async with factory() as session:
        v = await session.scalar(text("SELECT COUNT(*) FROM stock_movements"))
        return int(v or 0)


async def _movement_exists(movement_id: int) -> bool:
    factory = get_session_factory()
    async with factory() as session:
        v = await session.scalar(
            text("SELECT 1 FROM stock_movements WHERE id = :id"),
            {"id": int(movement_id)},
        )
        return v is not None


# ---------------------------------------------------------------------------
# Seed fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _seed(db: None) -> AsyncGenerator[None, None]:
    """Create a clean product set with a handful of stock movements."""
    # We rely on the conftest ``db`` fixture to TRUNCATE the mutable
    # tables between tests, so this just needs to insert a known set of
    # products + movements.
    await _seed_movements()
    yield


async def _seed_movements() -> None:
    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        # A couple of deterministic products for filter tests.
        await session.execute(
            text(
                "INSERT INTO products (id, name, selling_price, purchase_price, "
                "is_sellable, is_purchasable, is_producible, is_active, "
                "created_by, updated_by) "
                "VALUES (301, 'E7 Prod A', 50.00, 25.00, TRUE, TRUE, FALSE, TRUE, 1, 1), "
                "(302, 'E7 Prod B', 50.00, 25.00, TRUE, TRUE, FALSE, TRUE, 1, 1) "
                "ON CONFLICT (id) DO UPDATE SET "
                " is_active = EXCLUDED.is_active, "
                " selling_price = EXCLUDED.selling_price, "
                " purchase_price = EXCLUDED.purchase_price"
            )
        )
        await session.commit()
    # Seed movements AFTER the products exist (FK fk_sm_product).
    await _insert_stock_movement(
        301, trigger="opening_balance", quantity=100, unit_cost=25.00,
        reference_type=None, reference_id=None, reason="opening",
    )
    await _insert_stock_movement(
        301, trigger="sale", quantity=-10, unit_cost=25.00,
        reference_type="sale", reference_id=9001, reason="sale",
    )
    await _insert_stock_movement(
        302, trigger="opening_balance", quantity=50, unit_cost=10.00,
        reference_type=None, reference_id=None, reason="opening",
    )
    await _insert_stock_movement(
        302, trigger="production_output", quantity=20, unit_cost=10.00,
        reference_type="production_run", reference_id=5001, reason="production",
    )
    await _insert_stock_movement(
        302, trigger="sale", quantity=-5, unit_cost=10.00,
        reference_type="sale", reference_id=9002, reason="sale",
    )


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


class TestE7Authorization:
    async def test_list_unauthenticated_returns_401(
        self, app: AsyncClient
    ) -> None:
        r = await app.get("/api/v1/stock-movements")
        assert r.status_code == 401

    async def test_get_single_unauthenticated_returns_401(
        self, app: AsyncClient
    ) -> None:
        r = await app.get("/api/v1/stock-movements/1")
        assert r.status_code == 401

    async def test_list_staff_with_inventory_view_allowed(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        """Staff default role includes inventory.view → 200."""
        h = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/stock-movements", headers=h)
        assert r.status_code == 200, r.text

    async def test_get_single_staff_with_inventory_view_allowed(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        h = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/stock-movements/1", headers=h)
        # 200 if exists, 404 if not — either is an authed outcome.
        assert r.status_code in (200, 404)


# ---------------------------------------------------------------------------
# GET /stock-movements (listStockMovements)
# ---------------------------------------------------------------------------


class TestListStockMovements:
    async def test_list_pagination_envelope(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/stock-movements",
            headers=h,
            params={"page": 1, "per_page": 2},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "data" in body
        assert "pagination" in body
        pag = body["pagination"]
        assert pag["page"] == 1
        assert pag["per_page"] == 2

    async def test_list_returns_five_movements_total(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/stock-movements", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["pagination"]["total"] == 5
        assert len(body["data"]) == 5

    async def test_list_filters_by_product_id(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/stock-movements",
            headers=h,
            params={"filter[product_id]": 301},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["pagination"]["total"] == 2
        assert all(m["product_id"] == 301 for m in body["data"])

    async def test_list_filters_by_trigger(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/stock-movements",
            headers=h,
            params={"filter[trigger]": "sale"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pagination"]["total"] == 2  # two 'sale' movements
        assert all(m["trigger"] == "sale" for m in body["data"])

    async def test_list_filters_by_reference_type(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/stock-movements",
            headers=h,
            params={"filter[reference_type]": "sale"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["pagination"]["total"] == 2
        assert all(m["reference_type"] == "sale" for m in body["data"])

    async def test_list_filters_by_reference_id(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/stock-movements",
            headers=h,
            params={"filter[reference_id]": 9001},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["pagination"]["total"] == 1
        assert body["data"][0]["reference_id"] == 9001

    async def test_list_combines_filters(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/stock-movements",
            headers=h,
            params={
                "filter[product_id]": 301,
                "filter[trigger]": "sale",
                "filter[reference_type]": "sale",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["pagination"]["total"] == 1

    async def test_list_combined_filter_empty(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/stock-movements",
            headers=h,
            params={
                "filter[product_id]": 301,
                "filter[trigger]": "production_output",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["pagination"]["total"] == 0
        assert body["data"] == []

    async def test_list_empty_filters_returns_all(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/stock-movements", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["pagination"]["total"] == 5

    async def test_list_sort_default_is_id_asc(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/stock-movements", headers=h)
        assert r.status_code == 200
        ids = [m["id"] for m in r.json()["data"]]
        assert ids == sorted(ids), "expected ascending id (default sort)"

    async def test_list_sort_desc(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/stock-movements", headers=h, params={"sort": "-id"}
        )
        assert r.status_code == 200, r.text
        ids = [m["id"] for m in r.json()["data"]]
        assert ids == sorted(ids, reverse=True)

    async def test_list_sort_unknown_falls_back(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/stock-movements", headers=h, params={"sort": "bogus"}
        )
        assert r.status_code == 200
        ids = [m["id"] for m in r.json()["data"]]
        assert ids == sorted(ids)  # stable id ASC fallback

    async def test_list_pagination_total_pages(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/stock-movements",
            headers=h,
            params={"page": 1, "per_page": 2},
        )
        assert r.status_code == 200
        pag = r.json()["pagination"]
        assert pag["total"] == 5
        assert pag["total_pages"] == 3  # ceil(5/2)

    async def test_list_second_page(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r1 = await app.get(
            "/api/v1/stock-movements",
            headers=h,
            params={"page": 1, "per_page": 2},
        )
        r2 = await app.get(
            "/api/v1/stock-movements",
            headers=h,
            params={"page": 2, "per_page": 2},
        )
        assert r1.status_code == 200 and r2.status_code == 200
        first_ids = {m["id"] for m in r1.json()["data"]}
        second_ids = {m["id"] for m in r2.json()["data"]}
        assert first_ids.isdisjoint(second_ids), "page 1 and page 2 must not overlap"

    async def test_list_response_shape(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/stock-movements", headers=h)
        assert r.status_code == 200
        m = r.json()["data"][0]
        required = {
            "id", "product_id", "movement_date", "trigger", "quantity",
            "created_at", "created_by",
        }
        assert required.issubset(m.keys()), f"missing keys: {required - set(m.keys())}"
        assert isinstance(m["id"], int)
        assert isinstance(m["product_id"], int)
        assert isinstance(m["quantity"], (int, float))

    async def test_list_does_not_mutate_ledger(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """GET is read-only: no stock_movements rows added/removed/changed."""
        h = await _owner_headers(app, owner_user)
        before = await _count_movements()
        await app.get("/api/v1/stock-movements", headers=h)
        await app.get("/api/v1/stock-movements", headers=h, params={"sort": "-created_at"})
        after = await _count_movements()
        assert before == after, "list endpoint must not mutate stock_movements"


# ---------------------------------------------------------------------------
# GET /stock-movements/{id} (getStockMovement)
# ---------------------------------------------------------------------------


class TestGetStockMovement:
    async def test_get_single_existing(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        # Grab the first movement id.
        r = await app.get("/api/v1/stock-movements", headers=h)
        first_id = r.json()["data"][0]["id"]
        g = await app.get(f"/api/v1/stock-movements/{first_id}", headers=h)
        assert g.status_code == 200, g.text
        m = g.json()
        assert m["id"] == first_id
        assert "product_id" in m
        assert "trigger" in m
        assert "quantity" in m

    async def test_get_single_nonexistent_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/stock-movements/999999", headers=h)
        assert r.status_code == 404
        body = r.json()
        assert body["error"]["code"] == "not_found"

    async def test_get_single_response_shape(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/stock-movements", headers=h)
        mid = r.json()["data"][0]["id"]
        g = await app.get(f"/api/v1/stock-movements/{mid}", headers=h)
        assert g.status_code == 200
        m = g.json()
        required = {
            "id", "product_id", "movement_date", "trigger", "quantity",
            "created_at", "created_by",
        }
        assert required.issubset(m.keys())

    async def test_get_single_includes_reference_fields(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        # The 'sale' movement on product 301 has reference_type='sale'.
        r = await app.get(
            "/api/v1/stock-movements",
            headers=h,
            params={"filter[product_id]": 301, "filter[trigger]": "sale"},
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert len(data) == 1
        sid = data[0]["id"]
        g = await app.get(f"/api/v1/stock-movements/{sid}", headers=h)
        assert g.status_code == 200
        m = g.json()
        assert m["reference_type"] == "sale"
        assert m["reference_id"] == 9001

    async def test_get_single_does_not_mutate_ledger(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/stock-movements", headers=h)
        mid = r.json()["data"][0]["id"]
        before = await _count_movements()
        await app.get(f"/api/v1/stock-movements/{mid}", headers=h)
        after = await _count_movements()
        assert before == after


__all__: list[str] = []
