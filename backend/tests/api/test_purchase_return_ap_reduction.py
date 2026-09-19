"""Focused regression tests for AP reduction and payment ceiling on purchase returns.

Covers Scenarios A-E per the Phase E AP-reduction specification:
A. Unpaid purchase + return -> AP reduced, no SREC
B. Partial payment + return within AP -> AP reduced, payment ceiling enforced, no SREC
C. Partial payment + return exceeding remaining AP -> AP becomes 0, SREC created for excess
D. Fully paid + return -> AP is 0, full SREC created, no payment row created
E. Multiple returns -> AP sums posted returns; cancelled returns do not reduce AP
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient

from tests.api.test_purchases import (
    _idem,
    _owner_headers,
    _seed_supplier_and_product,
)


@pytest.fixture(autouse=True)
async def _ensure_seeds(app: AsyncClient) -> AsyncGenerator[None, None]:
    await _seed_supplier_and_product()
    yield


async def _create_posted_purchase(
    app: AsyncClient,
    headers: dict[str, str],
    *,
    quantity: str = "10.000",
    unit_price: str = "10.00",
) -> tuple[int, int]:
    """Create and post a purchase with one line. Default: 10 units @ 10.00 = 100.00."""
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
        json={"product_id": 1, "quantity": quantity, "unit_price": unit_price},
    )
    assert res_line.status_code == 201, res_line.text
    line_id = res_line.json()["id"]

    res_post = await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**headers, "Idempotency-Key": _idem()},
        json={},
    )
    assert res_post.status_code == 200, res_post.text
    return pid, line_id


async def _create_return(
    app: AsyncClient,
    headers: dict[str, str],
    pid: int,
    line_id: int,
    *,
    qty: str,
) -> int:
    res = await app.post(
        f"/api/v1/purchases/{pid}/returns",
        headers={**headers, "Idempotency-Key": _idem(), "If-Match": "*"},
        json={
            "reason": "defect",
            "lines": [{"purchase_line_id": line_id, "quantity": qty}],
        },
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


# ---------------------------------------------------------------------------
# Scenario A: Unpaid purchase + return
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_a_unpaid_purchase_return_reduces_ap(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Total 100, paid 0, return 30 -> outstanding/AP = 70, SREC = 0."""
    h = await _owner_headers(app, owner_user)
    pid, line_id = await _create_posted_purchase(app, h)

    # Return 3 units = 30.00
    await _create_return(app, h, pid, line_id, qty="3.000")

    # Verify purchase state
    res = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    assert res.status_code == 200
    data = res.json()
    assert Decimal(str(data["total_amount"])) == Decimal("100.00")
    assert Decimal(str(data["paid_amount"])) == Decimal("0.00")
    assert Decimal(str(data["outstanding"])) == Decimal("70.00")
    assert Decimal(str(data["ap"])) == Decimal("70.00")
    assert Decimal(str(data["supplier_receivable"])) == Decimal("0.00")

    # Assert no SREC row created
    res_srep = await app.get(f"/api/v1/supplier-repayments?purchase_id={pid}", headers=h)
    assert res_srep.status_code == 200
    items = res_srep.json().get("data", [])
    assert len(items) == 0


# ---------------------------------------------------------------------------
# Scenario B: Partial payment + return within AP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_b_partial_payment_return_within_ap(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Total 100, paid 40, return 30 -> outstanding/AP = 30, SREC = 0,
    payment ceiling is 30.
    """
    h = await _owner_headers(app, owner_user)
    pid, line_id = await _create_posted_purchase(app, h)

    # Pay 40.00
    res_pay = await app.post(
        f"/api/v1/purchases/{pid}/payments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"payment_method_id": 2, "amount": "40.00"},
    )
    assert res_pay.status_code == 201

    # Return 3 units = 30.00
    await _create_return(app, h, pid, line_id, qty="3.000")

    # Verify purchase state
    res = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    assert res.status_code == 200
    data = res.json()
    assert Decimal(str(data["total_amount"])) == Decimal("100.00")
    assert Decimal(str(data["paid_amount"])) == Decimal("40.00")
    assert Decimal(str(data["outstanding"])) == Decimal("30.00")
    assert Decimal(str(data["ap"])) == Decimal("30.00")
    assert Decimal(str(data["supplier_receivable"])) == Decimal("0.00")

    # Assert no SREC row created
    res_srep = await app.get(f"/api/v1/supplier-repayments?purchase_id={pid}", headers=h)
    assert res_srep.status_code == 200
    items = res_srep.json().get("data", [])
    assert len(items) == 0

    # Attempting payment > 30 is rejected with 409 allocation_exceeds_payable
    res_over = await app.post(
        f"/api/v1/purchases/{pid}/payments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"payment_method_id": 2, "amount": "31.00"},
    )
    assert res_over.status_code == 409
    assert res_over.json()["error"]["code"] == "allocation_exceeds_payable"

    # Payment of exact remaining 30.00 succeeds
    res_exact = await app.post(
        f"/api/v1/purchases/{pid}/payments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"payment_method_id": 2, "amount": "30.00"},
    )
    assert res_exact.status_code == 201
    final_data = res_exact.json()
    assert Decimal(str(final_data["paid_amount"])) == Decimal("70.00")
    assert Decimal(str(final_data["outstanding"])) == Decimal("0.00")
    assert Decimal(str(final_data["ap"])) == Decimal("0.00")
    assert final_data["payment_state"] == "paid"


# ---------------------------------------------------------------------------
# Scenario C: Partial payment + return exceeding remaining AP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_c_partial_payment_return_exceeds_ap(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Total 100, paid 60, return 50 -> AP = 0, SREC = 10,
    subsequent payments rejected.
    """
    h = await _owner_headers(app, owner_user)
    pid, line_id = await _create_posted_purchase(app, h)

    # Pay 60.00
    res_pay = await app.post(
        f"/api/v1/purchases/{pid}/payments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"payment_method_id": 2, "amount": "60.00"},
    )
    assert res_pay.status_code == 201

    # Return 5 units = 50.00 (remaining AP was 40, so AP reduction = 40, SREC = 10)
    await _create_return(app, h, pid, line_id, qty="5.000")

    # Verify purchase state
    res = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    assert res.status_code == 200
    data = res.json()
    assert Decimal(str(data["total_amount"])) == Decimal("100.00")
    assert Decimal(str(data["paid_amount"])) == Decimal("60.00")
    assert Decimal(str(data["outstanding"])) == Decimal("0.00")
    assert Decimal(str(data["ap"])) == Decimal("0.00")
    assert Decimal(str(data["supplier_receivable"])) == Decimal("10.00")

    # Verify SREC row created with amount = 10.00
    res_srep = await app.get(f"/api/v1/supplier-repayments?purchase_id={pid}", headers=h)
    assert res_srep.status_code == 200
    items = res_srep.json().get("data", [])
    assert len(items) == 1
    assert Decimal(str(items[0]["amount"])) == Decimal("10.00")
    assert items[0]["reason"] == "purchase_return_credit"

    # Attempting any additional payment is rejected
    res_over = await app.post(
        f"/api/v1/purchases/{pid}/payments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"payment_method_id": 2, "amount": "1.00"},
    )
    assert res_over.status_code == 409
    assert res_over.json()["error"]["code"] == "allocation_exceeds_payable"


