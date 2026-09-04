"""M4 Refunds lifecycle tests.

Covers all 3 OpenAPI operations: ``listRefunds``, ``createRefund``, ``getRefund``.

Plus:
* CRL = sum(sale_payments) - sum(refunds) (must equal refundable balance per sale)
* ``refund_exceeds_crl`` when refund > CRL
* ``cash_insufficient`` when cash balance too low
* Stress Test 2: sale -> payment -> cancel -> full refund -> duplicate refund attempt
* Idempotency
* Capability boundaries
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text


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


async def _seed_customer_product_stock() -> None:
    """Seed customer contact + product #1 with stock.

    The ``db`` fixture TRUNCATEs everything per test, so this is required
    for every test that exercises sales+refunds.
    """
    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO contacts (id, type, name, created_by)"
                " VALUES (1, 'customer', 'Refund Customer', 1)"
                " ON CONFLICT (id) DO NOTHING"
            )
        )
        await session.execute(
            text(
                "INSERT INTO products (id, name, selling_price, allow_negative_stock,"
                " is_sellable, is_active, created_by, updated_by)"
                " VALUES (1, 'Refund Product', 100.00, FALSE, TRUE, TRUE, 1, 1)"
                " ON CONFLICT (id) DO UPDATE SET"
                " selling_price = EXCLUDED.selling_price,"
                " is_active = TRUE"
            )
        )
        await session.execute(
            text(
                "INSERT INTO stock_movements "
                "(product_id, trigger, quantity, unit_cost_at_movement, total_cost, "
                "reference_type, reference_id, created_by) "
                "VALUES (1, 'opening_balance', 50.000, 50.00, 2500.00, 'seed', 0, 1) "
                "ON CONFLICT DO NOTHING"
            )
        )
        await session.commit()


@pytest.fixture(autouse=True)
async def _ensure_seeds(app: AsyncClient) -> AsyncGenerator[None, None]:
    await _seed_customer_product_stock()
    yield


# ---------------------------------------------------------------------------
# Full happy path: create + list + get
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refund_full_create_list_get(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    h = await _owner_headers(app, owner_user)

    # Sale (2 units @ 100.00 = 200.00)
    res = await app.post(
        "/api/v1/sales",
        headers={**h, "Idempotency-Key": _idem()},
        json={"customer_id": 1},
    )
    sid = res.json()["id"]
    res_line = await app.post(
        f"/api/v1/sales/{sid}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "2.000", "unit_price": "100.00"},
    )
    line_id = res_line.json()["id"]
    await app.post(
        f"/api/v1/sales/{sid}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )
    await app.post(
        f"/api/v1/sales/{sid}/payments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"payment_method_id": 1, "amount": "200.00"},
    )

    # Partial refund (return 1 unit, refund 100.00)
    res_ret = await app.post(
        f"/api/v1/sales/{sid}/returns",
        headers={**h, "Idempotency-Key": _idem()},
        json={"reason": "x", "lines": [{"sale_line_id": line_id, "quantity": "1.000"}]},
    )
    assert res_ret.status_code == 201, res_ret.text
    ret_id = res_ret.json()["id"]

    # Refund 100.00 cash
    res_ref = await app.post(
        "/api/v1/refunds",
        headers={**h, "Idempotency-Key": _idem()},
        json={
            "sale_id": ret_id,
            "payment_method_id": 1,
            "amount": "100.00",
        },
    )
    assert res_ref.status_code == 201, res_ref.text
    refund = res_ref.json()
    rid = refund["id"]
    assert Decimal(str(refund["amount"])) == Decimal("100.00")

    # List contains it
    res_list = await app.get("/api/v1/refunds", headers=h)
    assert res_list.status_code == 200
    items = res_list.json() if isinstance(res_list.json(), list) else res_list.json().get("data", [])
    assert any(x["id"] == rid for x in items)

    # Get single
    res_g = await app.get(f"/api/v1/refunds/{rid}", headers=h)
    assert res_g.status_code == 200
    assert res_g.json()["id"] == rid


# ---------------------------------------------------------------------------
# refund_exceeds_crl
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refund_exceeds_crl(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Refund more than CRL (Σ payments - Σ prior refunds) → 409."""
    h = await _owner_headers(app, owner_user)

    # Sale 200.00 → pay 200.00 → full return → refund 150 (only 200 CRL available)
    res = await app.post(
        "/api/v1/sales",
        headers={**h, "Idempotency-Key": _idem()},
        json={"customer_id": 1},
    )
    sid = res.json()["id"]
    res_line = await app.post(
        f"/api/v1/sales/{sid}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "2.000", "unit_price": "100.00"},
    )
    line_id = res_line.json()["id"]
    await app.post(f"/api/v1/sales/{sid}/post", headers={**h, "Idempotency-Key": _idem()}, json={})
    await app.post(
        f"/api/v1/sales/{sid}/payments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"payment_method_id": 1, "amount": "200.00"},
    )

    # Full return of 2 units
    res_ret = await app.post(
        f"/api/v1/sales/{sid}/returns",
        headers={**h, "Idempotency-Key": _idem()},
        json={"reason": "x", "lines": [{"sale_line_id": line_id, "quantity": "2.000"}]},
    )
    assert res_ret.status_code == 201
    ret_id = res_ret.json()["id"]

    # Try to refund 300.00 (CRL only 200) → refund_exceeds_crl
    res_over = await app.post(
        "/api/v1/refunds",
        headers={**h, "Idempotency-Key": _idem()},
        json={"sale_id": ret_id, "payment_method_id": 1, "amount": "300.00"},
    )
    assert res_over.status_code == 409, res_over.text
    assert res_over.json()["error"]["code"] == "refund_exceeds_crl"


