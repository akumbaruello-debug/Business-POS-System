"""Customer summary endpoint tests (G1 phase).

GET /contacts/{id}/summary returns {total_sales, sales_count, receivable} per:
  - Total Sales = posted non-cancelled sales revenue - posted returns selling price
  - Sales count = count of posted non-cancelled sales rows
  - Receivable = GREATEST(sale_total - payments - selling_returned, 0) summed
  - Draft excluded, cancelled excluded, supplier-only rejected, unknown -> 404
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


def _etag(res: Any) -> str:
    raw = str(res.headers.get("ETag", "")).strip('"')
    return f'"{raw}"'


async def _login(app: AsyncClient, username: str, password: str) -> str:
    r = await app.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"Idempotency-Key": _idem()},
    )
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


async def _headers(app: AsyncClient) -> dict[str, str]:
    token = await _login(app, "owner", "OwnerPass123!")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
async def _seed(app: AsyncClient) -> AsyncGenerator[None, None]:
    """Seed contacts + product + stock movements."""
    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO contacts (id, type, name, email, phone, is_active, created_by)"
                " VALUES (10, 'customer', 'Alice Customer', 'alice@example.test', '111', TRUE, 1)"
                " ON CONFLICT (id) DO NOTHING"
            )
        )
        await session.execute(
            text(
                "INSERT INTO contacts (id, type, name, email, phone, is_active, created_by)"
                " VALUES (30, 'supplier', 'Sup Only', 'sup@example.test', '999', TRUE, 1)"
                " ON CONFLICT (id) DO NOTHING"
            )
        )
        await session.execute(
            text(
                "INSERT INTO contacts (id, type, name, email, phone, is_active, created_by)"
                " VALUES (40, 'both', 'Both Person', 'both@example.test', '555', TRUE, 1)"
                " ON CONFLICT (id) DO NOTHING"
            )
        )
        await session.execute(
            text(
                "INSERT INTO products (id, name, selling_price, allow_negative_stock,"
                " is_sellable, is_active, created_by, updated_by)"
                " VALUES (10, 'Test Product', 100.00, FALSE, TRUE, TRUE, 1, 1)"
                " ON CONFLICT (id) DO UPDATE SET selling_price = EXCLUDED.selling_price, is_active = TRUE"
            )
        )
        await session.execute(
            text(
                "INSERT INTO stock_movements "
                "(product_id, trigger, quantity, unit_cost_at_movement, total_cost, "
                "reference_type, reference_id, created_by) "
                "VALUES (10, 'opening_balance', 1000.000, 50.00, 50000.00, 'seed', 0, 1) "
                "ON CONFLICT DO NOTHING"
            )
        )
        await session.commit()
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_posted_sale(
    app: AsyncClient,
    headers: dict[str, str],
    customer_id: int = 10,
    qty: str = "2.000",
    unit_price: str = "100.00",
) -> tuple[int, int, str]:
    """Create a draft sale, add a line, and post it. Returns (sale_id, line_id, etag)."""
    res = await app.post(
        "/api/v1/sales",
        headers={**headers, "Idempotency-Key": _idem()},
        json={"customer_id": customer_id, "notes": "test sale"},
    )
    assert res.status_code == 201, res.text
    sid = res.json()["id"]

    res_line = await app.post(
        f"/api/v1/sales/{sid}/lines",
        headers={**headers, "Idempotency-Key": _idem()},
        json={"product_id": 10, "quantity": qty, "unit_price": unit_price},
    )
    assert res_line.status_code == 201, res_line.text
    lid = res_line.json()["id"]

    res_post = await app.post(
        f"/api/v1/sales/{sid}/post",
        headers={**headers, "Idempotency-Key": _idem()},
        json={},
    )
    assert res_post.status_code == 200, res_post.text
    etag = _etag(res_post)
    return sid, lid, etag


async def _capture_payment(app: AsyncClient, headers: dict[str, str], sale_id: int, amount: str) -> str:
    res = await app.post(
        f"/api/v1/sales/{sale_id}/payments",
        headers={**headers, "Idempotency-Key": _idem()},
        json={"payment_method_id": 1, "amount": amount},
    )
    assert res.status_code == 201, res.text
    return _etag(res)


async def _post_return(
    app: AsyncClient, headers: dict[str, str], sale_id: int, line_id: int, qty: str
) -> int:
    res = await app.post(
        f"/api/v1/sales/{sale_id}/returns",
        headers={**headers, "Idempotency-Key": _idem()},
        json={
            "reason": "Customer changed mind",
            "lines": [{"sale_line_id": line_id, "quantity": qty}],
        },
    )
    assert res.status_code == 201, res.text
    return int(res.json()["id"])


async def _get_summary(app: AsyncClient, headers: dict[str, str], contact_id: int):
    return await app.get(f"/api/v1/contacts/{contact_id}/summary", headers=headers)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_response_shape(app: AsyncClient) -> None:
    """Endpoint returns exactly {total_sales, sales_count, receivable}."""
    h = await _headers(app)
    res = await _get_summary(app, h, 10)
    assert res.status_code == 200, res.text
    data = res.json()
    assert set(data.keys()) == {"total_sales", "sales_count", "receivable"}, data
    assert isinstance(data["total_sales"], (int, float))
    assert isinstance(data["sales_count"], int)
    assert isinstance(data["receivable"], (int, float))


@pytest.mark.asyncio
async def test_posted_sale_payment_return(app: AsyncClient) -> None:
    """Post sale: 2 qty @ 100 = 200.
    Pay 50. Return 1 qty (100).
    Total Sales = 200 - 100 = 100.
    Receivable = GREATEST(200 - 50 - 100, 0) = 50.
    Sales count = 1.
    """
    h = await _headers(app)
    sid, lid, _ = await _create_posted_sale(app, h, customer_id=10, qty="2.000", unit_price="100.00")

    await _capture_payment(app, h, sid, amount="50.00")
    await _post_return(app, h, sid, lid, qty="1.000")

    res = await _get_summary(app, h, 10)
    assert res.status_code == 200, res.text
    data = res.json()
    assert Decimal(str(data["total_sales"])) == Decimal("100.00"), data
    assert Decimal(str(data["receivable"])) == Decimal("50.00"), data
    assert data["sales_count"] == 1, data


@pytest.mark.asyncio
async def test_draft_excluded(app: AsyncClient) -> None:
    """Draft sale must NOT count toward sales_count or total_sales."""
    h = await _headers(app)
    # create draft sale, do not post
    res = await app.post(
        "/api/v1/sales",
        headers={**h, "Idempotency-Key": _idem()},
        json={"customer_id": 10, "notes": "draft sale"},
    )
    assert res.status_code == 201, res.text

    res_sum = await _get_summary(app, h, 10)
    assert res_sum.status_code == 200, res_sum.text
    data = res_sum.json()
    assert data["total_sales"] == 0.0
    assert data["sales_count"] == 0
    assert data["receivable"] == 0.0


@pytest.mark.asyncio
async def test_cancelled_excluded(app: AsyncClient) -> None:
    """Cancelled posted sale must NOT count."""
    h = await _headers(app)
    sid, _, etag = await _create_posted_sale(app, h, customer_id=10, qty="1.000", unit_price="100.00")

    # Cancel the posted sale
    res = await app.post(
        f"/api/v1/sales/{sid}/cancel",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"reason": "Customer cancelled order"},
    )
    assert res.status_code == 200, res.text

    summary = await _get_summary(app, h, 10)
    assert summary.status_code == 200, summary.text
    data = summary.json()
    assert data["total_sales"] == 0.0
    assert data["sales_count"] == 0
    assert data["receivable"] == 0.0


@pytest.mark.asyncio
async def test_multiple_sales_aggregate(app: AsyncClient) -> None:
    """Aggregate over multiple posted sales."""
    h = await _headers(app)
    s1, _, _ = await _create_posted_sale(app, h, customer_id=10, qty="1.000", unit_price="100.00")
    s2, l2, _ = await _create_posted_sale(app, h, customer_id=10, qty="2.000", unit_price="50.00")

    # s1: 100, pay 20 -> total_sales=100, receivable=80
    await _capture_payment(app, h, s1, amount="20.00")
    # s2: 100, return 1 @ 50 -> total_sales=50, receivable=50
    await _post_return(app, h, s2, l2, qty="1.000")

    res = await _get_summary(app, h, 10)
    assert res.status_code == 200, res.text
    data = res.json()
    assert Decimal(str(data["total_sales"])) == Decimal("150.00"), data
    assert Decimal(str(data["receivable"])) == Decimal("130.00"), data
    assert data["sales_count"] == 2


@pytest.mark.asyncio
async def test_type_both_accepted(app: AsyncClient) -> None:
    """type='both' contact returns summary successfully."""
    h = await _headers(app)
    await _create_posted_sale(app, h, customer_id=40, qty="1.000", unit_price="75.00")
    res = await _get_summary(app, h, 40)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["sales_count"] == 1
    assert Decimal(str(data["total_sales"])) == Decimal("75.00")


@pytest.mark.asyncio
async def test_supplier_only_rejected(app: AsyncClient) -> None:
    """type='supplier' -> 404."""
    h = await _headers(app)
    res = await _get_summary(app, h, 30)
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_unknown_contact(app: AsyncClient) -> None:
    """Nonexistent contact -> 404."""
    h = await _headers(app)
    res = await _get_summary(app, h, 99999)
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_receivable_no_negative(app: AsyncClient) -> None:
    """Payments matching/exceeding sale total -> receivable floored at 0."""
    h = await _headers(app)
    sid, _, _ = await _create_posted_sale(app, h, customer_id=10, qty="1.000", unit_price="100.00")

    # Pay full amount 100.00
    await _capture_payment(app, h, sid, amount="100.00")

    res = await _get_summary(app, h, 10)
    assert res.status_code == 200, res.text
    data = res.json()
    assert Decimal(str(data["total_sales"])) == Decimal("100.00")
    assert Decimal(str(data["receivable"])) == Decimal("0.00")
    assert data["sales_count"] == 1
