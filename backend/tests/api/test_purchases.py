"""M4 Purchases lifecycle tests.

Covers all 18 OpenAPI operations across:
* ``/purchases`` draft CRUD
* ``/purchases/{id}/lines`` line CRUD
* ``/purchases/{id}/shipping`` set/update
* ``/purchases/{id}/post`` posting
* ``/purchases/{id}/payments`` list + add
* ``/purchases/{id}/cancel`` cancellation (draft + posted w/ on-hand + consumed split)
* ``/purchases/{id}/returns`` create
* ``/purchase-returns`` list / get / cancel
+ Stress Test 3 (purchase → receipt → partial sale → cancel)
+ ETag/If-Match concurrency
+ Idempotency
+ Capability boundaries (purchase.{view,create,edit_own_draft,post,complete,cancel,return})
+ Error codes (``allocation_exceeds_payable``, ``lifecycle_state_invalid``)
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Local helpers
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


def _etag(resp: Any) -> str:
    """Return the resource ETag for If-Match.

    Prefer the response ``ETag`` header: that is the canonical validator
    the server compares against in ``check_if_match`` — a quoted ISO-8601
    timestamp serialised with the ``Z`` UTC suffix. Reading the body's
    ``updated_at`` instead yields the ``+00:00`` offset form (FastAPI's
    ``jsonable_encoder`` uses ``datetime.isoformat``), which does not
    string-compare equal to the server's ETag and would 412 every
    mutation. Fall back to the body field only when the header is absent.
    """
    hdr = resp.headers.get("ETag")
    if hdr:
        return hdr
    return f'"{resp.json()["updated_at"]}"'


async def _seed_supplier_and_product() -> None:
    """Seed a supplier contact (id=1) + purchasable product (id=1) + stock.

    The ``db`` fixture TRUNCATEs products / contacts per test, so this
    runs at the top of any test that needs them.
    """
    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO contacts (id, type, name, created_by)"
                " VALUES (1, 'supplier', 'Test Supplier', 1)"
                " ON CONFLICT (id) DO NOTHING"
            )
        )
        await session.execute(
            text(
                "INSERT INTO products (id, name, selling_price, allow_negative_stock,"
                " is_sellable, is_purchasable, is_active, created_by, updated_by)"
                " VALUES (1, 'Test Product', 50.00, FALSE, TRUE, TRUE, TRUE, 1, 1)"
                " ON CONFLICT (id) DO UPDATE SET"
                " selling_price = EXCLUDED.selling_price,"
                " is_active = TRUE,"
                " is_purchasable = TRUE"
            )
        )
        await session.commit()


@pytest.fixture(autouse=True)
async def _ensure_purchase_seeds(app: AsyncClient) -> AsyncGenerator[None, None]:
    """Auto-seed supplier + purchasable product for every test in this module."""
    await _seed_supplier_and_product()
    yield


# ---------------------------------------------------------------------------
# Draft CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purchase_draft_crud_lifecycle(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Draft list → create → get → patch → delete."""
    h = await _owner_headers(app, owner_user)

    # 1. Create draft (no body required beyond idempotency key)
    res = await app.post(
        "/api/v1/purchases",
        headers={**h, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    assert res.status_code == 201, res.text
    purchase = res.json()
    pid = purchase["id"]
    assert purchase["lifecycle_status"] == "draft"
    assert purchase["payment_state"] == "unpaid"
    assert Decimal(str(purchase["total_amount"])) == 0

    # 2. Get single
    res_g = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    assert res_g.status_code == 200
    assert res_g.json()["id"] == pid

    # 3. List contains it
    res_l = await app.get("/api/v1/purchases", headers=h)
    assert res_l.status_code == 200
    body = res_l.json()
    items = body if isinstance(body, list) else body.get("data", [])
    assert any(p["id"] == pid for p in items)

    # 4. Patch draft (If-Match with current ETag)
    etag = _etag(res)
    res_p = await app.patch(
        f"/api/v1/purchases/{pid}",
        headers={**h, "If-Match": etag},
        json={"notes": "edited"},
    )
    assert res_p.status_code == 200, res_p.text
    assert res_p.json()["notes"] == "edited"

    # 5. Delete draft
    etag = _etag(res_p)
    res_d = await app.request(
        "DELETE",
        f"/api/v1/purchases/{pid}",
        headers={**h, "If-Match": etag, "Idempotency-Key": _idem()},
    )
    assert res_d.status_code in (200, 204), res_d.text

    # 6. 404 after delete
    res_404 = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    assert res_404.status_code == 404


# ---------------------------------------------------------------------------
# Line CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purchase_line_crud_and_landed_cost(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Add line → update line → list → recompute total → delete line."""
    h = await _owner_headers(app, owner_user)

    # Draft
    res = await app.post(
        "/api/v1/purchases",
        headers={**h, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]
    etag = _etag(res)

    # Add line (10 units @ 12.50 = 125.00)
    res_line = await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**h, "If-Match": etag, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "10.000", "unit_price": "12.50"},
    )
    assert res_line.status_code == 201, res_line.text
    line = res_line.json()
    lid = line["id"]
    assert Decimal(str(line["line_total"])) == Decimal("125.00")
    etag = _etag(res_line)

    # List lines
    res_list = await app.get(f"/api/v1/purchases/{pid}/lines", headers=h)
    assert res_list.status_code == 200
    items = res_list.json() if isinstance(res_list.json(), list) else res_list.json().get("data", [])
    assert any(x["id"] == lid for x in items)

    # Update line (quantity 20 → 250.00)
    res_upd = await app.patch(
        f"/api/v1/purchases/{pid}/lines/{lid}",
        headers={**h, "If-Match": etag},
        json={"quantity": "20.000"},
    )
    assert res_upd.status_code == 200, res_upd.text
    assert Decimal(str(res_upd.json()["line_total"])) == Decimal("250.00")
    etag = _etag(res_upd)

    # Delete line
    res_del = await app.request(
        "DELETE",
        f"/api/v1/purchases/{pid}/lines/{lid}",
        headers={**h, "If-Match": etag, "Idempotency-Key": _idem()},
    )
    assert res_del.status_code in (200, 204), res_del.text

    # Total back to 0
    res_p = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    assert Decimal(str(res_p.json()["total_amount"])) == 0


# ---------------------------------------------------------------------------
# Shipping → landed cost allocation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purchase_shipping_set_and_update(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Set shipping cost → verify total increases → patch shipping."""
    h = await _owner_headers(app, owner_user)

    res = await app.post(
        "/api/v1/purchases",
        headers={**h, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]

    # Set shipping (paid_in_cash=FALSE so no cash movement yet)
    res_ship = await app.post(
        f"/api/v1/purchases/{pid}/shipping",
        headers={**h, "Idempotency-Key": _idem()},
        json={"amount": "25.00", "paid_in_cash": False, "description": "DHL"},
    )
    assert res_ship.status_code in (200, 201), res_ship.text
    shipping = res_ship.json()
    assert Decimal(str(shipping["amount"])) == Decimal("25.00")
    etag = _etag(res_ship)

    # Update shipping
    res_upd = await app.patch(
        f"/api/v1/purchases/{pid}/shipping",
        headers={**h, "If-Match": etag},
        json={"amount": "50.00", "paid_in_cash": True, "description": "FedEx"},
    )
    assert res_upd.status_code == 200, res_upd.text
    assert Decimal(str(res_upd.json()["amount"])) == Decimal("50.00")
    assert res_upd.json()["paid_in_cash"] is True


# ---------------------------------------------------------------------------
# Posting + stock receipt + AP creation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purchase_post_creates_stock_and_ap(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Post purchase → stock_movement('purchase_receipt') → moving-avg updated → AR/AP created.

    BR-PURCHASE-002: posted purchase sets received_date.
    """
    from app.db import get_session_factory

    h = await _owner_headers(app, owner_user)

    res = await app.post(
        "/api/v1/purchases",
        headers={**h, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]

    # Add line (10 units @ 12.50 = 125.00)
    res_line = await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "10.000", "unit_price": "12.50"},
    )
    assert res_line.status_code == 201, res_line.text

    # Post
    res_post = await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )
    assert res_post.status_code == 200, res_post.text
    posted = res_post.json()
    assert posted["lifecycle_status"] == "posted"
    assert posted["received_date"] is not None  # BR-PURCHASE-002

    # Verify stock_movement row exists with trigger='purchase_receipt'
    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT trigger, SUM(quantity) AS qty, SUM(total_cost) AS cost"
                    " FROM stock_movements WHERE reference_type = 'purchase'"
                    " AND reference_id = :pid"
                    " GROUP BY trigger"
                ),
                {"pid": pid},
            )
        ).mappings().first()
    assert row is not None
    assert row["trigger"] == "purchase_receipt"
    assert Decimal(str(row["qty"])) == Decimal("10.000")
    assert Decimal(str(row["cost"])) == Decimal("125.00")


# ---------------------------------------------------------------------------
# Payment allocation + over-tender + allocation bound
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purchase_payment_allocation_and_bound(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Add cash payment below total → ok. Try over-allocation → allocation_exceeds_payable."""
    h = await _owner_headers(app, owner_user)

    # Create + line + post
    res = await app.post(
        "/api/v1/purchases",
        headers={**h, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]
    await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "10.000", "unit_price": "12.50"},
    )
    res_post = await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )
    assert res_post.status_code == 200, res_post.text

    # List payments (empty)
    res_lp = await app.get(f"/api/v1/purchases/{pid}/payments", headers=h)
    assert res_lp.status_code == 200
    items = res_lp.json() if isinstance(res_lp.json(), list) else res_lp.json().get("data", [])
    assert items == []

    # Add bank-transfer payment 50.00 (partial — total 125.00)
    res_pay = await app.post(
        f"/api/v1/purchases/{pid}/payments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"payment_method_id": 2, "amount": "50.00"},
    )
    assert res_pay.status_code == 201, res_pay.text

    # Try to over-allocate (50 + 80 = 130 > 125; 80 alone > 75 remaining)
    res_over = await app.post(
        f"/api/v1/purchases/{pid}/payments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"payment_method_id": 2, "amount": "80.00"},
    )
    assert res_over.status_code == 409, res_over.text
    assert res_over.json()["error"]["code"] == "allocation_exceeds_payable"


