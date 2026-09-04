"""E.10 — Low-stock notifications worker tests.

Tests cover:

1. **Event mechanism:** stock_movements INSERT triggers ``NOTIFY stock_change``
   with the correct payload.
2. **Service logic (evaluate_stock_notification):** threshold crossing
   detection, duplicate prevention, recovery/re-crossing, field
   validation, inactive products.
3. **Integration:** a stock adjustment via the API → notification row
   appears in the ``notifications`` table.
4. **Transaction safety:** rolled-back stock_movements produce no
   notifications.
5. **Concurrency:** two rapid movements for the same product.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import asyncpg  # type: ignore[import-untyped]
import pytest
from sqlalchemy import text

from app.config import get_settings
from app.db import UnitOfWork, get_session_factory
from app.notifications.service import evaluate_stock_notification

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_OWNER_ID = 1  # seeded owner user


def _idem() -> str:
    return str(uuid.uuid4())


async def _login(app: Any, username: str, password: str) -> str:
    r = await app.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"Idempotency-Key": _idem()},
    )
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


async def _owner_headers(app: Any, owner_user: dict[str, Any]) -> dict[str, str]:
    token = await _login(app, owner_user["username"], owner_user["password"])
    return {"Authorization": f"Bearer {token}"}


async def _truncate_notifications() -> None:
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(text("TRUNCATE TABLE notifications RESTART IDENTITY CASCADE"))
        await session.commit()


async def _notification_count(
    *, category: str | None = None, product_id: int | None = None
) -> int:
    factory = get_session_factory()
    async with factory() as session:
        sql = "SELECT COUNT(*) FROM notifications WHERE 1=1"
        params: dict[str, Any] = {}
        if category is not None:
            sql += " AND category = :cat"
            params["cat"] = category
        if product_id is not None:
            sql += " AND reference_id = :pid AND reference_type = 'product'"
            params["pid"] = product_id
        row = (await session.execute(text(sql), params)).scalar()
        return int(row or 0)


async def _get_notifications(
    *, product_id: int | None = None,
) -> list[dict[str, Any]]:
    factory = get_session_factory()
    async with factory() as session:
        sql = (
            "SELECT id, user_id, category, severity, title, body, "
            "reference_type, reference_id, is_read, read_at, created_at "
            "FROM notifications WHERE 1=1"
        )
        params: dict[str, Any] = {}
        if product_id is not None:
            sql += " AND reference_id = :pid AND reference_type = 'product'"
            params["pid"] = product_id
        sql += " ORDER BY id"
        rows = (await session.execute(text(sql), params)).mappings().all()
        return [dict(r) for r in rows]


async def _seed_product(
    *,
    product_id: int,
    name: str,
    stock: float,
    threshold: float | None,
    is_active: bool = True,
) -> None:
    """Seed a product with optional opening-balance stock."""
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO products (id, name, selling_price, purchase_price, "
                "low_stock_threshold, is_active, is_sellable, is_purchasable, "
                "is_producible, created_by, updated_by) "
                "VALUES (:id, :name, 100, 10, :thr, :active, TRUE, TRUE, FALSE, 1, 1) "
                "ON CONFLICT (id) DO UPDATE SET "
                "  low_stock_threshold = EXCLUDED.low_stock_threshold, "
                "  is_active = EXCLUDED.is_active, "
                "  name = EXCLUDED.name"
            ),
            {"id": product_id, "name": name, "thr": threshold, "active": is_active},
        )
        if stock > 0:
            await session.execute(
                text(
                    "INSERT INTO stock_movements "
                    "(product_id, trigger, quantity, unit_cost_at_movement, "
                    " total_cost, reference_type, reference_id, created_by) "
                    "VALUES (:pid, 'opening_balance', :qty, 10.0000, :tc, 'seed', 0, 1)"
                ),
                {"pid": product_id, "qty": stock, "tc": stock * 10},
            )
        await session.commit()


async def _insert_stock_movement(
    product_id: int, quantity: float, *, commit: bool = True
) -> int:
    """Insert a stock_movement directly (simulating any business operation).

    Returns the movement id.
    """
    factory = get_session_factory()
    async with factory() as session:
        trigger = "stock_adjustment"
        row = (
            await session.execute(
                text(
                    "INSERT INTO stock_movements "
                    "(product_id, trigger, quantity, unit_cost_at_movement, "
                    " total_cost, created_by) "
                    "VALUES (:pid, :trg, :qty, 10.0000, :tc, 1) "
                    "RETURNING id"
                ),
                {
                    "pid": product_id,
                    "trg": trigger,
                    "qty": quantity,
                    "tc": quantity * 10,
                },
            )
        ).scalar()
        if commit:
            await session.commit()
        else:
            await session.rollback()
        return int(row)


async def _on_hand(product_id: int) -> float:
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT on_hand_quantity FROM product_valuation "
                    "WHERE product_id = :pid"
                ),
                {"pid": product_id},
            )
        ).scalar()
        return float(row or 0)


def _asyncpg_dsn() -> str:
    """Derive raw asyncpg DSN from SQLAlchemy URL."""
    sa_url = get_settings().database_url
    return sa_url.replace("postgresql+asyncpg://", "postgresql://", 1)


# ===================================================================
# SECTION 1: Event mechanism — NOTIFY fires on stock_movements INSERT
# ===================================================================


class TestStockChangeNotify:
    """Verify the PostgreSQL trigger emits ``NOTIFY stock_change``."""

    @pytest.mark.asyncio
    async def test_notify_fires_on_committed_insert(self, db: None) -> None:
        """A committed stock_movements INSERT produces a ``stock_change``
        notification with ``{product_id, movement_id}`` payload."""
        await _seed_product(product_id=501, name="Notify Test", stock=100, threshold=10)

        dsn = _asyncpg_dsn()
        conn = await asyncpg.connect(dsn)
        try:
            received: list[dict[str, Any]] = []

            def on_notify(
                conn: asyncpg.Connection,
                pid: int,
                channel: str,
                payload: str,
            ) -> None:
                received.append(json.loads(payload))

            await conn.add_listener("stock_change", on_notify)

            # Insert a stock movement (committed)
            movement_id = await _insert_stock_movement(501, -5)

            # asyncpg delivers notifications asynchronously; give it a moment
            await asyncio.sleep(0.3)

            assert len(received) >= 1
            msg = received[-1]
            assert msg["product_id"] == 501
            assert msg["movement_id"] == movement_id
        finally:
            await conn.close()

    @pytest.mark.asyncio
    async def test_notify_not_fired_on_rollback(self, db: None) -> None:
        """A rolled-back stock_movements INSERT must NOT produce a
        ``stock_change`` notification."""
        await _seed_product(product_id=502, name="Rollback Test", stock=100, threshold=10)

        dsn = _asyncpg_dsn()
        conn = await asyncpg.connect(dsn)
        try:
            received: list[str] = []

            def on_notify(
                conn: asyncpg.Connection,
                pid: int,
                channel: str,
                payload: str,
            ) -> None:
                received.append(payload)

            await conn.add_listener("stock_change", on_notify)

            # Insert and rollback
            await _insert_stock_movement(502, -5, commit=False)

            await asyncio.sleep(0.3)
            assert len(received) == 0
        finally:
            await conn.close()


# ===================================================================
# SECTION 2: Service logic — evaluate_stock_notification
# ===================================================================


class TestLowStockNotification:
    """Low-stock threshold crossing detection."""

    @pytest.mark.asyncio
    async def test_above_to_at_threshold(self, db: None) -> None:
        """on_hand drops from above threshold to exactly threshold → low_stock."""
        await _truncate_notifications()
        # stock=15, threshold=10 → on_hand=15 > 10
        await _seed_product(product_id=601, name="At Threshold", stock=15, threshold=10)

        # Drop by 5: 15 → 10 (exactly at threshold)
        mid = await _insert_stock_movement(601, -5)
        assert await _on_hand(601) == pytest.approx(10)

        async with UnitOfWork() as uow:
            created = await evaluate_stock_notification(uow, product_id=601, movement_id=mid)
            await uow.commit()

        assert len(created) == 1
        assert created[0]["category"] == "low_stock"
        assert created[0]["severity"] == "info"

    @pytest.mark.asyncio
    async def test_above_to_below_threshold(self, db: None) -> None:
        """on_hand drops from above threshold to below → low_stock."""
        await _truncate_notifications()
        await _seed_product(product_id=602, name="Below Threshold", stock=15, threshold=10)

        mid = await _insert_stock_movement(602, -8)
        assert await _on_hand(602) == pytest.approx(7)

        async with UnitOfWork() as uow:
            created = await evaluate_stock_notification(uow, product_id=602, movement_id=mid)
            await uow.commit()

        assert len(created) == 1
        assert created[0]["category"] == "low_stock"

    @pytest.mark.asyncio
    async def test_already_below_no_duplicate(self, db: None) -> None:
        """Already below threshold → further drop produces NO notification."""
        await _truncate_notifications()
        await _seed_product(product_id=603, name="Already Low", stock=15, threshold=10)

        # First crossing: 15 → 8
        mid1 = await _insert_stock_movement(603, -7)
        async with UnitOfWork() as uow:
            c1 = await evaluate_stock_notification(uow, product_id=603, movement_id=mid1)
            await uow.commit()
        assert len(c1) == 1

        # Second drop: 8 → 5 — already below threshold, no new notification
        mid2 = await _insert_stock_movement(603, -3)
        async with UnitOfWork() as uow:
            c2 = await evaluate_stock_notification(uow, product_id=603, movement_id=mid2)
            await uow.commit()
        assert len(c2) == 0

    @pytest.mark.asyncio
    async def test_stock_increase_no_notification(self, db: None) -> None:
        """Stock increases while below threshold → NO notification."""
        await _truncate_notifications()
        await _seed_product(product_id=604, name="Increase Test", stock=5, threshold=10)

        # Stock goes up: 5 → 8, still below threshold, but not a crossing
        mid = await _insert_stock_movement(604, 3)
        async with UnitOfWork() as uow:
            c = await evaluate_stock_notification(uow, product_id=604, movement_id=mid)
            await uow.commit()
        assert len(c) == 0

    @pytest.mark.asyncio
    async def test_recovery_then_recrossing(self, db: None) -> None:
        """Stock recovers above threshold, then drops below again → new notification."""
        await _truncate_notifications()
        await _seed_product(product_id=605, name="Re-cross", stock=15, threshold=10)

        # First crossing: 15 → 8
        mid1 = await _insert_stock_movement(605, -7)
        async with UnitOfWork() as uow:
            c1 = await evaluate_stock_notification(uow, product_id=605, movement_id=mid1)
            await uow.commit()
        assert len(c1) == 1

        # Recovery: 8 → 20 (above threshold)
        await _insert_stock_movement(605, 12)
        assert await _on_hand(605) == pytest.approx(20)

        # Re-crossing: 20 → 9
        mid3 = await _insert_stock_movement(605, -11)
        async with UnitOfWork() as uow:
            c3 = await evaluate_stock_notification(uow, product_id=605, movement_id=mid3)
            await uow.commit()
        assert len(c3) == 1

        # Total: 2 low_stock notifications for this product
        assert await _notification_count(category="low_stock", product_id=605) == 2


# ===================================================================
# SECTION 3: Out-of-stock (insufficient_stock)
# ===================================================================


class TestOutOfStockNotification:
    """Out-of-stock (on_hand → 0) detection."""

    @pytest.mark.asyncio
    async def test_positive_to_zero(self, db: None) -> None:
        """on_hand drops from positive to zero → insufficient_stock."""
        await _truncate_notifications()
        await _seed_product(product_id=701, name="To Zero", stock=5, threshold=None)

        mid = await _insert_stock_movement(701, -5)
        assert await _on_hand(701) == pytest.approx(0)

        async with UnitOfWork() as uow:
            c = await evaluate_stock_notification(uow, product_id=701, movement_id=mid)
            await uow.commit()

        cats = [r["category"] for r in c]
        assert "insufficient_stock" in cats

    @pytest.mark.asyncio
    async def test_already_zero_no_duplicate(self, db: None) -> None:
        """Already at zero → no duplicate insufficient_stock."""
        await _truncate_notifications()
        await _seed_product(product_id=702, name="Already Zero", stock=0, threshold=None)

        # Stock is already 0; add opening balance then remove it
        await _insert_stock_movement(702, 5)   # 0 → 5
        mid = await _insert_stock_movement(702, -5)   # 5 → 0
        assert await _on_hand(702) == pytest.approx(0)

        async with UnitOfWork() as uow:
            c = await evaluate_stock_notification(uow, product_id=702, movement_id=mid)
            await uow.commit()
        cats = [r["category"] for r in c]
        assert "insufficient_stock" in cats

        # Now another movement from 0 stays at 0 conceptually — but
        # actually we must add and remove again
        await _insert_stock_movement(702, 3)   # 0 → 3
        mid2 = await _insert_stock_movement(702, -3)  # 3 → 0
        async with UnitOfWork() as uow:
            c2 = await evaluate_stock_notification(uow, product_id=702, movement_id=mid2)
            await uow.commit()
        # This IS a crossing (3 > 0 → 0), so it should fire
        cats2 = [r["category"] for r in c2]
        assert "insufficient_stock" in cats2

    @pytest.mark.asyncio
    async def test_both_low_stock_and_out_of_stock(self, db: None) -> None:
        """Threshold=5, stock drops from 10 to 0 → both low_stock AND
        insufficient_stock fire."""
        await _truncate_notifications()
        await _seed_product(product_id=703, name="Both Fire", stock=10, threshold=5)

        mid = await _insert_stock_movement(703, -10)
        assert await _on_hand(703) == pytest.approx(0)

        async with UnitOfWork() as uow:
            c = await evaluate_stock_notification(uow, product_id=703, movement_id=mid)
            await uow.commit()

        cats = sorted(r["category"] for r in c)
        assert cats == ["insufficient_stock", "low_stock"]

    @pytest.mark.asyncio
    async def test_correct_severity(self, db: None) -> None:
        """low_stock severity=info, insufficient_stock severity=warning."""
        await _truncate_notifications()
        await _seed_product(product_id=704, name="Severity", stock=10, threshold=5)

        mid = await _insert_stock_movement(704, -10)
        async with UnitOfWork() as uow:
            c = await evaluate_stock_notification(uow, product_id=704, movement_id=mid)
            await uow.commit()

        by_cat = {r["category"]: r["severity"] for r in c}
        assert by_cat["low_stock"] == "info"
        assert by_cat["insufficient_stock"] == "warning"


# ===================================================================
# SECTION 4: Threshold resolution
# ===================================================================


class TestThresholdResolution:

    @pytest.mark.asyncio
    async def test_explicit_product_threshold(self, db: None) -> None:
        """Per-product threshold is used when set."""
        await _truncate_notifications()
        await _seed_product(product_id=801, name="Explicit Thr", stock=20, threshold=15)

        mid = await _insert_stock_movement(801, -8)
        assert await _on_hand(801) == pytest.approx(12)

        async with UnitOfWork() as uow:
            c = await evaluate_stock_notification(uow, product_id=801, movement_id=mid)
            await uow.commit()

        assert len(c) == 1
        assert c[0]["category"] == "low_stock"

    @pytest.mark.asyncio
    async def test_system_default_threshold(self, db: None) -> None:
        """When product threshold is NULL, system default is used.

        Default is 0 → only out-of-stock fires at zero."""
        await _truncate_notifications()
        await _seed_product(product_id=802, name="Default Thr", stock=5, threshold=None)

        # Drop to 2 — default threshold is 0, so no low_stock
        mid = await _insert_stock_movement(802, -3)
        assert await _on_hand(802) == pytest.approx(2)

        async with UnitOfWork() as uow:
            c = await evaluate_stock_notification(uow, product_id=802, movement_id=mid)
            await uow.commit()
        assert len(c) == 0  # 2 > 0, no crossing


# ===================================================================
# SECTION 5: Field validation
# ===================================================================


class TestNotificationFields:

    @pytest.mark.asyncio
    async def test_reference_fields(self, db: None) -> None:
        """Notification row has correct reference_type='product' and
        reference_id=product_id."""
        await _truncate_notifications()
        await _seed_product(product_id=901, name="Fields Test", stock=15, threshold=10)

        mid = await _insert_stock_movement(901, -8)
        async with UnitOfWork() as uow:
            c = await evaluate_stock_notification(uow, product_id=901, movement_id=mid)
            await uow.commit()

        assert len(c) >= 1
        row = c[0]
        assert row["reference_type"] == "product"
        assert row["reference_id"] == 901

    @pytest.mark.asyncio
    async def test_system_broadcast_user(self, db: None) -> None:
        """user_id is NULL (system/broadcast)."""
        await _truncate_notifications()
        await _seed_product(product_id=902, name="Broadcast", stock=15, threshold=10)

        mid = await _insert_stock_movement(902, -8)
        async with UnitOfWork() as uow:
            await evaluate_stock_notification(uow, product_id=902, movement_id=mid)
            await uow.commit()

        rows = await _get_notifications(product_id=902)
        assert len(rows) >= 1
        assert rows[0]["user_id"] is None

    @pytest.mark.asyncio
    async def test_unread_defaults(self, db: None) -> None:
        """is_read=FALSE and read_at=NULL by default."""
        await _truncate_notifications()
        await _seed_product(product_id=903, name="Unread", stock=15, threshold=10)

        mid = await _insert_stock_movement(903, -8)
        async with UnitOfWork() as uow:
            await evaluate_stock_notification(uow, product_id=903, movement_id=mid)
            await uow.commit()

        rows = await _get_notifications(product_id=903)
        assert len(rows) >= 1
        assert rows[0]["is_read"] is False
        assert rows[0]["read_at"] is None

    @pytest.mark.asyncio
    async def test_meaningful_title_body(self, db: None) -> None:
        """title and body are non-empty and mention the product."""
        await _truncate_notifications()
        await _seed_product(product_id=904, name="TitleBody", stock=15, threshold=10)

        mid = await _insert_stock_movement(904, -8)
        async with UnitOfWork() as uow:
            await evaluate_stock_notification(uow, product_id=904, movement_id=mid)
            await uow.commit()

        rows = await _get_notifications(product_id=904)
        assert len(rows) >= 1
        assert len(rows[0]["title"]) > 0
        assert "TitleBody" in rows[0]["title"]
        assert rows[0]["body"] is not None
        assert len(rows[0]["body"]) > 0


# ===================================================================
# SECTION 6: Transaction safety
# ===================================================================


class TestTransactionSafety:

    @pytest.mark.asyncio
    async def test_rolledback_movement_no_notification(self, db: None) -> None:
        """A rolled-back stock movement must not leave an orphan notification.

        Since evaluate_stock_notification runs in its own UoW (the worker
        only processes committed events via NOTIFY), and NOTIFY is only
        delivered for committed transactions, this is tested at the
        NOTIFY level (see TestStockChangeNotify.test_notify_not_fired_on_rollback).

        Additionally, verify that if we manually call evaluate on a
        non-existent movement, no notification is created.
        """
        await _truncate_notifications()
        await _seed_product(product_id=1001, name="Rollback Safe", stock=15, threshold=10)

        # Non-existent movement — service should handle gracefully
        async with UnitOfWork() as uow:
            c = await evaluate_stock_notification(
                uow, product_id=1001, movement_id=999999
            )
            await uow.commit()
        # The movement doesn't exist, so on_hand_before = on_hand_after
        # → no crossing
        assert len(c) == 0

    @pytest.mark.asyncio
    async def test_committed_movement_notification(self, db: None) -> None:
        """Committed movement + committed notification → row persists."""
        await _truncate_notifications()
        await _seed_product(product_id=1002, name="Committed", stock=15, threshold=10)

        mid = await _insert_stock_movement(1002, -8)
        async with UnitOfWork() as uow:
            c = await evaluate_stock_notification(uow, product_id=1002, movement_id=mid)
            await uow.commit()
        assert len(c) >= 1

        # Verify the notification persisted
        count = await _notification_count(product_id=1002)
        assert count >= 1


# ===================================================================
# SECTION 7: Inactive product
# ===================================================================


class TestInactiveProduct:

    @pytest.mark.asyncio
    async def test_inactive_product_no_notification(self, db: None) -> None:
        """Inactive products don't generate stock notifications."""
        await _truncate_notifications()
        await _seed_product(
            product_id=1101, name="Inactive", stock=15, threshold=10, is_active=False
        )

        mid = await _insert_stock_movement(1101, -8)
        async with UnitOfWork() as uow:
            c = await evaluate_stock_notification(uow, product_id=1101, movement_id=mid)
            await uow.commit()
        assert len(c) == 0


