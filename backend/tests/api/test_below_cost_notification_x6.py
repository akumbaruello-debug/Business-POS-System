"""X.6 — Below-cost sale notification tests.

Authoritative spec: Backend-Architecture-V1.0.md §21.2 line 2128:
  below_cost_sale: emitted by POST /sales/{id}/post service after computing
  snapshot, if unit_cost_snapshot > unit_price, INSERT a notification with
  related_entity_type='sale', related_entity_id=sale_id.

Test matrix:
  1. below-cost sale → notification inserted with category='below_cost',
     severity='info', user_id=posting user, reference_type='sale'.
  2. at-cost sale → no notification.
  3. above-cost sale → no notification.
  4. notification body contains warning message.
  5. no unintended duplicate notification when operation replayed.
  6. notification read via /notifications maps to type='below_cost_sale'.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.db import get_session_factory


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


async def _seed_stock_product(
    product_id: int,
    name: str,
    stock: float = 10.0,
    unit_cost: float = 50.0,
    is_active: bool = True,
) -> None:
    """Seed a product with opening-balance stock."""
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO products (id, name, selling_price, purchase_price, "
                "low_stock_threshold, is_active, is_sellable, is_purchasable, "
                "is_producible, allow_negative_stock, created_by, updated_by) "
                "VALUES (:id, :name, 100, :cost, NULL, :active, TRUE, TRUE, FALSE, FALSE, 1, 1) "
                "ON CONFLICT (id) DO UPDATE SET "
                "  selling_price = EXCLUDED.selling_price, "
                "  is_active = TRUE"
            ),
            {"id": product_id, "name": name, "cost": unit_cost, "active": is_active},
        )
        if stock > 0:
            await session.execute(
                text(
                    "INSERT INTO stock_movements "
                    "(product_id, trigger, quantity, unit_cost_at_movement, "
                    "total_cost, reference_type, reference_id, created_by) "
                    "VALUES (:pid, 'opening_balance', :qty, :cost, :tc, 'seed', 0, 1)"
                ),
                {"pid": product_id, "qty": stock, "cost": unit_cost, "tc": stock * unit_cost},
            )
        await session.commit()


async def _create_draft_sale_with_line(
    app: AsyncClient,
    headers: dict[str, str],
    product_id: int,
    quantity: str = "1.000",
    unit_price: str = "50.00",
) -> int:
    """Create a draft sale, add one line, return sale_id."""
    r = await app.post("/api/v1/sales", headers={**headers, "Idempotency-Key": _idem()}, json={})
    assert r.status_code == 201, r.text
    sale_id = r.json()["id"]

    r_line = await app.post(
        f"/api/v1/sales/{sale_id}/lines",
        headers={**headers, "Idempotency-Key": _idem()},
        json={"product_id": product_id, "quantity": quantity, "unit_price": unit_price},
    )
    assert r_line.status_code == 201, r_line.text
    return sale_id


async def _post_sale(
    app: AsyncClient,
    headers: dict[str, str],
    sale_id: int,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    key = idempotency_key or _idem()
    r = await app.post(
        f"/api/v1/sales/{sale_id}/post",
        headers={**headers, "Idempotency-Key": key},
        json={},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _count_notifications_for_sale(sale_id: int, user_id: int) -> int:
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM notifications "
                    "WHERE reference_type = 'sale' AND reference_id = :sid AND user_id = :uid"
                ),
                {"sid": sale_id, "uid": user_id},
            )
        ).scalar()
        return int(row or 0)


async def _get_notification_for_sale(sale_id: int, user_id: int) -> dict[str, Any] | None:
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT id, category, severity, title, body, reference_type, reference_id, user_id "
                    "FROM notifications "
                    "WHERE reference_type = 'sale' AND reference_id = :sid AND user_id = :uid "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"sid": sale_id, "uid": user_id},
            )
        ).mappings().first()
        return dict(row) if row else None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBelowCostNotification:
    @pytest.fixture(autouse=True)
    async def _ensure_product(self, app: AsyncClient) -> None:
        await _seed_stock_product(product_id=1, name="Test Product", stock=10, unit_cost=50.0)

    @pytest.mark.asyncio
    async def test_below_cost_triggers_notification(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Case 1: below-cost sale inserts a notification."""
        h = await _owner_headers(app, owner_user)
        sale_id = await _create_draft_sale_with_line(app, h, product_id=1, unit_price="0.01")
        await _post_sale(app, h, sale_id)

        count = await _count_notifications_for_sale(sale_id, owner_user["user_id"])
        assert count == 1

        notif = await _get_notification_for_sale(sale_id, owner_user["user_id"])
        assert notif is not None
        assert notif["category"] == "below_cost"
        assert notif["severity"] == "info"
        assert notif["title"] == "Below-cost sale posted"
        assert "1 line sold below unit cost snapshot" in notif["body"]
        assert notif["reference_type"] == "sale"
        assert notif["reference_id"] == sale_id
        assert notif["user_id"] == owner_user["user_id"]

    @pytest.mark.asyncio
    async def test_at_cost_no_notification(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Case 2: at-cost sale -> no notification."""
        h = await _owner_headers(app, owner_user)
        sale_id = await _create_draft_sale_with_line(app, h, product_id=1, unit_price="50.00")
        await _post_sale(app, h, sale_id)

        count = await _count_notifications_for_sale(sale_id, owner_user["user_id"])
        assert count == 0

    @pytest.mark.asyncio
    async def test_above_cost_no_notification(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Case 3: above-cost sale -> no notification."""
        h = await _owner_headers(app, owner_user)
        sale_id = await _create_draft_sale_with_line(app, h, product_id=1, unit_price="75.00")
        await _post_sale(app, h, sale_id)

        count = await _count_notifications_for_sale(sale_id, owner_user["user_id"])
        assert count == 0

    @pytest.mark.asyncio
    async def test_notification_body_contains_warning_message(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Case 5: notification body matches warning message."""
        h = await _owner_headers(app, owner_user)
        sale_id = await _create_draft_sale_with_line(app, h, product_id=1, unit_price="0.01")
        resp = await _post_sale(app, h, sale_id)

        warning_msg = resp["warning"]["message"]
        notif = await _get_notification_for_sale(sale_id, owner_user["user_id"])
        assert notif is not None
        assert notif["body"] == warning_msg

    @pytest.mark.asyncio
    async def test_idempotent_replay_no_duplicate_notification(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Case 6: idempotent replay of POST does not create duplicate notification."""
        h = await _owner_headers(app, owner_user)
        sale_id = await _create_draft_sale_with_line(app, h, product_id=1, unit_price="0.01")

        key = _idem()
        resp1 = await _post_sale(app, h, sale_id, idempotency_key=key)
        assert resp1["warning"] is not None

        count1 = await _count_notifications_for_sale(sale_id, owner_user["user_id"])
        assert count1 == 1

        # Replay with same key
        resp2 = await _post_sale(app, h, sale_id, idempotency_key=key)
        assert resp2["warning"] is not None
        assert resp2.get("id") == resp1.get("id")

        count2 = await _count_notifications_for_sale(sale_id, owner_user["user_id"])
        assert count2 == 1  # no duplicate

    @pytest.mark.asyncio
    async def test_notification_read_api_maps_to_below_cost_sale(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Case 7: notification appears in GET /notifications with type=below_cost_sale."""
        h = await _owner_headers(app, owner_user)
        sale_id = await _create_draft_sale_with_line(app, h, product_id=1, unit_price="0.01")
        await _post_sale(app, h, sale_id)

        r = await app.get("/api/v1/notifications", headers=h)
        assert r.status_code == 200
        data = r.json()["data"]
        # Find the notification for this sale
        matches = [d for d in data if d["related_entity_id"] == sale_id and d["related_entity_type"] == "sale"]
        assert len(matches) == 1
        notif = matches[0]
        assert notif["type"] == "below_cost_sale"
        assert "Below-cost" in notif["message"]
        assert notif["is_read"] is False
        assert notif["related_entity_id"] == sale_id
        assert notif["related_entity_type"] == "sale"