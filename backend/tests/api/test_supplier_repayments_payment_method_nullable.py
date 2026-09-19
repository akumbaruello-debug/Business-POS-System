"""Contractual validation for nullable payment_method_id on supplier_repayments.

Tests:
A. Purchase return creating SREC:
   - purchase with paid > 0 and return > remaining AP
   - verify SREC created
   - verify supplier_repayments row has:
     - amount = srec
     - received_amount = 0
     - payment_method_id IS NULL

B. Actual repayment:
   - repaying that obligation with a real payment method (e.g. bank_transfer = 2)
   - verify received_amount increases correctly
   - verify payment_method_id reflects the actual settlement method

C. Multiple partial repayments:
   - verify received_amount accumulates
   - verify remaining SREC decreases
   - verify ceiling is respected (cannot exceed obligation amount)

D. Zero-cash-received state:
   - verify an unpaid obligation does NOT create cash_movements
   - verify cash ledger is untouched until repayment occurs

E. Payment method filtering / reporting:
   - verify filtering by payment_method_id does not classify an unpaid SREC obligation as cash
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.db import get_session_factory
from tests.api.test_purchases import (
    _idem,
    _owner_headers,
    _seed_supplier_and_product,
)


@pytest.fixture(autouse=True)
async def _ensure_seeds(app: AsyncClient) -> AsyncGenerator[None, None]:
    await _seed_supplier_and_product()
    yield


async def _create_posted_and_paid_purchase(
    app: AsyncClient, headers: dict[str, str], *, qty: str = "10.000", price: str = "10.00", paid: str = "100.00"
) -> tuple[int, int]:
    """Create a purchase, add line, post, and record payment."""
    res = await app.post(
        "/api/v1/purchases",
        headers={**headers, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    assert res.status_code == 201, res.text
    pid = res.json()["id"]

    res_line = await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**headers, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": qty, "unit_price": price},
    )
    assert res_line.status_code == 201, res_line.text
    line_id = res_line.json()["id"]

    res_post = await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**headers, "Idempotency-Key": _idem()},
        json={},
    )
    assert res_post.status_code == 200, res_post.text

    if Decimal(paid) > Decimal("0"):
        res_pay = await app.post(
            f"/api/v1/purchases/{pid}/payments",
            headers={**headers, "Idempotency-Key": _idem()},
            json={"payment_method_id": 2, "amount": paid},
        )
        assert res_pay.status_code == 201, res_pay.text

    return pid, line_id


@pytest.mark.asyncio
async def test_a_purchase_return_creates_srec_with_null_payment_method(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Test A: Purchase return with paid > 0 creates SREC obligation with payment_method_id = NULL."""
    h = await _owner_headers(app, owner_user)
    # Total = 100.00, Paid = 100.00 -> remaining AP = 0.
    # Return 4 units @ 10.00 = 40.00 -> return > remaining AP -> SREC = 40.00.
    pid, line_id = await _create_posted_and_paid_purchase(app, h, qty="10.000", price="10.00", paid="100.00")

    res_ret = await app.post(
        f"/api/v1/purchases/{pid}/returns",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": "*"},
        json={
            "reason": "damaged",
            "lines": [{"purchase_line_id": line_id, "quantity": "4.000"}],
        },
    )
    assert res_ret.status_code == 201, res_ret.text

    # Verify supplier_repayments row directly in DB
    factory = get_session_factory()
    async with factory() as session:
        res = await session.execute(
            text(
                "SELECT id, purchase_id, amount, received_amount, payment_method_id, reason "
                "FROM supplier_repayments WHERE purchase_id = :pid"
            ),
            {"pid": pid},
        )
        rows = res.fetchall()

    assert len(rows) == 1
    row = rows[0]
    assert Decimal(str(row.amount)) == Decimal("40.00")
    assert Decimal(str(row.received_amount)) == Decimal("0.00")
    assert row.payment_method_id is None, f"Expected payment_method_id IS NULL, got {row.payment_method_id}"
    assert row.reason == "purchase_return_credit"