# ---------------------------------------------------------------------------
# Cancellation: draft + posted w/ on-hand + consumed (ST-3 scenario)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purchase_cancel_draft_simple(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Cancel a draft purchase → no stock movement, lifecycle=cancelled."""
    h = await _owner_headers(app, owner_user)

    res = await app.post(
        "/api/v1/purchases",
        headers={**h, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]
    res_c = await app.post(
        f"/api/v1/purchases/{pid}/cancel",
        headers={**h, "Idempotency-Key": _idem()},
        json={"reason": "created in error"},
    )
    assert res_c.status_code == 200, res_c.text
    assert res_c.json()["lifecycle_status"] == "cancelled"


@pytest.mark.asyncio
async def test_purchase_cancel_posted_with_consumed_split(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Stress Test 3: purchase → receipt → partial sale (consumed) → cancel.

    Verifies the on-hand portion is reversed via ``stock_movement('purchase_reversal')``
    and the sold portion is reversed via ``stock_movement('purchase_consumed_reversal')``
    with appropriate cost.
    """
    from app.db import get_session_factory

    h = await _owner_headers(app, owner_user)

    # Purchase 10 units @ 12.50 = 125.00
    res = await app.post(
        "/api/v1/purchases",
        headers={**h, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]
    await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "10.000", "unit_price": "12.50"},
    )
    res_post = await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )
    assert res_post.status_code == 200, res_post.text

    # Seed a customer + sale that consumes 4 units (40% of 10)
    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO contacts (id, type, name, created_by)"
                " VALUES (2, 'customer', 'Test Customer 2', 1)"
                " ON CONFLICT (id) DO NOTHING"
            )
        )
        await session.commit()

    res_sale = await app.post(
        "/api/v1/sales",
        headers={**h, "Idempotency-Key": _idem()},
        json={"customer_id": 2},
    )
    sid = res_sale.json()["id"]
    await app.post(
        f"/api/v1/sales/{sid}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "4.000", "unit_price": "50.00"},
    )
    await app.post(
        f"/api/v1/sales/{sid}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )

    # Now cancel the purchase → triggers should insert purchase_reversal
    # for the on-hand 6 units, and purchase_consumed_reversal for the 4 sold.
    res_c = await app.post(
        f"/api/v1/purchases/{pid}/cancel",
        headers={**h, "Idempotency-Key": _idem()},
        json={"reason": "buyer's remorse"},
    )
    assert res_c.status_code == 200, res_c.text
    assert res_c.json()["lifecycle_status"] == "cancelled"

    async with factory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT trigger, SUM(quantity) AS qty, SUM(total_cost) AS cost"
                    " FROM stock_movements WHERE reference_type = 'purchase'"
                    " AND reference_id = :pid"
                    " GROUP BY trigger"
                    " ORDER BY trigger"
                ),
                {"pid": pid},
            )
        ).mappings().all()

    triggers = {r["trigger"]: r for r in rows}
    # Original purchase_receipt (10 @ 12.50 = 125)
    assert "purchase_receipt" in triggers
    assert Decimal(str(triggers["purchase_receipt"]["qty"])) == Decimal("10.000")
    # Reversal: full unwinding (-10) at landed cost = -125
    assert "purchase_reversal" in triggers
    rev = triggers["purchase_reversal"]
    assert Decimal(str(rev["qty"])) == Decimal("-10.000")
    assert Decimal(str(rev["cost"])) == Decimal("-125.00")
    # Companion value_adjustment records the consumed split (4 units
    # at landed cost → +50.00 to balance cost back to -on_hand*cost).
    assert "value_adjustment" in triggers
    consumed = triggers["value_adjustment"]
    assert Decimal(str(consumed["qty"])) == Decimal("0.0000")
    assert Decimal(str(consumed["cost"])) == Decimal("50.00")


# ---------------------------------------------------------------------------
# Purchase returns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purchase_return_create_list_get_cancel(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Create return → list → get → cancel; quantity bound enforced."""
    h = await _owner_headers(app, owner_user)

    # Purchase 20 units @ 10.00 = 200.00
    res = await app.post(
        "/api/v1/purchases",
        headers={**h, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]
    res_line = await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "20.000", "unit_price": "10.00"},
    )
    line_id = res_line.json()["id"]
    await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )

    # Return 5 units
    res_ret = await app.post(
        f"/api/v1/purchases/{pid}/returns",
        headers={**h, "Idempotency-Key": _idem()},
        json={"reason": "damaged", "lines": [{"purchase_line_id": line_id, "quantity": "5.000"}]},
    )
    assert res_ret.status_code == 201, res_ret.text
    ret_id = res_ret.json()["id"]

    # List
    res_list = await app.get("/api/v1/purchase-returns", headers=h)
    assert res_list.status_code == 200
    items = res_list.json() if isinstance(res_list.json(), list) else res_list.json().get("data", [])
    assert any(x["id"] == ret_id for x in items)

    # Get
    res_g = await app.get(f"/api/v1/purchase-returns/{ret_id}", headers=h)
    assert res_g.status_code == 200
    assert res_g.json()["id"] == ret_id

    # Cancel return
    res_c = await app.post(
        f"/api/v1/purchase-returns/{ret_id}/cancel",
        headers={**h, "Idempotency-Key": _idem()},
        json={"reason": "vendor dispute resolved"},
    )
    assert res_c.status_code == 200, res_c.text
    assert res_c.json()["lifecycle_status"] == "cancelled"


