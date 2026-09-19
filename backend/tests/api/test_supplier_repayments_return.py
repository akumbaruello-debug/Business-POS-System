"""Tests for return-created SREC obligations.

Covers:
- Two returns on same purchase create distinct supplier_repayments rows.
- Repayment specifying purchase_return_id updates only that row.
- Repayment without purchase_return_id still works if only one SREC exists.
- Ceiling enforcement per-return.
"""

import uuid
import pytest
from decimal import Decimal
from httpx import AsyncClient

from tests.api.test_purchases import (
    _etag,
    _owner_headers,
    _seed_supplier_and_product,
)

def _idem() -> str:
    return str(uuid.uuid4())

@pytest.fixture(autouse=True)
async def _ensure_seeds(app: AsyncClient) -> None:
    await _seed_supplier_and_product()

async def _create_purchase_with_two_returns(app: AsyncClient, headers: dict[str, str]):
    # create posted purchase of 20 units @ 10.00
    res = await app.post(
        "/api/v1/purchases",
        headers={**headers, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]
    # add line
    await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**headers, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "20.000", "unit_price": "10.00"},
    )
    res_post = await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**headers, "Idempotency-Key": _idem()},
        json={},
    )
    # fully pay the purchase (200.00) so returns produce SREC
    res_pay = await app.post(
        f"/api/v1/purchases/{pid}/payments",
        headers={**headers, "Idempotency-Key": _idem(), "If-Match": _etag(res_post)},
        json={"payment_method_id": 2, "amount": 200.00},
    )
    assert res_pay.status_code == 201
    # first return of 5 units (5 units @ 10 = 50, paid 200, outstanding 0, SREC = 50)
    res_ret_a = await app.post(
        f"/api/v1/purchases/{pid}/returns",
        headers={**headers, "Idempotency-Key": _idem(), "If-Match": "*"},
        json={"reason": "damaged", "lines": [{"purchase_line_id": 1, "quantity": "5.000"}]},
    )
    assert res_ret_a.status_code == 201
    ret_a = res_ret_a.json()["id"]
    # second return of 3 units (3 units @ 10 = 30, SREC = 30)
    res_ret_b = await app.post(
        f"/api/v1/purchases/{pid}/returns",
        headers={**headers, "Idempotency-Key": _idem(), "If-Match": "*"},
        json={"reason": "defect", "lines": [{"purchase_line_id": 1, "quantity": "3.000"}]},
    )
    assert res_ret_b.status_code == 201
    ret_b = res_ret_b.json()["id"]
    return pid, ret_a, ret_b

@pytest.mark.asyncio
async def test_two_returns_distinct_srecs(app: AsyncClient, owner_user: dict):
    h = await _owner_headers(app, owner_user)
    pid, ret_a, ret_b = await _create_purchase_with_two_returns(app, h)
    # list supplier repayments for purchase
    list_res = await app.get(
        "/api/v1/supplier-repayments",
        params={"filter[purchase_id]": pid},
        headers=h,
    )
    rows = list_res.json()["data"]
    assert len(rows) == 2
    # each row must have its purchase_return_id set appropriately
    ids = {row["purchase_return_id"] for row in rows}
    assert ids == {ret_a, ret_b}

@pytest.mark.asyncio
async def test_repayment_targets_specific_return(app: AsyncClient, owner_user: dict):
    h = await _owner_headers(app, owner_user)
    pid, ret_a, ret_b = await _create_purchase_with_two_returns(app, h)
    res_p = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    # repay 5 against return A
    repay_res = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": _etag(res_p)},
        json={
            "purchase_id": pid,
            "purchase_return_id": ret_a,
            "amount": 5,
            "payment_method_id": 2,
        },
    )
    assert repay_res.status_code == 201
    # fetch rows again
    rows = (await app.get(
        "/api/v1/supplier-repayments",
        params={"filter[purchase_id]": pid},
        headers=h,
    )).json()["data"]
    a_row = next(r for r in rows if r["purchase_return_id"] == ret_a)
    b_row = next(r for r in rows if r["purchase_return_id"] == ret_b)
    assert a_row["received_amount"] == 5
    assert b_row["received_amount"] == 0

@pytest.mark.asyncio
async def test_ceiling_per_return(app: AsyncClient, owner_user: dict):
    h = await _owner_headers(app, owner_user)
    pid, ret_a, _ = await _create_purchase_with_two_returns(app, h)
    res_p = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    # first repayment 30 (allowed, return A is 50)
    rep1 = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": _etag(res_p)},
        json={"purchase_id": pid, "purchase_return_id": ret_a, "amount": 30, "payment_method_id": 2},
    )
    assert rep1.status_code == 201

    res_p2 = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    # second repayment 30 (exceeds remaining 20 of return A) should error 422
    err = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": _etag(res_p2)},
        json={"purchase_id": pid, "purchase_return_id": ret_a, "amount": 30, "payment_method_id": 2},
    )
    assert err.status_code == 409
    # ensure received_amount stayed at 30
    rows = (await app.get(
        "/api/v1/supplier-repayments",
        params={"filter[purchase_id]": pid},
        headers=h,
    )).json()["data"]
    a_row = next(r for r in rows if r["purchase_return_id"] == ret_a)
    assert a_row["received_amount"] == 30