@pytest.mark.asyncio
async def test_b_actual_repayment_updates_received_amount_and_method(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Test B: Actual repayment sets payment_method_id and increases received_amount."""
    h = await _owner_headers(app, owner_user)
    pid, line_id = await _create_posted_and_paid_purchase(app, h, qty="10.000", price="10.00", paid="100.00")

    # Return 50.00 of goods -> SREC = 50.00
    res_ret = await app.post(
        f"/api/v1/purchases/{pid}/returns",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": "*"},
        json={
            "reason": "overstock return",
            "lines": [{"purchase_line_id": line_id, "quantity": "5.000"}],
        },
    )
    assert res_ret.status_code == 201, res_ret.text

    # Get purchase ETag
    p_res = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    etag = p_res.headers["etag"]

    # Repay 30.00 via bank_transfer (id = 2)
    rep_res = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
        json={
            "purchase_id": pid,
            "amount": 30.00,
            "payment_method_id": 2,
            "reason": "supplier refund wire",
        },
    )
    assert rep_res.status_code == 201, rep_res.text
    rep_data = rep_res.json()
    assert rep_data["received_amount"] == 30.00
    assert rep_data["payment_method_id"] == 2

    # Verify DB state
    factory = get_session_factory()
    async with factory() as session:
        res = await session.execute(
            text(
                "SELECT amount, received_amount, payment_method_id "
                "FROM supplier_repayments WHERE purchase_id = :pid"
            ),
            {"pid": pid},
        )
        row = res.mappings().first()

    assert Decimal(str(row["amount"])) == Decimal("50.00")
    assert Decimal(str(row["received_amount"])) == Decimal("30.00")
    assert row["payment_method_id"] == 2


@pytest.mark.asyncio
async def test_c_multiple_partial_repayments_accumulate_and_respect_ceiling(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Test C: Multiple repayments accumulate received_amount and enforce ceiling."""
    h = await _owner_headers(app, owner_user)
    pid, line_id = await _create_posted_and_paid_purchase(app, h, qty="10.000", price="10.00", paid="80.00")
    # Total = 100.00, Paid = 80.00 -> remaining AP = 20.00.
    # Return 5 units @ 10.00 = 50.00.
    # AP absorbs 20.00 -> SREC created = 30.00.
    res_ret = await app.post(
        f"/api/v1/purchases/{pid}/returns",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": "*"},
        json={
            "reason": "partial return",
            "lines": [{"purchase_line_id": line_id, "quantity": "5.000"}],
        },
    )
    assert res_ret.status_code == 201, res_ret.text

    # First repayment: 10.00 via method 2
    p1 = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    rep1 = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": p1.headers["etag"]},
        json={"purchase_id": pid, "amount": 10.00, "payment_method_id": 2},
    )
    assert rep1.status_code == 201
    assert rep1.json()["received_amount"] == 10.00

    # Second repayment: 15.00 via method 2 -> cumulative = 25.00.
    # We avoid method 1 (cash) because the system requires sufficient cash
    # balance for cash repayments, and we have not seeded any in this test.
    p2 = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    rep2 = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": p2.headers["etag"]},
        json={"purchase_id": pid, "amount": 15.00, "payment_method_id": 2},
    )
    assert rep2.status_code == 201
    assert rep2.json()["received_amount"] == 25.00

    # Remaining SREC = 30 - 25 = 5.00. Attempting to repay 10.00 must be rejected with 409
    p3 = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    rep_excess = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": p3.headers["etag"]},
        json={"purchase_id": pid, "amount": 10.00, "payment_method_id": 2},
    )
    assert rep_excess.status_code == 409
    assert "exceeds" in rep_excess.json()["error"]["message"].lower()

    # Final 5.00 repayment succeeds and completes the obligation
    rep3 = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": p3.headers["etag"]},
        json={"purchase_id": pid, "amount": 5.00, "payment_method_id": 2},
    )
    assert rep3.status_code == 201
    assert rep3.json()["received_amount"] == 30.00


@pytest.mark.asyncio
async def test_d_zero_cash_received_state_does_not_affect_cash_movements(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Test D: Return-created SREC obligation does not insert cash_movements."""
    h = await _owner_headers(app, owner_user)

    factory = get_session_factory()
    async with factory() as session:
        res_before = await session.execute(
            text("SELECT COUNT(*) AS c, COALESCE(SUM(amount), 0) AS total FROM cash_movements")
        )
        before = res_before.mappings().first()

    pid, line_id = await _create_posted_and_paid_purchase(app, h, qty="10.000", price="10.00", paid="100.00")

    # Return goods creating SREC
    res_ret = await app.post(
        f"/api/v1/purchases/{pid}/returns",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": "*"},
        json={
            "reason": "defective",
            "lines": [{"purchase_line_id": line_id, "quantity": "3.000"}],
        },
    )
    assert res_ret.status_code == 201

    # Verify cash_movements has NO supplier_repayment entry
    async with factory() as session:
        res_sr_movements = await session.execute(
            text(
                "SELECT COUNT(*) AS c FROM cash_movements "
                "WHERE trigger = 'supplier_repayment' OR reference_type = 'supplier_repayment'"
            )
        )
        sr_count = res_sr_movements.scalar()

    assert sr_count == 0, f"Expected 0 supplier_repayment cash movements, found {sr_count}"


@pytest.mark.asyncio
async def test_e_payment_method_filtering_does_not_classify_unpaid_srec_as_cash(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Test E: GET /supplier-repayments?filter[payment_method_id]=1 does not return unpaid SREC obligation."""
    h = await _owner_headers(app, owner_user)
    pid, line_id = await _create_posted_and_paid_purchase(app, h, qty="10.000", price="10.00", paid="100.00")

    # Create return creating SREC obligation row with payment_method_id = NULL
    res_ret = await app.post(
        f"/api/v1/purchases/{pid}/returns",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": "*"},
        json={
            "reason": "return",
            "lines": [{"purchase_line_id": line_id, "quantity": "5.000"}],
        },
    )
    assert res_ret.status_code == 201

    # Filter by payment_method_id = 1 (cash). Unpaid SREC obligation has
    # payment_method_id = NULL and must NOT be returned by a cash filter.
    res_cash_filter = await app.get(
        f"/api/v1/supplier-repayments?purchase_id={pid}&payment_method_id=1",
        headers=h,
    )
    assert res_cash_filter.status_code == 200
    items = res_cash_filter.json()["data"]
    assert len(items) == 0, f"Unpaid SREC obligation was incorrectly classified as cash: {items}"

    # Verify listing without filter returns the obligation row with payment_method_id = None / null
    res_unfiltered = await app.get(
        f"/api/v1/supplier-repayments?purchase_id={pid}",
        headers=h,
    )
    assert res_unfiltered.status_code == 200
    all_items = res_unfiltered.json()["data"]
    assert len(all_items) == 1
    assert all_items[0]["payment_method_id"] is None
    assert all_items[0]["received_amount"] == 0.0
    assert all_items[0]["amount"] == 50.0