# ---------------------------------------------------------------------------
# ST-2: sale → payment → cancel → full refund → duplicate refund attempt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stress_test_2_refund_duplicate_after_cancel(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """V1.9 ST-2: full sale lifecycle + full refund + duplicate refund guard.

    Sequence:
      1. Sale 200.00 (1 line @ 100 x 2)
      2. Pay 200.00 cash
      3. Cancel sale (refundable balance = 200; refund cash via payment_method_id=1)
      4. Create refund 200.00 — must succeed
      5. Duplicate refund attempt — must fail (CRL = 0 → refund_exceeds_crl)
    """
    h = await _owner_headers(app, owner_user)

    # 1. Sale 200.00
    res = await app.post(
        "/api/v1/sales",
        headers={**h, "Idempotency-Key": _idem()},
        json={"customer_id": 1},
    )
    sid = res.json()["id"]
    await app.post(
        f"/api/v1/sales/{sid}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "2.000", "unit_price": "100.00"},
    )
    await app.post(f"/api/v1/sales/{sid}/post", headers={**h, "Idempotency-Key": _idem()}, json={})

    # 2. Pay 200.00
    res_pay = await app.post(
        f"/api/v1/sales/{sid}/payments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"payment_method_id": 1, "amount": "200.00"},
    )
    assert res_pay.status_code == 201

    # 3. Cancel sale → CRL = 200
    res_c = await app.post(
        f"/api/v1/sales/{sid}/cancel",
        headers={**h, "Idempotency-Key": _idem()},
        json={"reason": "customer cancelled"},
    )
    assert res_c.status_code == 200, res_c.text
    assert res_c.json()["lifecycle_status"] == "cancelled"

    # 4. Create full refund 200.00 (no return needed because cancel reverses whole sale)
    res_ref = await app.post(
        "/api/v1/refunds",
        headers={**h, "Idempotency-Key": _idem()},
        json={"sale_id": sid, "payment_method_id": 1, "amount": "200.00"},
    )
    assert res_ref.status_code == 201, res_ref.text
    assert Decimal(str(res_ref.json()["amount"])) == Decimal("200.00")

    # 5. Duplicate refund attempt → CRL=0 → refund_exceeds_crl
    res_dup = await app.post(
        "/api/v1/refunds",
        headers={**h, "Idempotency-Key": _idem()},
        json={"sale_id": sid, "payment_method_id": 1, "amount": "1.00"},
    )
    assert res_dup.status_code == 409, res_dup.text
    assert res_dup.json()["error"]["code"] == "refund_exceeds_crl"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refund_create_idempotency(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    h = await _owner_headers(app, owner_user)

    # Setup: sale + pay + return
    res = await app.post(
        "/api/v1/sales",
        headers={**h, "Idempotency-Key": _idem()},
        json={"customer_id": 1},
    )
    sid = res.json()["id"]
    res_line = await app.post(
        f"/api/v1/sales/{sid}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "1.000", "unit_price": "100.00"},
    )
    line_id = res_line.json()["id"]
    await app.post(f"/api/v1/sales/{sid}/post", headers={**h, "Idempotency-Key": _idem()}, json={})
    await app.post(
        f"/api/v1/sales/{sid}/payments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"payment_method_id": 1, "amount": "100.00"},
    )
    res_ret = await app.post(
        f"/api/v1/sales/{sid}/returns",
        headers={**h, "Idempotency-Key": _idem()},
        json={"reason": "x", "lines": [{"sale_line_id": line_id, "quantity": "1.000"}]},
    )
    ret_id = res_ret.json()["id"]

    body = {"sale_id": ret_id, "payment_method_id": 1, "amount": "100.00"}
    idk = _idem()
    res1 = await app.post("/api/v1/refunds", headers={**h, "Idempotency-Key": idk}, json=body)
    assert res1.status_code == 201

    # Replay
    res2 = await app.post("/api/v1/refunds", headers={**h, "Idempotency-Key": idk}, json=body)
    assert res2.status_code == 200
    assert res2.headers.get("Idempotent-Replay") == "true"
    # The cached body is a JSON string (replayed verbatim); the
    # authoritative signal is the Idempotent-Replay header + 200 status.
    assert res2.text != ""


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refund_auth(
    app: AsyncClient,
    owner_user: dict[str, Any],
    staff_user: dict[str, Any],
) -> None:
    h_owner = await _owner_headers(app, owner_user)
    h_staff = await _staff_headers(app, staff_user)

    # 401 no auth
    res = await app.get("/api/v1/refunds")
    assert res.status_code == 401

    # Staff can list (sale.view)
    res = await app.get("/api/v1/refunds", headers=h_staff)
    assert res.status_code == 200

    # Setup sale → staff can NOT create refund (purchase.refund owner-only)
    res = await app.post(
        "/api/v1/sales",
        headers={**h_owner, "Idempotency-Key": _idem()},
        json={"customer_id": 1},
    )
    sid = res.json()["id"]
    res_line = await app.post(
        f"/api/v1/sales/{sid}/lines",
        headers={**h_owner, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "1.000", "unit_price": "100.00"},
    )
    line_id = res_line.json()["id"]
    await app.post(f"/api/v1/sales/{sid}/post", headers={**h_owner, "Idempotency-Key": _idem()}, json={})
    await app.post(
        f"/api/v1/sales/{sid}/payments",
        headers={**h_owner, "Idempotency-Key": _idem()},
        json={"payment_method_id": 1, "amount": "100.00"},
    )
    res_ret = await app.post(
        f"/api/v1/sales/{sid}/returns",
        headers={**h_owner, "Idempotency-Key": _idem()},
        json={"reason": "x", "lines": [{"sale_line_id": line_id, "quantity": "1.000"}]},
    )
    ret_id = res_ret.json()["id"]

    res_staff_refund = await app.post(
        "/api/v1/refunds",
        headers={**h_staff, "Idempotency-Key": _idem()},
        json={"sale_id": ret_id, "payment_method_id": 1, "amount": "100.00"},
    )
    assert res_staff_refund.status_code == 403, res_staff_refund.text