# ---------------------------------------------------------------------------
# Scenario D: Fully paid + return
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_d_fully_paid_return_creates_srec_no_payment_rows(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Total 100, paid 100, return 40 -> AP = 0, SREC = 40,
    no purchase_payments row created for the return.
    """
    h = await _owner_headers(app, owner_user)
    pid, line_id = await _create_posted_purchase(app, h)

    # Pay 100.00 in full
    res_pay = await app.post(
        f"/api/v1/purchases/{pid}/payments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"payment_method_id": 2, "amount": "100.00"},
    )
    assert res_pay.status_code == 201

    # Return 4 units = 40.00
    await _create_return(app, h, pid, line_id, qty="4.000")

    # Verify purchase state
    res = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    assert res.status_code == 200
    data = res.json()
    assert Decimal(str(data["total_amount"])) == Decimal("100.00")
    assert Decimal(str(data["paid_amount"])) == Decimal("100.00")
    assert Decimal(str(data["outstanding"])) == Decimal("0.00")
    assert Decimal(str(data["ap"])) == Decimal("0.00")
    assert Decimal(str(data["supplier_receivable"])) == Decimal("40.00")

    # SREC created with amount = 40.00
    res_srep = await app.get(f"/api/v1/supplier-repayments?purchase_id={pid}", headers=h)
    assert res_srep.status_code == 200
    items = res_srep.json().get("data", [])
    assert len(items) == 1
    assert Decimal(str(items[0]["amount"])) == Decimal("40.00")

    # Assert no purchase_payments row created for the return (exactly 1 row exists)
    res_pays = await app.get(f"/api/v1/purchases/{pid}/payments", headers=h)
    assert res_pays.status_code == 200
    pays = res_pays.json() if isinstance(res_pays.json(), list) else res_pays.json().get("data", [])
    assert len(pays) == 1
    assert Decimal(str(pays[0]["amount"])) == Decimal("100.00")


# ---------------------------------------------------------------------------
# Scenario E: Multiple posted returns & cancelled returns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_e_multiple_returns_and_cancelled_returns(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """AP sums posted returns. Cancelled returns do NOT reduce AP."""
    h = await _owner_headers(app, owner_user)
    pid, line_id = await _create_posted_purchase(app, h)

    # Return 1: 2 units = 20.00 -> AP = 80
    await _create_return(app, h, pid, line_id, qty="2.000")

    res1 = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    assert Decimal(str(res1.json()["ap"])) == Decimal("80.00")

    # Return 2: 3 units = 30.00 -> AP = 50
    await _create_return(app, h, pid, line_id, qty="3.000")

    res2 = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    assert Decimal(str(res2.json()["ap"])) == Decimal("50.00")

    # Return 3: 1 unit = 10.00 -> AP becomes 40 temporarily
    ret3_id = await _create_return(app, h, pid, line_id, qty="1.000")
    res3 = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    assert Decimal(str(res3.json()["ap"])) == Decimal("40.00")

    # Cancel Return 3
    res_cancel = await app.post(
        f"/api/v1/purchase-returns/{ret3_id}/cancel",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": "*"},
        json={"reason": "mistake"},
    )
    assert res_cancel.status_code == 200

    # AP restores to 50.00 (cancelled return no longer reduces AP)
    res4 = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    assert Decimal(str(res4.json()["ap"])) == Decimal("50.00")
    assert Decimal(str(res4.json()["outstanding"])) == Decimal("50.00")

    # Payment of 51 is rejected
    res_over = await app.post(
        f"/api/v1/purchases/{pid}/payments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"payment_method_id": 2, "amount": "51.00"},
    )
    assert res_over.status_code == 409
    assert res_over.json()["error"]["code"] == "allocation_exceeds_payable"

    # Payment of 50 succeeds
    res_exact = await app.post(
        f"/api/v1/purchases/{pid}/payments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"payment_method_id": 2, "amount": "50.00"},
    )
    assert res_exact.status_code == 201
    assert Decimal(str(res_exact.json()["outstanding"])) == Decimal("0.00")
    assert Decimal(str(res_exact.json()["ap"])) == Decimal("0.00")
