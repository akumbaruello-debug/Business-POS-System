"""B.7 — Products sub-resource tests.

Covers:
  GET /products/{id}/price-history  (product.view, pagination, q, from, to)
  GET /products/{id}/stock-movements (inventory.view, pagination, q, from, to)
  GET /products/{id}/valuation       (inventory.view, contract shape)

All three are READ-ONLY endpoints. No idempotency, no If-Match, no audit.
"""

from __future__ import annotations

import uuid
from typing import Any

from httpx import AsyncClient
from sqlalchemy import text

from app.db import get_session_factory
from app.errors.codes import ErrorCode


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


def _valid_product(name: str | None = None, **kw: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name or f"prod_{uuid.uuid4().hex[:8]}",
        "is_active": True,
    }
    body.update(kw)
    return body


async def _create_product(app: AsyncClient, headers: dict[str, str]) -> int:
    """Create a product and return its id."""
    r = await app.post(
        "/api/v1/products",
        json=_valid_product(),
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
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
) -> int:
    """Insert a stock_movements row directly via SQL (read-only endpoint tests)."""
    factory = get_session_factory()
    async with factory() as session:
        row = (
            (
                await session.execute(
                    text("""
                        INSERT INTO stock_movements (
                            product_id, movement_date, trigger, quantity,
                            unit_cost_at_movement, total_cost,
                            reference_type, reference_id, reference_line_id,
                            reason, created_by
                        ) VALUES (
                            :pid, NOW(), :trigger, :qty,
                            :unit_cost, :total_cost,
                            NULL, NULL, NULL,
                            NULL, 1
                        ) RETURNING id
                    """),
                    {
                        "pid": product_id,
                        "trigger": trigger,
                        "qty": quantity,
                        "unit_cost": unit_cost,
                        "total_cost": total_cost
                        if total_cost is not None
                        else (quantity * unit_cost),
                    },
                )
            )
            .mappings()
            .first()
        )
        await session.commit()
        assert row is not None
        return int(row["id"])


async def _insert_price_history(
    product_id: int,
    *,
    purchase_price: float = 10.0,
    selling_price: float = 20.0,
) -> int:
    """Insert a product_price_history row directly via SQL."""
    factory = get_session_factory()
    async with factory() as session:
        row = (
            (
                await session.execute(
                    text("""
                        INSERT INTO product_price_history (
                            product_id, purchase_price, selling_price,
                            effective_at, changed_by
                        ) VALUES (
                            :pid, :pp, :sp, NOW(), 1
                        ) RETURNING id
                    """),
                    {
                        "pid": product_id,
                        "pp": purchase_price,
                        "sp": selling_price,
                    },
                )
            )
            .mappings()
            .first()
        )
        await session.commit()
        assert row is not None
        return int(row["id"])


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