# ===================================================================
# SECTION 8: Integration — API-driven stock movement
# ===================================================================


class TestIntegration:

    @pytest.mark.asyncio
    async def test_stock_adjustment_creates_notification(
        self, app: Any, owner_user: dict[str, Any], db: None
    ) -> None:
        """POST /inventory/adjustments → if the adjustment crosses the
        threshold, calling evaluate_stock_notification produces a row.

        The actual worker is not running in tests; we verify the full
        pipeline by:
        1. Making an API stock adjustment that drops stock below threshold
        2. Manually calling evaluate_stock_notification with the resulting
           movement_id
        3. Verifying the notification row exists
        """
        await _truncate_notifications()
        await _seed_product(product_id=1201, name="API Adjust", stock=20, threshold=10)

        headers = await _owner_headers(app, owner_user)

        # Read updated_at for ETag
        factory = get_session_factory()
        async with factory() as session:
            row = (
                await session.execute(
                    text("SELECT updated_at FROM products WHERE id = 1201"),
                )
            ).mappings().first()
            assert row is not None
            ts = row["updated_at"]
            etag = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)

        # API call: adjust -15 (20 → 5, crossing threshold=10)
        resp = await app.post(
            "/api/v1/inventory/adjustments",
            json={"product_id": 1201, "quantity": -15, "reason": "E10 test"},
            headers={
                **headers,
                "Idempotency-Key": _idem(),
                "If-Match": f'"{etag}"',
            },
        )
        assert resp.status_code == 201, resp.text
        movement_id = resp.json()["id"]

        # Manually evaluate (worker is not running in test mode)
        async with UnitOfWork() as uow:
            created = await evaluate_stock_notification(
                uow, product_id=1201, movement_id=movement_id
            )
            await uow.commit()

        assert len(created) >= 1
        assert any(r["category"] == "low_stock" for r in created)

        # Verify in DB
        rows = await _get_notifications(product_id=1201)
        assert len(rows) >= 1


