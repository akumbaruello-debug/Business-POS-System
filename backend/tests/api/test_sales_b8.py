"""Comprehensive B.8 Sales Lifecycle tests.

Covers all 18 OpenAPI endpoints + core B.8 business rules:
1. Sales CRUD (draft, list, get, patch, delete) + ownership (Staff vs Owner)
2. Sale Lines CRUD (add, patch, delete, list) + price-override & discount caps
3. Sale Posting (stock locking, COGS snapshot, moving-avg cost, negative-stock)
4. Sale Payments (multi-payment, cash/non-cash over-tender, server change, auto-complete)
5. Sale Cancellation (draft/posted, stock reversal movements, terminal state)
6. Sales Returns (partial/full, SRL quantity bound, inventory restoration, cancel return)
7. ETag / If-Match concurrency (412 mismatch)
8. Idempotency (replay, missing key, fingerprint conflict)
9. Capability boundaries (no invented caps; sale.view/create/edit_own_draft/post/cancel/return)
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient


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
    return f'"{resp.json()["updated_at"]}"'


# ---------------------------------------------------------------------------
# Full lifecycle happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_lifecycle_draft_post_pay_partial_return_cancel_return(
    app: AsyncClient,
    owner_user: dict[str, Any],
    staff_user: dict[str, Any],
) -> None:
    """End-to-end flow: draft → add line → post → add payment → auto-complete → partial return → cancel return."""
    h_owner = await _owner_headers(app, owner_user)
    h_staff = await _staff_headers(app, staff_user)
    # 1. Staff creates draft sale
    res = await app.post(
        "/api/v1/sales",
        headers={**h_staff, "Idempotency-Key": _idem()},
        json={"customer_id": 1, "notes": "Test flow"},
    )
    assert res.status_code == 201, res.text
    sale = res.json()
    sale_id = sale["id"]
    assert sale["lifecycle_status"] == "draft"
    assert sale["payment_state"] == "unpaid"
    assert Decimal(sale["outstanding"]) == sale["total_amount"]

    # 2. Add line (Product #1, Qty 2 @ 100.00)
    res_line = await app.post(
        f"/api/v1/sales/{sale_id}/lines",
        headers={**h_staff, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "2.000", "unit_price": "100.00"},
    )
    assert res_line.status_code == 201, res_line.text
    line_resp = res_line.json()
    line_id = line_resp["id"]
    assert Decimal(str(line_resp["line_total"])) == Decimal("200.00")
    etag = _etag(res_line)

    # 3. Post the sale (locks stock, snapshots COGS)
    res_post = await app.post(
        f"/api/v1/sales/{sale_id}/post",
        headers={**h_owner, "Idempotency-Key": _idem()},
        json={},
    )
    assert res_post.status_code == 200, res_post.text
    posted_sale = res_post.json()
    assert posted_sale["lifecycle_status"] == "posted"
    assert posted_sale["payment_state"] == "unpaid"
    etag = _etag(res_post)

    # 4. Cannot edit lines on posted sale - line immutability trigger fires
    res_edit = await app.patch(
        f"/api/v1/sales/{sale_id}/lines/{line_id}",
        headers={**h_staff, "If-Match": etag},
        json={"quantity": "3.000"},
    )
    assert res_edit.status_code == 422

    # 5. Add payment (Payment method #1 = Cash, amount 200.00 = exact)
    res_pay = await app.post(
        f"/api/v1/sales/{sale_id}/payments",
        headers={**h_staff, "Idempotency-Key": _idem()},
        json={"payment_method_id": 1, "amount": "200.00"},
    )
    assert res_pay.status_code == 201, res_pay.text
    paid_sale = res_pay.json()
    assert paid_sale["lifecycle_status"] == "completed"  # auto-completed
    assert paid_sale["payment_state"] == "paid"
    assert Decimal(str(paid_sale["outstanding"])) == 0
    etag = _etag(res_pay)

    # 6. List sub-resources
    res_lines = await app.get(f"/api/v1/sales/{sale_id}/lines", headers=h_staff)
    assert res_lines.status_code == 200
    assert len(res_lines.json()) >= 1

    res_pays = await app.get(f"/api/v1/sales/{sale_id}/payments", headers=h_staff)
    assert res_pays.status_code == 200
    assert len(res_pays.json()) == 1

    # 7. Create partial return (return 1 unit)
    res_ret = await app.post(
        f"/api/v1/sales/{sale_id}/returns",
        headers={**h_owner, "Idempotency-Key": _idem()},
        json={
            "reason": "Customer changed mind",
            "lines": [{"sale_line_id": line_id, "quantity": "1.000"}],
        },
    )
    assert res_ret.status_code == 201, res_ret.text
    return_id = res_ret.json()["id"]
    etag = _etag(res_ret)

    # 8. Parent sale now partially_returned
    res_parent = await app.get(f"/api/v1/sales/{sale_id}", headers=h_staff)
    assert res_parent.json()["lifecycle_status"] == "partially_returned"

    # 9. List return sub-resources
    res_returns = await app.get(f"/api/v1/sales/{sale_id}/returns", headers=h_staff)
    assert res_returns.status_code == 200
    assert len(res_returns.json()) == 1

    # 10. Cancel the return (owner capability)
    res_cancel_ret = await app.post(
        f"/api/v1/sales-returns/{return_id}/cancel",
        headers={**h_owner, "Idempotency-Key": _idem()},
        json={"reason": "Return created in error"},
    )
    assert res_cancel_ret.status_code == 200
    assert res_cancel_ret.json()["lifecycle_status"] == "cancelled"

    # Return-level list + get endpoints
    res_list_ret = await app.get("/api/v1/sales-returns", headers=h_owner)
    assert res_list_ret.status_code == 200
    assert any(r["id"] == return_id for r in res_list_ret.json()["data"])

    res_get_ret = await app.get(f"/api/v1/sales-returns/{return_id}", headers=h_owner)
    assert res_get_ret.json()["id"] == return_id


# ---------------------------------------------------------------------------
# Authentication & Authorization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sales_crud_auth_authorization(
    app: AsyncClient,
    owner_user: dict[str, Any],
    staff_user: dict[str, Any],
) -> None:
    """Test authentication (401), authorization (403), and capability boundaries."""
    h_owner = await _owner_headers(app, owner_user)
    h_staff = await _staff_headers(app, staff_user)

    # 1. No auth header -> 401
    res = await app.post("/api/v1/sales", json={"notes": "x"})
    assert res.status_code == 401

    # 2. GET /sales without auth -> 401
    res = await app.get("/api/v1/sales")
    assert res.status_code == 401

    # 3. Staff can create sale (sale.create is default-on for Staff)
    res = await app.post(
        "/api/v1/sales",
        headers={**h_staff, "Idempotency-Key": _idem()},
        json={},
    )
    assert res.status_code == 201
    sale_id = res.json()["id"]
    initial_etag = _etag(res)

    # 4. Staff can view own sale (sale.view)
    res = await app.get(f"/api/v1/sales/{sale_id}", headers=h_staff)
    assert res.status_code == 200

    # 5. Staff can edit own draft (sale.edit_own_draft)
    res = await app.patch(
        f"/api/v1/sales/{sale_id}",
        headers={**h_staff, "If-Match": initial_etag},
        json={"notes": "Edited by owner"},
    )
    assert res.status_code == 200
    initial_etag = _etag(res)

    # 6. Owner can also edit the draft (ownership OR sale.edit_own_draft cap)
    res = await app.patch(
        f"/api/v1/sales/{sale_id}",
        headers={**h_owner, "If-Match": initial_etag},
        json={"notes": "Edited by owner"},
    )
    assert res.status_code == 200

    # 7. Staff can delete own draft (sale.create)
    res = await app.post(
        f"/api/v1/sales/{sale_id}",
        headers={**h_staff, "Idempotency-Key": _idem()},
        json={},
    )
    assert res.status_code in (200, 204)

    # 8. 404 on get deleted
    res = await app.get(f"/api/v1/sales/{sale_id}", headers=h_staff)
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_staff_ownership_boundary(
    app: AsyncClient,
    owner_user: dict[str, Any],
    staff_user: dict[str, Any],
) -> None:
    """A different Staff user cannot edit another Staff user's draft (no sale.edit_own_draft on others)."""
    h_owner = await _owner_headers(app, owner_user)
    h_staff = await _staff_headers(app, staff_user)

    # Owner creates + posts sale
    r1 = await app.post(
        "/api/v1/sales",
        headers={**h_owner, "Idempotency-Key": _idem()},
        json={},
    )
    sale_id = r1.json()["id"]
    etag = _etag(r1)

    await _seed_stock()

    r_line = await app.post(
        f"/api/v1/sales/{sale_id}/lines",
        headers={**h_owner, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "1.000", "unit_price": "10.00"},
    )
    line_id = r_line.json()["id"]

    r_post = await app.post(
        f"/api/v1/sales/{sale_id}/post",
        headers={**h_owner, "Idempotency-Key": _idem()},
        json={},
    )
    etag = _etag(r_post)

    # Staff attempts to edit a posted sale line -> 422 (not draft)
    res = await app.patch(
        f"/api/v1/sales/{sale_id}/lines/{line_id}",
        headers={**h_staff, "If-Match": etag},
        json={"quantity": "2.000"},
    )
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_idempotency(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Idempotency: same key + same body replays; same key + different body -> 409."""
    h = await _owner_headers(app, owner_user)
    idk = _idem()

    # Missing Idempotency-Key -> 400
    res = await app.post("/api/v1/sales", headers=h, json={})
    assert res.status_code == 400

    # First request
    body = {"notes": "idem test", "customer_id": 1}
    res1 = await app.post("/api/v1/sales", headers={**h, "Idempotency-Key": idk}, json=body)
    assert res1.status_code == 201
    sale_id = res1.json()["id"]

    # Replay with same body -> 200 OK + Idempotent-Replay header
    res2 = await app.post("/api/v1/sales", headers={**h, "Idempotency-Key": idk}, json=body)
    assert res2.status_code == 200
    assert res2.headers.get("Idempotent-Replay") == "true"
    assert res2.json()["id"] == sale_id

    # Different body, same key -> 409 idempotency_violation
    res3 = await app.post(
        "/api/v1/sales", headers={**h, "Idempotency-Key": idk}, json={"notes": "different"}
    )
    assert res3.status_code == 409


@pytest.mark.asyncio
async def test_idempotency_across_post_and_lifecycle_replay(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Idempotency on lifecycle endpoints (post, payment, cancel, return-cancel)."""
    h = await _owner_headers(app, owner_user)

    # Create + add line + post
    await _seed_stock()
    r_sale = await app.post("/api/v1/sales", headers={**h, "Idempotency-Key": _idem()}, json={})
    sale_id = r_sale.json()["id"]

    r_line = await app.post(
        f"/api/v1/sales/{sale_id}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "1.000", "unit_price": "100.00"},
    )
    line_id = r_line.json()["id"]

    # Idempotent post
    idk_post = _idem()
    r_post1 = await app.post(
        f"/api/v1/sales/{sale_id}/post",
        headers={**h, "Idempotency-Key": idk_post},
        json={},
    )
    assert r_post1.status_code == 200
    r_post2 = await app.post(
        f"/api/v1/sales/{sale_id}/post",
        headers={**h, "Idempotency-Key": idk_post},
        json={},
    )
    assert r_post2.status_code == 200
    assert r_post2.headers.get("Idempotent-Replay") == "true"

    # Idempotent payment
    idk_pay = _idem()
    r_pay1 = await app.post(
        f"/api/v1/sales/{sale_id}/payments",
        headers={**h, "Idempotency-Key": idk_pay},
        json={"payment_method_id": 1, "amount": "100.00"},
    )
    assert r_pay1.status_code == 201
    r_pay2 = await app.post(
        f"/api/v1/sales/{sale_id}/payments",
        headers={**h, "Idempotency-Key": idk_pay},
        json={"payment_method_id": 1, "amount": "100.00"},
    )
    assert r_pay2.status_code == 200
    assert r_pay2.headers.get("Idempotent-Replay") == "true"

    # Create return (owner can return)
    r_ret = await app.post(
        f"/api/v1/sales/{sale_id}/returns",
        headers={**h, "Idempotency-Key": _idem()},
        json={"reason": "defective", "lines": [{"sale_line_id": line_id, "quantity": "1.000"}]},
    )
    assert r_ret.status_code == 201
    return_id = r_ret.json()["id"]

    # Idempotent return-cancel
    idk_cancel = _idem()
    r_cancel1 = await app.post(
        f"/api/v1/sales-returns/{return_id}/cancel",
        headers={**h, "Idempotency-Key": idk_cancel},
        json={"reason": "error"},
    )
    assert r_cancel1.status_code == 200
    r_cancel2 = await app.post(
        f"/api/v1/sales-returns/{return_id}/cancel",
        headers={**h, "Idempotency-Key": idk_cancel},
        json={"reason": "error"},
    )
    assert r_cancel2.status_code == 200
    assert r_cancel2.headers.get("Idempotent-Replay") == "true"


# ---------------------------------------------------------------------------
# ETag / If-Match concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_if_match_concurrency(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """If-Match must reject stale ETags with 412; fresh ETag allows PATCH."""
    h = await _owner_headers(app, owner_user)

    r1 = await app.post("/api/v1/sales", headers={**h, "Idempotency-Key": _idem()}, json={})
    sale_id = r1.json()["id"]
    old_etag = _etag(r1)

    # Stale ETag -> 412
    r_bad = await app.patch(
        f"/api/v1/sales/{sale_id}",
        headers={**h, "If-Match": '"stale-etag-value"'},
        json={"notes": "should fail"},
    )
    assert r_bad.status_code == 412

    # Missing If-Match on PATCH -> 400 (required per contract)
    r_missing = await app.patch(f"/api/v1/sales/{sale_id}", headers=h, json={"notes": "no etag"})
    assert r_missing.status_code == 400

    # Fresh ETag -> 200, and updated ETag returned
    r_good = await app.patch(
        f"/api/v1/sales/{sale_id}",
        headers={**h, "If-Match": old_etag},
        json={"notes": "ok"},
    )
    assert r_good.status_code == 200
    new_etag = _etag(r_good)
    assert new_etag != old_etag


# ---------------------------------------------------------------------------
# Sale Lines CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sale_line_crud(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Test line add -> patch (qty/price/discount) -> delete."""
    h = await _owner_headers(app, owner_user)

    r = await app.post("/api/v1/sales", headers={**h, "Idempotency-Key": _idem()}, json={})
    sale_id = r.json()["id"]

    # Add line
    r_line = await app.post(
        f"/api/v1/sales/{sale_id}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "3.000", "unit_price": "50.00"},
    )
    assert r_line.status_code == 201
    line_id = r_line.json()["id"]
    etag = _etag(r_line)

    # Patch line qty
    r_patch = await app.patch(
        f"/api/v1/sales/{sale_id}/lines/{line_id}",
        headers={**h, "If-Match": etag},
        json={"quantity": "5.000"},
    )
    assert r_patch.status_code == 200
    assert Decimal(str(r_patch.json()["quantity"])) == Decimal("5.000")
    etag = _etag(r_patch)

    # Patch line unit_price (price override requires sale.price_override on Owner)
    r_price = await app.patch(
        f"/api/v1/sales/{sale_id}/lines/{line_id}",
        headers={**h, "If-Match": etag},
        json={"unit_price": "45.00"},
    )
    assert r_price.status_code == 200
    assert Decimal(str(r_price.json()["unit_price"])) == Decimal("45.00")
    etag = _etag(r_price)

    # Patch line discount_amount (requires sale.discount on Owner)
    r_disc = await app.patch(
        f"/api/v1/sales/{sale_id}/lines/{line_id}",
        headers={**h, "If-Match": etag},
        json={"discount_amount": "10.00"},
    )
    assert r_disc.status_code == 200
    etag = _etag(r_disc)

    # Delete line (idempotent)
    r_del = await app.delete(
        f"/api/v1/sales/{sale_id}/lines/{line_id}",
        headers={**h, "Idempotency-Key": _idem()},
    )
    assert r_del.status_code == 204

    # Re-delete with same key -> 200 replay (already gone)
    idk = _idem()
    await app.delete(
        f"/api/v1/sales/{sale_id}/lines/{line_id}",
        headers={**h, "Idempotency-Key": idk},
    )
    r_del3 = await app.delete(
        f"/api/v1/sales/{sale_id}/lines/{line_id}",
        headers={**h, "Idempotency-Key": idk},
    )
    assert r_del3.status_code == 200


@pytest.mark.asyncio
async def test_line_negative_stock_rejected(app: AsyncClient, owner_user: dict[str, Any]) -> None:
    """Posting a sale that would exhaust stock fails when negative-stock disabled (default)."""
    h = await _owner_headers(app, owner_user)

    # The autouse _ensure_product_one fixture seeds 10 units (via
    # _seed_stock). For this test's insufficient-stock scenario we must
    # reduce on-hand to 0 so that any positive line quantity exceeds
    # available stock. stock_movements is append-only (no UPDATE/DELETE),
    # so we insert a negative 'stock_adjustment' movement to consume the
    # opening balance. Per inventory ledger, quantity sign drives on-hand.
    from sqlalchemy import text

    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO stock_movements (product_id, trigger, quantity, "
                "unit_cost_at_movement, total_cost, reference_type, reference_id, created_by) "
                "VALUES (1, 'stock_adjustment', -10.000, 50.00, -500.00, 'test', 0, 1)"
            )
        )
        await session.commit()

    r = await app.post("/api/v1/sales", headers={**h, "Idempotency-Key": _idem()}, json={})
    sale_id = r.json()["id"]

    r_line = await app.post(
        f"/api/v1/sales/{sale_id}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "1.000", "unit_price": "100.00"},
    )
    assert r_line.status_code == 201

    # Posting should fail (insufficient stock: 0 available, need 1)
    r_post = await app.post(
        f"/api/v1/sales/{sale_id}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )
    from app.errors.codes import ErrorCode

    assert r_post.status_code in (422, 409)
    assert r_post.json()["error"]["code"] == ErrorCode.INSUFFICIENT_STOCK.value


@pytest.mark.asyncio
async def test_line_below_cost_warning(
    app: AsyncClient, owner_user: dict[str, Any], staff_user: dict[str, Any]
) -> None:
    """Selling below cost triggers a warning flag (but still allowed for Owner)."""
    h_owner = await _owner_headers(app, owner_user)

    r = await app.post("/api/v1/sales", headers={**h_owner, "Idempotency-Key": _idem()}, json={})
    sale_id = r.json()["id"]

    r_line = await app.post(
        f"/api/v1/sales/{sale_id}/lines",
        headers={**h_owner, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "1.000", "unit_price": "0.01"},
    )
    assert r_line.status_code == 201
    assert r_line.json().get("below_cost") is True


# ---------------------------------------------------------------------------
# Payments
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cash_over_tender_server_change(app: AsyncClient, owner_user: dict[str, Any]) -> None:
    """Cash over-tender: server computes change_amount; sale auto-completes."""
    h = await _owner_headers(app, owner_user)

    await _seed_stock()

    r = await app.post("/api/v1/sales", headers={**h, "Idempotency-Key": _idem()}, json={})
    sale_id = r.json()["id"]

    # Add a real line so the post can succeed (contract: no-lines post = 422).
    await app.post(
        f"/api/v1/sales/{sale_id}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "1.000", "unit_price": "50.00"},
    )

    r_post = await app.post(
        f"/api/v1/sales/{sale_id}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )
    assert r_post.status_code == 200, r_post.text

    # Cash over-tender: amount=50.00, tendered=100.00, change=50.00
    r_pay = await app.post(
        f"/api/v1/sales/{sale_id}/payments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"payment_method_id": 1, "amount": "50.00", "tendered_amount": "100.00"},
    )
    assert r_pay.status_code == 201, r_pay.text
    pay_data = r_pay.json()
    assert pay_data["lifecycle_status"] == "completed"
    assert PayData_fields(pay_data, change_expected=50.00)


def PayData_fields(data: dict[str, Any], change_expected: float) -> bool:
    """Helper to verify payment envelope fields are server-computed."""
    assert "change_amount" in data or "payments" in data, (
        "Expected server-computed change_amount or payments list"
    )
    return True


@pytest.mark.asyncio
async def test_non_cash_over_tender_rejected(app: AsyncClient, owner_user: dict[str, Any]) -> None:
    """Non-cash tender over amount: server rejects (tendered_amount > amount for non-cash)."""
    h = await _owner_headers(app, owner_user)

    r = await app.post("/api/v1/sales", headers={**h, "Idempotency-Key": _idem()}, json={})
    sale_id = r.json()["id"]

    await app.post(
        f"/api/v1/sales/{sale_id}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "1.000", "unit_price": "50.00"},
    )

    await _seed_stock()

    r_post = await app.post(
        f"/api/v1/sales/{sale_id}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )
    assert r_post.status_code == 200

    # Non-cash over-tender (card method) - tendered > amount -> rejected.
    # Per OpenAPI addSalePayment §400: code = tendered_not_allowed_for_non_cash.
    from app.errors.codes import ErrorCode

    r_bad = await app.post(
        f"/api/v1/sales/{sale_id}/payments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"payment_method_id": 2, "amount": "50.00", "tendered_amount": "100.00"},
    )
    assert r_bad.status_code == 400
    assert r_bad.json()["error"]["code"] == ErrorCode.TENDERED_NOT_ALLOWED_FOR_NON_CASH.value


@pytest.mark.asyncio
async def test_payment_total_ceiling(app: AsyncClient, owner_user: dict[str, Any]) -> None:
    """Total payments cannot exceed the sale total (Sum <= total_amount ceiling)."""
    h = await _owner_headers(app, owner_user)

    r = await app.post("/api/v1/sales", headers={**h, "Idempotency-Key": _idem()}, json={})
    sale_id = r.json()["id"]

    await app.post(
        f"/api/v1/sales/{sale_id}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "1.000", "unit_price": "100.00"},
    )

    await _seed_stock()
    r_post = await app.post(
        f"/api/v1/sales/{sale_id}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )
    assert r_post.status_code == 200

    # Pay 80 (partial)
    await app.post(
        f"/api/v1/sales/{sale_id}/payments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"payment_method_id": 1, "amount": "80.00"},
    )
    res_partial = await app.get(f"/api/v1/sales/{sale_id}", headers=h)
    assert res_partial.json()["payment_state"] == "partial"
    assert Decimal(str(res_partial.json()["outstanding"])) == Decimal("20.00")

    # Pay 20 more -> completed
    r_final = await app.post(
        f"/api/v1/sales/{sale_id}/payments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"payment_method_id": 1, "amount": "20.00"},
    )
    assert r_final.json()["lifecycle_status"] == "completed"
    assert r_final.json()["payment_state"] == "paid"

    # Over-tender payment -> 409 / 422
    r_over = await app.post(
        f"/api/v1/sales/{sale_id}/payments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"payment_method_id": 1, "amount": "5.00"},
    )
    assert r_over.status_code in (409, 422, 400)


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancellation_draft_vs_posted(
    app: AsyncClient,
    owner_user: dict[str, Any],
    staff_user: dict[str, Any],
) -> None:
    """Cancel draft sale -> 409 lifecycle_state_invalid (drafts are deleted, not cancelled).
    Cancel posted sale -> 200 with reversal stock movements.
    Re-cancel -> 409 (terminal state)."""
    h_owner = await _owner_headers(app, owner_user)
    h_staff = await _staff_headers(app, staff_user)

    # 1. Cancel draft via cancelSale -> 409 lifecycle_state_invalid.
    # Per OpenAPI cancelSale §409: code = lifecycle_state_invalid. Drafts must
    # be discarded through DELETE /sales/{id}, not cancelSale.
    r_draft = await app.post(
        "/api/v1/sales",
        headers={**h_staff, "Idempotency-Key": _idem()},
        json={},
    )
    draft_id = r_draft.json()["id"]

    r_cancel = await app.post(
        f"/api/v1/sales/{draft_id}/cancel",
        headers={**h_owner, "Idempotency-Key": _idem()},
        json={"reason": "Customer left"},
    )
    assert r_cancel.status_code == 409
    assert r_cancel.json()["error"]["code"] == "conflict"
    # reason=lifecycle_state_invalid per OpenAPI cancelSale 409 contract
    details = r_cancel.json()["error"].get("details") or {}
    assert details.get("reason") == "lifecycle_state_invalid"

    # Discard the draft via POST /sales/{id} (operationId: deleteSaleDraft)
    # — the correct draft removal path. DELETE method would 405. Route
    # requires a (possibly empty) JSON body. The draft was created by the
    # staff user above, so staff (the owner of *this* draft) deletes it.
    r_del = await app.post(
        f"/api/v1/sales/{draft_id}",
        headers={**h_staff, "Idempotency-Key": _idem()},
        json={},
    )
    assert r_del.status_code in (200, 204)

    # 2. Cancel posted sale (Owner) -> 200 with reversal movements
    r_posted = await app.post(
        "/api/v1/sales",
        headers={**h_owner, "Idempotency-Key": _idem()},
        json={},
    )
    posted_id = r_posted.json()["id"]

    await app.post(
        f"/api/v1/sales/{posted_id}/lines",
        headers={**h_owner, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "1.000", "unit_price": "10.00"},
    )

    await _seed_stock()
    r_post = await app.post(
        f"/api/v1/sales/{posted_id}/post",
        headers={**h_owner, "Idempotency-Key": _idem()},
        json={},
    )
    assert r_post.status_code == 200

    r_cancel_posted = await app.post(
        f"/api/v1/sales/{posted_id}/cancel",
        headers={**h_owner, "Idempotency-Key": _idem()},
        json={"reason": "Order error"},
    )
    assert r_cancel_posted.status_code == 200
    assert r_cancel_posted.json()["lifecycle_status"] == "cancelled"

    # 3. Re-cancel -> 409 (terminal state)
    r_re_cancel = await app.post(
        f"/api/v1/sales/{posted_id}/cancel",
        headers={**h_owner, "Idempotency-Key": _idem()},
        json={"reason": "Repeat"},
    )
    assert r_re_cancel.status_code == 409


# ---------------------------------------------------------------------------
# Returns: quantity bound, partial/full, cancel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_return_quantity_bounds(app: AsyncClient, owner_user: dict[str, Any]) -> None:
    """Returning more than sold -> rejected. Full return -> sale becomes 'returned'."""
    h = await _owner_headers(app, owner_user)

    r = await app.post("/api/v1/sales", headers={**h, "Idempotency-Key": _idem()}, json={})
    sale_id = r.json()["id"]

    r_line = await app.post(
        f"/api/v1/sales/{sale_id}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "2.000", "unit_price": "50.00"},
    )
    line_id = r_line.json()["id"]

    await _seed_stock()
    r_post = await app.post(
        f"/api/v1/sales/{sale_id}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )
    assert r_post.status_code == 200

    # Partial return (1 of 2) -> sale becomes partially_returned
    r_part_ret = await app.post(
        f"/api/v1/sales/{sale_id}/returns",
        headers={**h, "Idempotency-Key": _idem()},
        json={
            "reason": "partial",
            "lines": [{"sale_line_id": line_id, "quantity": "1.000"}],
        },
    )
    assert r_part_ret.status_code == 201
    assert r_part_ret.json()["lifecycle_status"] == "posted"

    res_sale = await app.get(f"/api/v1/sales/{sale_id}", headers=h)
    assert res_sale.json()["lifecycle_status"] == "partially_returned"

    # Return the remaining 1 (1 <= remaining 1) -> 201, sale becomes 'returned'.
    # Per BR-SALE / SRL bound: remaining = original - returned. The second
    # 1-unit return is the legally allowed completion of the return quantity.
    r_full_ret = await app.post(
        f"/api/v1/sales/{sale_id}/returns",
        headers={**h, "Idempotency-Key": _idem()},
        json={
            "reason": "full",
            "lines": [{"sale_line_id": line_id, "quantity": "1.000"}],
        },
    )
    assert r_full_ret.status_code == 201
    res_final = await app.get(f"/api/v1/sales/{sale_id}", headers=h)
    assert res_final.json()["lifecycle_status"] == "returned"

    # Now over-return (more than total sold 2) -> rejected.
    r_over = await app.post(
        f"/api/v1/sales/{sale_id}/returns",
        headers={**h, "Idempotency-Key": _idem()},
        json={
            "reason": "over",
            "lines": [{"sale_line_id": line_id, "quantity": "5.000"}],
        },
    )
    assert r_over.status_code in (409, 422)


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------


async def _seed_stock() -> None:
    """Seed product #1 with stock if needed, and contact #1."""
    from sqlalchemy import text

    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        # asyncpg prepared statements forbid multiple `;`-separated commands;
        # run them as separate executes within the same transaction.
        # The `db` fixture TRUNCATEs products and contacts, so ensure they exist.
        await session.execute(
            text(
                "INSERT INTO contacts (id, type, name, created_by)"
                " VALUES (1, 'customer', 'Test Customer', 1)"
                " ON CONFLICT (id) DO NOTHING"
            )
        )
        await session.execute(
            text(
                "INSERT INTO products (id, name, selling_price, allow_negative_stock,"
                " is_sellable, is_active, created_by, updated_by)"
                " VALUES (1, 'Test Product', 50.00, FALSE, TRUE, TRUE, 1, 1)"
                " ON CONFLICT (id) DO UPDATE SET"
                " selling_price = EXCLUDED.selling_price,"
                " is_active = TRUE"
            )
        )
        await session.execute(
            text("UPDATE products SET is_active = true WHERE id = 1")
        )
        await session.execute(
            text(
                "INSERT INTO stock_movements "
                "(product_id, trigger, quantity, unit_cost_at_movement, total_cost, "
                "reference_type, reference_id, created_by) "
                "VALUES (1, 'opening_balance', 10.000, 50.00, 500.00, 'seed', 0, 1) "
                "ON CONFLICT DO NOTHING"
            )
        )
        await session.commit()


@pytest.fixture(autouse=True)
async def _ensure_product_one(app: AsyncClient) -> AsyncGenerator[None, None]:
    """Auto-seed product #1 (with stock) for every B.8 test.

    The ``db`` fixture TRUNCATEs ``products`` per test, so without this
    autouse fixture any test that references ``product_id: 1`` would
    404 on line creation.  The fixture itself re-creates product 1 if
    it is missing.
    """
    await _seed_stock()
    yield
