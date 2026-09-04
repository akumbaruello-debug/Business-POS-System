"""M4 Supplier Repayments lifecycle tests.

Covers all 3 OpenAPI operations: ``listSupplierRepayments``, ``createSupplierRepayment``,
``getSupplierRepayment``.

Plus:
* SREC = outstanding AP - sum(supplier_repayments(received_amount))
* ``repayment_exceeds_srec`` when repayment > SREC
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


async def _seed_supplier_product_stock() -> None:
    """Seed supplier contact + product #1 with stock."""
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
async def _ensure_seeds(app: AsyncClient) -> AsyncGenerator[None, None]:
    await _seed_supplier_product_stock()
    yield


# ---------------------------------------------------------------------------
# Full happy path: create + list + get
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supplier_repayment_full_create_list_get(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    h = await _owner_headers(app, owner_user)

    # Purchase 200.00 on credit
    res = await app.post(
        "/api/v1/purchases",
        headers={**h, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]
    res_line = await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "10.000", "unit_price": "20.00"},
    )
    assert res_line.status_code == 201, res_line.text
    res_post = await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )
    assert res_post.status_code == 200, res_post.text

    # Repay 100.00 (bank transfer; cash is reserved for ST-2).
    res_rep = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": _idem()},
        json={
            "purchase_id": pid,
            "payment_method_id": 2,
            "amount": "100.00",
        },
    )
    assert res_rep.status_code == 201, res_rep.text
    repayment = res_rep.json()
    rid = repayment["id"]
    # `amount` is the total obligation for this purchase (200.00);
    # `received_amount` is the cumulative cash received (100.00).
    assert Decimal(str(repayment["amount"])) == Decimal("200.00")
    assert Decimal(str(repayment["received_amount"])) == Decimal("100.00")

    # List
    res_list = await app.get("/api/v1/supplier-repayments", headers=h)
    assert res_list.status_code == 200
    items = res_list.json() if isinstance(res_list.json(), list) else res_list.json().get("data", [])
    assert any(x["id"] == rid for x in items)

    # Get
    res_g = await app.get(f"/api/v1/supplier-repayments/{rid}", headers=h)
    assert res_g.status_code == 200
    assert res_g.json()["id"] == rid


# ---------------------------------------------------------------------------
# repayment_exceeds_srec
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supplier_repayment_exceeds_srec(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Repaying more than the outstanding AP → 409."""
    h = await _owner_headers(app, owner_user)

    # Purchase 100.00
    res = await app.post(
        "/api/v1/purchases",
        headers={**h, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]
    await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "5.000", "unit_price": "20.00"},
    )
    await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )

    # Repay 150.00 (only 100 AP exists)
    res_over = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"purchase_id": pid, "payment_method_id": 2, "amount": "150.00"},
    )
    assert res_over.status_code == 409, res_over.text
    assert res_over.json()["error"]["code"] == "repayment_exceeds_srec"


# ---------------------------------------------------------------------------
# Multiple repayments must not over-allocate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supplier_repayment_cumulative_bound(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """First repayment 60 ok; second 60 fails (total 120 > AP 100)."""
    h = await _owner_headers(app, owner_user)

    res = await app.post(
        "/api/v1/purchases",
        headers={**h, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]
    await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "5.000", "unit_price": "20.00"},
    )
    await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )

    # First repayment 60 ok
    r1 = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"purchase_id": pid, "payment_method_id": 2, "amount": "60.00"},
    )
    assert r1.status_code == 201, r1.text

    # Second 60 → cumulative 120 > 100 AP → fail
    r2 = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"purchase_id": pid, "payment_method_id": 2, "amount": "60.00"},
    )
    assert r2.status_code == 409, r2.text
    assert r2.json()["error"]["code"] == "repayment_exceeds_srec"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supplier_repayment_idempotency(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    h = await _owner_headers(app, owner_user)

    res = await app.post(
        "/api/v1/purchases",
        headers={**h, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]
    await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "5.000", "unit_price": "20.00"},
    )
    await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )

    body = {"purchase_id": pid, "payment_method_id": 2, "amount": "50.00"}
    idk = _idem()
    r1 = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": idk},
        json=body,
    )
    assert r1.status_code == 201

    r2 = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": idk},
        json=body,
    )
    assert r2.status_code == 200
    assert r2.headers.get("Idempotent-Replay") == "true"
    assert r2.text != ""


# ---------------------------------------------------------------------------
# Auth / capability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supplier_repayment_auth(
    app: AsyncClient,
    owner_user: dict[str, Any],
    staff_user: dict[str, Any],
) -> None:
    h_owner = await _owner_headers(app, owner_user)
    h_staff = await _staff_headers(app, staff_user)

    # 401
    res = await app.get("/api/v1/supplier-repayments")
    assert res.status_code == 401

    # Staff can list (purchase.view)
    res = await app.get("/api/v1/supplier-repayments", headers=h_staff)
    assert res.status_code == 200

    # Setup purchase
    res = await app.post(
        "/api/v1/purchases",
        headers={**h_owner, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]
    await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**h_owner, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "5.000", "unit_price": "20.00"},
    )
    await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**h_owner, "Idempotency-Key": _idem()},
        json={},
    )

    # Staff cannot create supplier repayment (purchase.refund is owner-only)
    res_staff = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h_staff, "Idempotency-Key": _idem()},
        json={"purchase_id": pid, "payment_method_id": 2, "amount": "50.00"},
    )
    assert res_staff.status_code == 403, res_staff.text