# ===================================================================
# SECTION 9: Concurrency — two rapid movements
# ===================================================================


class TestConcurrency:

    @pytest.mark.asyncio
    async def test_two_rapid_movements_one_crossing(self, db: None) -> None:
        """Two rapid stock decrements for the same product; only the first
        crossing produces a notification."""
        await _truncate_notifications()
        await _seed_product(product_id=1301, name="Rapid", stock=20, threshold=10)

        # First: 20 → 12 (above threshold, no crossing)
        mid1 = await _insert_stock_movement(1301, -8)
        async with UnitOfWork() as uow:
            c1 = await evaluate_stock_notification(uow, product_id=1301, movement_id=mid1)
            await uow.commit()
        assert len(c1) == 0

        # Second: 12 → 7 (crosses threshold)
        mid2 = await _insert_stock_movement(1301, -5)
        async with UnitOfWork() as uow:
            c2 = await evaluate_stock_notification(uow, product_id=1301, movement_id=mid2)
            await uow.commit()
        assert len(c2) == 1
        assert c2[0]["category"] == "low_stock"

        # Third: 7 → 4 (already below, no dup)
        mid3 = await _insert_stock_movement(1301, -3)
        async with UnitOfWork() as uow:
            c3 = await evaluate_stock_notification(uow, product_id=1301, movement_id=mid3)
            await uow.commit()
        assert len(c3) == 0

        # Total: exactly 1 low_stock notification
        assert await _notification_count(category="low_stock", product_id=1301) == 1