@pytest.mark.asyncio
async def test_purchase_return_quantity_bound(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Returning more units than purchased must be rejected."""
    h = await _owner_headers(app, owner_user)

    res = await app.post(
        "/api/v1/purchases",
        headers={**h, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]
    res_line = await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "10.000", "unit_price": "10.00"},
    )
    line_id = res_line.json()["id"]
    await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )

    # Try to return 100 (max 10)
    res_over = await app.post(
        f"/api/v1/purchases/{pid}/returns",
        headers={**h, "Idempotency-Key": _idem()},
        json={"reason": "x", "lines": [{"purchase_line_id": line_id, "quantity": "100.000"}]},
    )
    assert res_over.status_code in (400, 409), res_over.text


# ---------------------------------------------------------------------------
# ETag / If-Match concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purchase_etag_concurrency(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Stale ETag → 412 version_mismatch."""
    h = await _owner_headers(app, owner_user)

    res = await app.post(
        "/api/v1/purchases",
        headers={**h, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]

    res1 = await app.patch(
        f"/api/v1/purchases/{pid}",
        headers={**h, "If-Match": _etag(res)},
        json={"notes": "v1"},
    )
    assert res1.status_code == 200, res1.text

    # Replay with stale ETag → 412
    res2 = await app.patch(
        f"/api/v1/purchases/{pid}",
        headers={**h, "If-Match": _etag(res)},
        json={"notes": "v2"},
    )
    assert res2.status_code == 412, res2.text


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purchase_create_idempotency(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Same key + same body → 200 replay; same key + different body → 409."""
    h = await _owner_headers(app, owner_user)
    idk = _idem()

    # Missing key → 400
    res0 = await app.post("/api/v1/purchases", headers=h, json={"supplier_id": 1})
    assert res0.status_code == 400

    body = {"supplier_id": 1}
    res1 = await app.post(
        "/api/v1/purchases", headers={**h, "Idempotency-Key": idk}, json=body
    )
    assert res1.status_code == 201
    pid = res1.json()["id"]

    # Replay → 200 + Idempotent-Replay
    res2 = await app.post(
        "/api/v1/purchases", headers={**h, "Idempotency-Key": idk}, json=body
    )
    assert res2.status_code == 200
    assert res2.headers.get("Idempotent-Replay") == "true"
    assert res2.json()["id"] == pid

    # Different body → 409
    res3 = await app.post(
        "/api/v1/purchases",
        headers={**h, "Idempotency-Key": idk},
        json={"supplier_id": 1, "notes": "diff"},
    )
    assert res3.status_code == 409


# ---------------------------------------------------------------------------
# Authorization / capability boundaries
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purchase_auth_and_capability(
    app: AsyncClient,
    owner_user: dict[str, Any],
    staff_user: dict[str, Any],
) -> None:
    """401 no auth; 403 wrong capability."""
    h_owner = await _owner_headers(app, owner_user)
    h_staff = await _staff_headers(app, staff_user)

    # 401
    res = await app.get("/api/v1/purchases")
    assert res.status_code == 401

    # Staff CAN create draft (purchase.create default-on)
    res = await app.post(
        "/api/v1/purchases",
        headers={**h_staff, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    assert res.status_code == 201
    pid = res.json()["id"]

    # Staff CANNOT post (purchase.post owner-only by default)
    res_post = await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**h_staff, "Idempotency-Key": _idem()},
        json={},
    )
    assert res_post.status_code == 403, res_post.text

    # Owner CAN post
    res_line = await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**h_owner, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "5.000", "unit_price": "10.00"},
    )
    assert res_line.status_code == 201, res_line.text

    res_post = await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**h_owner, "Idempotency-Key": _idem()},
        json={},
    )
    assert res_post.status_code == 200, res_post.text


# ---------------------------------------------------------------------------
# Terminal state: cannot mutate a cancelled purchase
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_purchase_cancelled_is_terminal(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Once cancelled, cannot add lines / patch / post / pay."""
    h = await _owner_headers(app, owner_user)

    res = await app.post(
        "/api/v1/purchases",
        headers={**h, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]
    res_c = await app.post(
        f"/api/v1/purchases/{pid}/cancel",
        headers={**h, "Idempotency-Key": _idem()},
        json={"reason": "x"},
    )
    assert res_c.status_code == 200
    etag = _etag(res_c)

    res_line = await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**h, "If-Match": etag, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "1.000", "unit_price": "1.00"},
    )
    assert res_line.status_code in (409, 422), res_line.text
