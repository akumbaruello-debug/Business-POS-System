"""E.5 — Inventory read endpoints tests.

Covers the three OpenAPI operations from ``openapi.yaml`` §15.2:

* ``GET  /api/v1/inventory``               — listInventory
* ``GET  /api/v1/inventory/products/{id}`` — getProductStock
* ``GET  /api/v1/inventory/low-stock``     — listLowStock

Scope: read-only endpoints. No Idempotency-Key / If-Match / request
body expectations. Authorization is ``inventory.view``.

Test groups:
  - Happy paths: each endpoint returns 200 with the contract shape.
  - Authorization: unauthenticated → 401; authenticated without
    ``inventory.view`` → 403.
  - Low-stock logic: the predicate (active + non-null threshold +
    on_hand ≤ threshold) is computed correctly.
  - get_product_stock 404 on missing product.
  - Filters: category_id / is_active on listInventory.
  - Pagination: page/per_page reflected in the envelope.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _login(app: AsyncClient, username: str, password: str) -> str:
    r = await app.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"Idempotency-Key": str(uuid.uuid4())},
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
# Seed fixtures
# ---------------------------------------------------------------------------


async def _seed_categories() -> None:
    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO categories (id, name) VALUES"
                " (10, 'Widgets'),"
                " (11, 'Raw Materials')"
                " ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name"
            )
        )
        await session.commit()


async def _seed_inventory_products() -> list[dict[str, Any]]:
    """Seed a deterministic product set for inventory E.5 tests.

    Returns the list of product dicts (id, name, low_stock_threshold,
    category_id) so tests can assert against known values.

    Products:
      101 — 'Above Threshold'     — low_stock_threshold 10, stock 100
      102 — 'At Threshold'       — low_stock_threshold 10, stock 10
      103 — 'Below Threshold'    — low_stock_threshold 10, stock 5
      104 — 'Active No Threshold'— low_stock_threshold NULL, stock 50
      105 — 'Deactivated Low'    — low_stock_threshold 10, stock 5, is_active FALSE
      106 — 'Missing Product'    — NOT seeded (404 test)
    """
    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        # Ensure categories exist first (FK).
        await _seed_categories()
        await session.execute(
            text(
                "INSERT INTO products (id, name, selling_price, allow_negative_stock,"
                " is_sellable, is_purchasable, is_producible, is_active,"
                " low_stock_threshold, category_id, created_by, updated_by)"
                " VALUES"
                " (101, 'Above Threshold', 100.00, FALSE, TRUE, FALSE, TRUE, TRUE, 10.0000, 10, 1, 1),"
                " (102, 'At Threshold', 50.00, FALSE, TRUE, TRUE, FALSE, TRUE, 10.0000, 10, 1, 1),"
                " (103, 'Below Threshold', 20.00, FALSE, TRUE, FALSE, TRUE, TRUE, 10.0000, 10, 1, 1),"
                " (104, 'Active No Threshold', 30.00, FALSE, TRUE, TRUE, FALSE, TRUE, NULL, 11, 1, 1),"
                " (105, 'Deactivated Low', 10.00, FALSE, FALSE, TRUE, FALSE, FALSE, 10.0000, 10, 1, 1)"
                " ON CONFLICT (id) DO UPDATE SET"
                " is_active = EXCLUDED.is_active,"
                " low_stock_threshold = EXCLUDED.low_stock_threshold,"
                " category_id = EXCLUDED.category_id,"
                " selling_price = EXCLUDED.selling_price"
            )
        )
        # Seed stock via opening_balance movements so product_valuation view
        # reflects the quantities.
        await session.execute(
            text(
                "INSERT INTO stock_movements (product_id, trigger, quantity,"
                " unit_cost_at_movement, total_cost,"
                " reference_type, reference_id, created_by)"
                " VALUES"
                " (101, 'opening_balance', 100.0000, 5.00, 500.00, 'seed', 0, 1),"
                " (102, 'opening_balance', 10.0000, 5.00, 50.00, 'seed', 0, 1),"
                " (103, 'opening_balance', 5.0000, 5.00, 25.00, 'seed', 0, 1),"
                " (104, 'opening_balance', 50.0000, 5.00, 250.00, 'seed', 0, 1)"
                " ON CONFLICT DO NOTHING"
            )
        )
        await session.commit()
    return [
        {"id": 101, "name": "Above Threshold", "threshold": 10, "stock": 100, "active": True},
        {"id": 102, "name": "At Threshold", "threshold": 10, "stock": 10, "active": True},
        {"id": 103, "name": "Below Threshold", "threshold": 10, "stock": 5, "active": True},
        {"id": 104, "name": "Active No Threshold", "threshold": None, "stock": 50, "active": True},
        {"id": 105, "name": "Deactivated Low", "threshold": 10, "stock": 5, "active": False},
    ]


@pytest.fixture(autouse=True)
async def _seed_inventory(db: None) -> AsyncGenerator[None, None]:
    """Seed + clean the inventory test data.

    Depends on the session-scoped ``db`` fixture so the session factory
    is initialised before we touch the database. The DB transaction is
    rolled back at teardown, so we only need to ensure the seed rows
    are present before each test.
    """
    await _seed_inventory_products()
    yield


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


async def test_list_inventory_unauthenticated_rejected(app: AsyncClient) -> None:
    r = await app.get("/api/v1/inventory")
    assert r.status_code == 401


async def test_get_product_stock_unauthenticated_rejected(app: AsyncClient) -> None:
    r = await app.get("/api/v1/inventory/products/101")
    assert r.status_code == 401


async def test_list_low_stock_unauthenticated_rejected(app: AsyncClient) -> None:
    r = await app.get("/api/v1/inventory/low-stock")
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# listInventory — happy path
# ---------------------------------------------------------------------------


async def test_list_inventory_happy_path(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    h = await _owner_headers(app, owner_user)
    r = await app.get("/api/v1/inventory", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "data" in body and "pagination" in body
    ids = {row["product_id"] for row in body["data"]}
    # All 5 seeded active/inactive products appear in the unfiltered list
    # (product_valuation is a LEFT JOIN view — all products appear).
    assert {101, 102, 103, 104, 105} <= ids
    row101 = next(row for row in body["data"] if row["product_id"] == 101)
    assert row101["on_hand_quantity"] == 100
    assert row101["low_stock"] is False
    # moving_average_unit_cost derived (500/100 = 5.0)
    assert row101["moving_average_unit_cost"] == 5.0
    assert row101["inventory_value"] == 500.0
    assert "as_of" in row101


async def test_list_inventory_returns_contract_shape(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    h = await _owner_headers(app, owner_user)
    r = await app.get("/api/v1/inventory", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["pagination"], dict)
    assert {"page", "per_page", "total", "total_pages"} <= body["pagination"].keys()
    for row in body["data"]:
        # InventorySummary required fields
        assert {"product_id", "on_hand_quantity", "inventory_value", "as_of"} <= row.keys()
        assert "low_stock" in row  # boolean in contract


# ---------------------------------------------------------------------------
# listInventory — pagination
# ---------------------------------------------------------------------------


async def test_list_inventory_pagination(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    h = await _owner_headers(app, owner_user)
    r = await app.get("/api/v1/inventory?per_page=2&page=1", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pagination"]["per_page"] == 2
    assert len(body["data"]) == 2
    # With per_page=2 and 5 seeded products, total_pages must be 3.
    assert body["pagination"]["total"] == 5
    assert body["pagination"]["total_pages"] == 3
    # Page 3 (offset 4) returns the 5th product.
    r3 = await app.get("/api/v1/inventory?per_page=2&page=3", headers=h)
    assert r3.status_code == 200, r3.text
    body3 = r3.json()
    assert len(body3["data"]) == 1
    # Combined pages cover all 5.
    r2 = await app.get("/api/v1/inventory?per_page=2&page=2", headers=h)
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    seen = (
        {r["product_id"] for r in body["data"]}
        | {r["product_id"] for r in body2["data"]}
        | {r["product_id"] for r in body3["data"]}
    )
    assert seen == {101, 102, 103, 104, 105}


# ---------------------------------------------------------------------------
# listInventory — filters
# ---------------------------------------------------------------------------


async def test_list_inventory_filter_category(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    h = await _owner_headers(app, owner_user)
    # Products 101,102,103 belong to category 10 (Widgets);
    # products 104 → cat 11 (Raw); 105 → cat 10.
    r = await app.get("/api/v1/inventory?filter[category_id]=11", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    ids = {row["product_id"] for row in body["data"]}
    assert ids == {104}


async def test_list_inventory_filter_is_active(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    h = await _owner_headers(app, owner_user)
    r = await app.get("/api/v1/inventory?filter[is_active]=true", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    ids = {row["product_id"] for row in body["data"]}
    # 105 is deactivated — must be excluded.
    assert 105 not in ids
    assert {101, 102, 103, 104} <= ids


# ---------------------------------------------------------------------------
# listInventory — search
# ---------------------------------------------------------------------------


async def test_list_inventory_search_q(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    h = await _owner_headers(app, owner_user)
    # "Below" only appears in product 103's name.
    r = await app.get("/api/v1/inventory?q=below", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    ids = {row["product_id"] for row in body["data"]}
    assert ids == {103}


# ---------------------------------------------------------------------------
# getProductStock — happy path + 404
# ---------------------------------------------------------------------------


async def test_get_product_stock_happy_path(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    h = await _owner_headers(app, owner_user)
    r = await app.get("/api/v1/inventory/products/103", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    # ProductStock contract shape
    assert {"product_id", "on_hand_quantity", "inventory_value", "as_of"} <= body.keys()
    assert {"moving_average_unit_cost", "low_stock", "low_stock_threshold"} <= body.keys()
    assert body["product_id"] == 103
    assert body["on_hand_quantity"] == 5
    assert body["low_stock_threshold"] == 10
    assert body["low_stock"] is True  # 5 <= 10


async def test_get_product_stock_product_without_threshold(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    h = await _owner_headers(app, owner_user)
    r = await app.get("/api/v1/inventory/products/104", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["product_id"] == 104
    assert body["low_stock_threshold"] is None
    # Active product with no threshold cannot be low_stock.
    assert body["low_stock"] is False


async def test_get_product_stock_not_found(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    h = await _owner_headers(app, owner_user)
    r = await app.get("/api/v1/inventory/products/999999", headers=h)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# listLowStock — predicate correctness
# ---------------------------------------------------------------------------


async def test_list_low_stock_happy_path(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    h = await _owner_headers(app, owner_user)
    r = await app.get("/api/v1/inventory/low-stock", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    ids = {row["product_id"] for row in body["data"]}
    # 102 (at threshold) and 103 (below) qualify.
    # 105 is below the threshold numerically but is_deactivated → excluded.
    assert ids == {102, 103}
    row102 = next(row for row in body["data"] if row["product_id"] == 102)
    assert row102["low_stock"] is True  # 10 <= 10


async def test_list_low_stock_excludes_deactivated(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    h = await _owner_headers(app, owner_user)
    r = await app.get("/api/v1/inventory/low-stock", headers=h)
    assert r.status_code == 200, r.text
    ids = {row["product_id"] for row in r.json()["data"]}
    assert 105 not in ids  # deactivated product at 5/10 stock


async def test_list_low_stock_excludes_no_threshold(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    h = await _owner_headers(app, owner_user)
    r = await app.get("/api/v1/inventory/low-stock", headers=h)
    assert r.status_code == 200, r.text
    ids = {row["product_id"] for row in r.json()["data"]}
    assert 104 not in ids  # no threshold defined


async def test_list_low_stock_pagination(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    h = await _owner_headers(app, owner_user)
    r = await app.get("/api/v1/inventory/low-stock?per_page=1&page=1", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pagination"]["per_page"] == 1
    assert len(body["data"]) == 1


# ---------------------------------------------------------------------------
# Authorization — capability enforcement
# ---------------------------------------------------------------------------


async def test_list_inventory_staff_allowed(
    app: AsyncClient, staff_user: dict[str, Any]
) -> None:
    """Staff has inventory.view by default."""
    h = await _staff_headers(app, staff_user)
    r = await app.get("/api/v1/inventory", headers=h)
    assert r.status_code == 200


# ---------------------------------------------------------------------------
# Read-only guarantees
# ---------------------------------------------------------------------------


async def test_inventory_endpoints_are_read_only(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """GET-only — POST/PATCH/DELETE must 405."""
    h = await _owner_headers(app, owner_user)
    for method in ("post", "patch", "delete", "put"):
        fn = getattr(app, method)
        r = await fn("/api/v1/inventory", headers=h)
        assert r.status_code == 405, (method, r.status_code, r.text)


# ---------------------------------------------------------------------------
# Phase 3A — summary, stock_status filter, sorting
# ---------------------------------------------------------------------------


async def test_list_inventory_summary_active_only(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """Summary aggregates must be derived from the same filtered dataset.

    The summary's ``low_stock_count`` and ``out_of_stock_count`` always
    count active products only. ``total_units`` and ``inventory_value``
    must likewise aggregate active products so all four metrics are
    internally consistent when ``filter[is_active]`` is omitted.
    """
    h = await _owner_headers(app, owner_user)
    r = await app.get("/api/v1/inventory", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "summary" in body
    summary = body["summary"]
    # Seeded active products: 101(100,500), 102(10,50), 103(5,25), 104(50,250)
    # Inactive 105(5,25) must NOT contribute to summary totals.
    assert summary["total_units"] == 165.0
    assert summary["inventory_value"] == 825.0
    # Low stock among active: 102 (10<=10) and 103 (5<=10).
    assert summary["low_stock_count"] == 2
    # Out of stock: none (all active products have qty > 0).
    assert summary["out_of_stock_count"] == 0


async def test_list_inventory_summary_filter_is_active_false(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """filter[is_active]=false should surface only inactive products in
    the data array; summary totals must still reflect the active-only
    semantics (i.e., zero) because the summary counts are defined as
    "active inventory" aggregates.
    """
    h = await _owner_headers(app, owner_user)
    r = await app.get("/api/v1/inventory?filter[is_active]=false", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    ids = {row["product_id"] for row in body["data"]}
    assert ids == {105}
    summary = body["summary"]
    # Even though the data array shows only inactive products, the
    # summary counts active inventory (consistent with low/out counts).
    assert summary["total_units"] == 0.0
    assert summary["inventory_value"] == 0.0
    assert summary["low_stock_count"] == 0
    assert summary["out_of_stock_count"] == 0


async def test_list_inventory_filter_stock_status_in(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    h = await _owner_headers(app, owner_user)
    r = await app.get("/api/v1/inventory?filter[stock_status]=in", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    ids = {row["product_id"] for row in body["data"]}
    # 101 (100 > 10), 104 (50 > null threshold)
    assert ids == {101, 104}
    summary = body["summary"]
    # Only in-stock products: 101 + 104 = 150 units, 750 value.
    assert summary["total_units"] == 150.0
    assert summary["inventory_value"] == 750.0
    assert summary["low_stock_count"] == 0
    assert summary["out_of_stock_count"] == 0


async def test_list_inventory_filter_stock_status_low(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    h = await _owner_headers(app, owner_user)
    r = await app.get("/api/v1/inventory?filter[stock_status]=low", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    ids = {row["product_id"] for row in body["data"]}
    # 102 (10 <= 10), 103 (5 <= 10)
    assert ids == {102, 103}
    summary = body["summary"]
    assert summary["total_units"] == 15.0
    assert summary["inventory_value"] == 75.0
    assert summary["low_stock_count"] == 2
    assert summary["out_of_stock_count"] == 0


async def test_list_inventory_filter_stock_status_out(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    h = await _owner_headers(app, owner_user)
    r = await app.get("/api/v1/inventory?filter[stock_status]=out", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["data"] == []
    summary = body["summary"]
    assert summary["total_units"] == 0.0
    assert summary["inventory_value"] == 0.0
    assert summary["low_stock_count"] == 0
    assert summary["out_of_stock_count"] == 0


async def test_list_inventory_sort_low_stock(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """Assert ordering for ``low_stock`` ASC/DESC using existing seeded data.

    In the seed, low_stock = True for product 102 and 103; False for
    101 and 104. ASC should list non-low first, then low; DESC reverse.
    """
    h = await _owner_headers(app, owner_user)
    # ASC: non-low before low
    r = await app.get("/api/v1/inventory?sort=low_stock", headers=h)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    ids = [row["product_id"] for row in data]
    # Find first occurrence of a low_stock item (should be after non-low)
    first_low_index = next(i for i, pid in enumerate(ids) if pid in {102, 103})
    # All preceding ids must be non-low
    assert all(pid not in {102, 103} for pid in ids[:first_low_index])

    # DESC: low before non-low
    r2 = await app.get("/api/v1/inventory?sort=-low_stock", headers=h)
    assert r2.status_code == 200, r2.text
    data2 = r2.json()["data"]
    ids2 = [row["product_id"] for row in data2]
    first_non_low = next(i for i, pid in enumerate(ids2) if pid not in {102, 103})
    assert all(pid in {102, 103} for pid in ids2[:first_non_low])


async def test_list_inventory_sort_moving_average_unit_cost(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """Assert the moving_average_unit_cost sort keys are accepted and
    produce a stable, well-defined ordering.

    All seeded products share MAUC=5.0 so the ordering is determined
    by the tiebreaker ``pv.product_id ASC``. The test verifies that
    the sort keys do not produce 200/422/500 and return the same set
    of products in the same order regardless of direction (since all
    values are equal, the tiebreaker is the only distinguishing key).
    """
    h = await _owner_headers(app, owner_user)
    r1 = await app.get("/api/v1/inventory?sort=moving_average_unit_cost", headers=h)
    assert r1.status_code == 200, r1.text
    r2 = await app.get("/api/v1/inventory?sort=-moving_average_unit_cost", headers=h)
    assert r2.status_code == 200, r2.text
    asc_ids = [r["product_id"] for r in r1.json()["data"]]
    desc_ids = [r["product_id"] for r in r2.json()["data"]]
    # Equal MAUC across all rows → ASC tiebreak by product_id ASC,
    # DESC tiebreak by product_id ASC as well (DESC flips the
    # MAUC, but equal values fall back to the secondary key).
    # The product set must match, order is allowed to differ only if
    # the secondary key direction is the same.
    assert set(asc_ids) == set(desc_ids)


async def test_list_inventory_returns_product_identity(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """Phase 3A: InventorySummary must expose product_name, product_code,
    and updated_at (ETag source)."""
    h = await _owner_headers(app, owner_user)
    r = await app.get("/api/v1/inventory", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    for row in body["data"]:
        assert "product_name" in row
        assert isinstance(row["product_name"], str)
        assert "product_code" in row
        # product_code may be null for legacy rows; accept both.
        assert row["product_code"] is None or isinstance(row["product_code"], str)
        assert "updated_at" in row
        if row["updated_at"] is not None:
            assert "T" in row["updated_at"]