class TestB7Authorization:
    async def test_price_history_without_auth_returns_401(self, app: AsyncClient) -> None:
        r = await app.get("/api/v1/products/1/price-history")
        assert r.status_code == 401

    async def test_stock_movements_without_auth_returns_401(self, app: AsyncClient) -> None:
        r = await app.get("/api/v1/products/1/stock-movements")
        assert r.status_code == 401

    async def test_valuation_without_auth_returns_401(self, app: AsyncClient) -> None:
        r = await app.get("/api/v1/products/1/valuation")
        assert r.status_code == 401

    async def test_price_history_requires_product_view(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        """Staff has product.view by default → 200 (not 403)."""
        headers = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/products/1/price-history", headers=headers)
        # staff has product.view, so no 403 on cap. Product 1 doesn't exist
        # → 404, but NOT 403.
        assert r.status_code != 403

    async def test_stock_movements_requires_inventory_view(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        """Staff has inventory.view by default → not 403."""
        headers = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/products/1/stock-movements", headers=headers)
        assert r.status_code != 403

    async def test_valuation_requires_inventory_view(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        """Staff has inventory.view by default → not 403."""
        headers = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/products/1/valuation", headers=headers)
        assert r.status_code != 403


# ---------------------------------------------------------------------------
# Price History
# ---------------------------------------------------------------------------


class TestProductPriceHistory:
    async def test_get_empty_history(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        pid = await _create_product(app, headers)
        r = await app.get(f"/api/v1/products/{pid}/price-history", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["data"] == []
        assert body["pagination"]["total"] == 0

    async def test_get_history_after_patch_price_change(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """PATCH with price change writes a price_history row (existing B.6
        behavior); B.7 endpoint must surface it."""
        headers = await _owner_headers(app, owner_user)
        pid = await _create_product(app, headers)

        # Change price via PATCH (writes a price_history row via B.6 service).
        etag = await app.get(f"/api/v1/products/{pid}", headers=headers)
        updated_at = etag.json()["updated_at"]
        r2 = await app.patch(
            f"/api/v1/products/{pid}",
            json={"purchase_price": 1000, "selling_price": 2000},
            headers={**headers, "If-Match": f'"{updated_at}"'},
        )
        assert r2.status_code == 200

        r3 = await app.get(f"/api/v1/products/{pid}/price-history", headers=headers)
        assert r3.status_code == 200
        body = r3.json()
        assert len(body["data"]) == 1
        ph = body["data"][0]
        assert ph["product_id"] == pid
        assert float(ph["purchase_price"]) == 1000.0
        assert float(ph["selling_price"]) == 2000.0
        assert "effective_at" in ph
        assert ph["changed_by"] == owner_user["user_id"]

    async def test_get_history_newest_first(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Multiple price history rows must be ordered effective_at DESC."""
        headers = await _owner_headers(app, owner_user)
        pid = await _create_product(app, headers)

        await _insert_price_history(pid, purchase_price=10, selling_price=20)
        await _insert_price_history(pid, purchase_price=15, selling_price=25)
        await _insert_price_history(pid, purchase_price=20, selling_price=30)

        r = await app.get(f"/api/v1/products/{pid}/price-history", headers=headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert len(data) == 3
        # newest first by effective_at
        assert float(data[0]["purchase_price"]) == 20.0
        assert float(data[2]["purchase_price"]) == 10.0

    async def test_price_history_with_from_to_filters(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        pid = await _create_product(app, headers)
        await _insert_price_history(pid, purchase_price=10, selling_price=20)
        await _insert_price_history(pid, purchase_price=15, selling_price=25)

        # Filter from epoch — should still return all rows.
        r = await app.get(
            f"/api/v1/products/{pid}/price-history?from=1970-01-01T00:00:00Z",
            headers=headers,
        )
        assert r.status_code == 200

    async def test_price_history_pagination(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        pid = await _create_product(app, headers)
        for i in range(5):
            await _insert_price_history(pid, purchase_price=float(i), selling_price=float(i + 1))

        r = await app.get(
            f"/api/v1/products/{pid}/price-history?per_page=2&page=2",
            headers=headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["data"]) == 2
        assert body["pagination"]["page"] == 2
        assert body["pagination"]["per_page"] == 2
        assert body["pagination"]["total"] == 5
        assert body["pagination"]["total_pages"] == 3

    async def test_price_history_nonexistent_product(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/products/999999/price-history", headers=headers)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == ErrorCode.NOT_FOUND.value

    async def test_price_history_staff_can_view(
        self, app: AsyncClient, owner_user: dict[str, Any], staff_user: dict[str, Any]
    ) -> None:
        owner = await _owner_headers(app, owner_user)
        pid = await _create_product(app, owner)
        await _insert_price_history(pid, purchase_price=10, selling_price=20)
        staff = await _staff_headers(app, staff_user)
        r = await app.get(f"/api/v1/products/{pid}/price-history", headers=staff)
        assert r.status_code == 200
        assert len(r.json()["data"]) == 1


# ---------------------------------------------------------------------------
# Stock Movements
# ---------------------------------------------------------------------------


class TestProductStockMovements:
    async def test_get_empty_movements(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        pid = await _create_product(app, headers)
        r = await app.get(f"/api/v1/products/{pid}/stock-movements", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["data"] == []
        assert body["pagination"]["total"] == 0

    async def test_get_movements_after_insert(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        pid = await _create_product(app, headers)
        await _insert_stock_movement(pid, trigger="opening_balance", quantity=100, unit_cost=0.50)

        r = await app.get(f"/api/v1/products/{pid}/stock-movements", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert len(body["data"]) == 1
        sm = body["data"][0]
        assert sm["product_id"] == pid
        assert float(sm["quantity"]) == 100.0
        assert float(sm["unit_cost_at_movement"]) == 0.50
        assert float(sm["total_cost"]) == 50.0
        assert sm["trigger"] == "opening_balance"
        assert sm["reference_type"] is None
        assert sm["created_by"] == owner_user["user_id"]

    async def test_get_movements_newest_first(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        pid = await _create_product(app, headers)
        await _insert_stock_movement(pid, trigger="opening_balance", quantity=100)
        await _insert_stock_movement(pid, trigger="sale", quantity=-10, unit_cost=0.50)

        r = await app.get(f"/api/v1/products/{pid}/stock-movements", headers=headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert len(data) == 2
        # newest first
        assert float(data[0]["quantity"]) == -10.0
        assert float(data[1]["quantity"]) == 100.0

    async def test_movements_pagination(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        pid = await _create_product(app, headers)
        for i in range(5):
            await _insert_stock_movement(pid, trigger="opening_balance", quantity=float(i + 1))

        r = await app.get(
            f"/api/v1/products/{pid}/stock-movements?per_page=2&page=1",
            headers=headers,
        )
        assert r.status_code == 200
        body = r.json()
        assert len(body["data"]) == 2
        assert body["pagination"]["total"] == 5
        assert body["pagination"]["total_pages"] == 3

    async def test_stock_movements_nonexistent_product(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/products/999999/stock-movements", headers=headers)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == ErrorCode.NOT_FOUND.value


# ---------------------------------------------------------------------------
# Valuation
# ---------------------------------------------------------------------------


class TestProductValuation:
    async def test_valuation_zero_stock(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        """New product → no movements → on_hand=0, inventory_value=0, mauc=null."""
        headers = await _owner_headers(app, owner_user)
        pid = await _create_product(app, headers)
        r = await app.get(f"/api/v1/products/{pid}/valuation", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["product_id"] == pid
        assert float(body["on_hand_quantity"]) == 0
        assert float(body["inventory_value"]) == 0
        # moving_average_unit_cost is null when on_hand == 0
        assert body["moving_average_unit_cost"] is None
        assert "as_of" in body

    async def test_valuation_with_stock(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        """With a positive receipt: on_hand=quantity, inventory_value=total_cost,
        moving_average_unit_cost = inventory_value / on_hand."""
        headers = await _owner_headers(app, owner_user)
        pid = await _create_product(app, headers)
        await _insert_stock_movement(
            pid, trigger="opening_balance", quantity=100, unit_cost=0.50, total_cost=50.0
        )

        r = await app.get(f"/api/v1/products/{pid}/valuation", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["product_id"] == pid
        assert float(body["on_hand_quantity"]) == 100
        assert float(body["inventory_value"]) == 50.0
        assert float(body["moving_average_unit_cost"]) == 0.5

    async def test_valuation_with_mixed_movimientos(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Receipt + sale: on_hand = net qty, inventory_value = net cost."""
        headers = await _owner_headers(app, owner_user)
        pid = await _create_product(app, headers)
        # Receipt: 100 @ 0.50 → total_cost = +50
        await _insert_stock_movement(
            pid, trigger="purchase_receipt", quantity=100, unit_cost=0.50, total_cost=50.0
        )
        # Sale: -30 @ 0.50 → total_cost = -15
        await _insert_stock_movement(
            pid, trigger="sale", quantity=-30, unit_cost=0.50, total_cost=-15.0
        )

        r = await app.get(f"/api/v1/products/{pid}/valuation", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert float(body["on_hand_quantity"]) == 70
        assert float(body["inventory_value"]) == 35.0

    async def test_valuation_nonexistent_product(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/products/999999/valuation", headers=headers)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == ErrorCode.NOT_FOUND.value


# ---------------------------------------------------------------------------
# B.7 vs locked artifacts — no accidental writes to stock_movement / price_history
# ---------------------------------------------------------------------------


class TestB7ReadOnlyContract:
    async def test_price_history_endpoint_does_not_write(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """GET must not mutate any table (no audit_log insert, no price_history)."""
        headers = await _owner_headers(app, owner_user)
        pid = await _create_product(app, headers)
        before = await _count_audit_log(pid)
        await app.get(f"/api/v1/products/{pid}/price-history", headers=headers)
        await app.get(f"/api/v1/products/{pid}/stock-movements", headers=headers)
        await app.get(f"/api/v1/products/{pid}/valuation", headers=headers)
        after = await _count_audit_log(pid)
        assert before == after, "B.7 read endpoints must not write audit_log"

    async def test_stock_movements_endpoint_does_not_write(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """GET /products/{id}/stock-movements must not insert movement rows."""
        headers = await _owner_headers(app, owner_user)
        pid = await _create_product(app, headers)
        before = await _count_stock_movements(pid)
        await app.get(f"/api/v1/products/{pid}/stock-movements", headers=headers)
        after = await _count_stock_movements(pid)
        assert before == after, "B.7 read endpoints must not write stock_movements"


async def _count_audit_log(product_id: int) -> int:
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM audit_log WHERE entity_type = 'product' AND entity_id = :pid"
                ),
                {"pid": product_id},
            )
        ).scalar()
        return int(row or 0)


async def _count_stock_movements(product_id: int) -> int:
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                text("SELECT COUNT(*) FROM stock_movements WHERE product_id = :pid"),
                {"pid": product_id},
            )
        ).scalar()
        return int(row or 0)
