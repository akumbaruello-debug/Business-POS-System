from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.db import get_session_factory


def _idem() -> str:
    return str(uuid.uuid4())


async def _owner_headers(app: AsyncClient, owner_user: dict[str, Any]) -> dict[str, str]:
    response = await app.post(
        "/api/v1/auth/login",
        json={"username": owner_user["username"], "password": owner_user["password"]},
        headers={"Idempotency-Key": _idem()},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _seed_product() -> None:
    async with get_session_factory()() as session:
        await session.execute(text(
            "INSERT INTO contacts(id,type,name,created_by) VALUES (1,'supplier','Supplier',1) ON CONFLICT(id) DO NOTHING"
        ))
        await session.execute(text(
            "INSERT INTO products(id,name,selling_price,allow_negative_stock,is_sellable,is_purchasable,is_active,created_by,updated_by) "
            "VALUES (1,'Fish',0,FALSE,TRUE,TRUE,TRUE,1,1) ON CONFLICT(id) DO UPDATE SET is_active=TRUE,is_purchasable=TRUE"
        ))
        await session.commit()


@pytest.mark.asyncio
async def test_purchase_cost_posting_inventory_ap_and_pnl_once(
    app: AsyncClient, owner_user: dict[str, Any],
) -> None:
    await _seed_product()
    headers = await _owner_headers(app, owner_user)
    expense_category_id = await _expense_category_id()
    suffix = uuid.uuid4().hex[:8]

    category_response = await app.post(
        "/api/v1/purchase-cost-categories",
        headers=headers,
        json={"name": f"Handling expense {suffix}", "capitalizable": False, "financial_category_id": expense_category_id},
    )
    assert category_response.status_code == 201, category_response.text
    expense_category = category_response.json()
    cap_response = await app.post(
        "/api/v1/purchase-cost-categories",
        headers=headers,
        json={"name": f"Acquisition cost {suffix}", "capitalizable": True},
    )
    assert cap_response.status_code == 201, cap_response.text

    purchase_response = await app.post(
        "/api/v1/purchases", headers={**headers, "Idempotency-Key": _idem()}, json={"supplier_id": 1},
    )
    assert purchase_response.status_code == 201, purchase_response.text
    purchase_id = purchase_response.json()["id"]
    line_response = await app.post(
        f"/api/v1/purchases/{purchase_id}/lines",
        headers={**headers, "Idempotency-Key": _idem(), "If-Match": purchase_response.headers["ETag"]},
        json={"product_id": 1, "quantity": "2", "unit_price": "500"},
    )
    assert line_response.status_code == 201, line_response.text
    purchase_response = await app.get(f"/api/v1/purchases/{purchase_id}?include=lines,shipping")
    etag = purchase_response.headers["ETag"]

    ship_response = await app.post(
        f"/api/v1/purchases/{purchase_id}/shipping",
        headers={**headers, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"amount": "100", "paid_in_cash": False},
    )
    assert ship_response.status_code == 200, ship_response.text
    purchase_response = await app.get(f"/api/v1/purchases/{purchase_id}?include=lines,shipping")
    etag = purchase_response.headers["ETag"]

    cap_cost = await app.post(
        f"/api/v1/purchases/{purchase_id}/costs",
        headers={**headers, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"category_id": cap_response.json()["id"], "amount": "20.00", "allocation_method": "line_value"},
    )
    assert cap_cost.status_code == 201, cap_cost.text
    purchase_response = await app.get(f"/api/v1/purchases/{purchase_id}?include=lines,shipping")
    etag = purchase_response.headers["ETag"]
    op_cost = await app.post(
        f"/api/v1/purchases/{purchase_id}/costs",
        headers={**headers, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"category_id": expense_category["id"], "amount": "50.00"},
    )
    assert op_cost.status_code == 201, op_cost.text
    assert op_cost.json()["allocation_method"] == "line_value"
    purchase_response = await app.get(f"/api/v1/purchases/{purchase_id}?include=lines,shipping")
    manual_cost = await app.post(
        f"/api/v1/purchases/{purchase_id}/costs",
        headers={**headers, "Idempotency-Key": _idem(), "If-Match": purchase_response.headers["ETag"]},
        json={"category_id": cap_response.json()["id"], "amount": "30.00", "allocation_method": "manual",
              "allocations": [{"purchase_line_id": purchase_response.json()["lines"][0]["id"], "amount": "30.00"}]},
    )
    assert manual_cost.status_code == 201, manual_cost.text
    assert manual_cost.json()["allocation_method"] == "manual"
    purchase_response = await app.get(f"/api/v1/purchases/{purchase_id}?include=lines,shipping")
    assert Decimal(str(purchase_response.json()["total_amount"])) == Decimal("1200.0")
    etag = purchase_response.headers["ETag"]

    posted = await app.post(
        f"/api/v1/purchases/{purchase_id}/post",
        headers={**headers, "Idempotency-Key": _idem(), "If-Match": etag}, json={},
    )
    assert posted.status_code == 200, posted.text

    async with get_session_factory()() as session:
        movement = (await session.execute(text(
            "SELECT unit_cost_at_movement,total_cost FROM stock_movements WHERE reference_type='purchase' AND reference_id=:pid AND trigger='purchase_receipt'",
        ), {"pid": purchase_id})).mappings().one()
        cost_snapshot = (await session.execute(text(
            "SELECT COUNT(*) AS n, SUM(amount) AS total FROM purchase_costs WHERE purchase_id=:pid AND posted_at IS NOT NULL AND NOT capitalizable_snapshot",
        ), {"pid": purchase_id})).mappings().one()
        assert Decimal(str(movement["total_cost"])) == Decimal("1150.00")
        assert Decimal(str(cost_snapshot["total"])) == Decimal("50.00")
        assert cost_snapshot["n"] == 1
        with pytest.raises(Exception, match="Posted purchase costs are immutable"):
            await session.execute(text("UPDATE purchase_costs SET amount=amount+1 WHERE purchase_id=:pid"), {"pid": purchase_id})
        await session.rollback()

    report_args = {"from": "2000-01-01T00:00:00Z", "to": "2100-01-01T00:00:00Z"}
    pnl = await app.get("/api/v1/reports/p-and-l", headers=headers, params=report_args)
    assert pnl.status_code == 200, pnl.text
    assert Decimal(str(pnl.json()["data"]["operating_expenses"])) == Decimal("50.0")

    async with get_session_factory()() as session:
        non_cash_id = (await session.execute(text(
            "SELECT id FROM payment_methods WHERE is_cash=FALSE AND is_active ORDER BY id LIMIT 1"
        ))).scalar_one()
    current = await app.get(f"/api/v1/purchases/{purchase_id}")
    paid = await app.post(
        f"/api/v1/purchases/{purchase_id}/payments",
        headers={**headers, "Idempotency-Key": _idem(), "If-Match": current.headers["ETag"]},
        json={"payment_method_id": non_cash_id, "amount": "1200.00"},
    )
    assert paid.status_code == 201, paid.text
    category_update = await app.patch(
        f"/api/v1/purchase-cost-categories/{expense_category['id']}",
        headers=headers,
        json={"name": "Reclassified for future purchases", "capitalizable": True, "financial_category_id": None},
    )
    assert category_update.status_code == 200, category_update.text
    pnl_after_payment = await app.get("/api/v1/reports/p-and-l", headers=headers, params=report_args)
    assert pnl_after_payment.status_code == 200, pnl_after_payment.text
    assert Decimal(str(pnl_after_payment.json()["data"]["operating_expenses"])) == Decimal("50.0")

    current = await app.get(f"/api/v1/purchases/{purchase_id}?include=lines")
    returned = await app.post(
        f"/api/v1/purchases/{purchase_id}/returns",
        headers={**headers, "Idempotency-Key": _idem(), "If-Match": current.headers["ETag"]},
        json={"reason": "return accounting verification", "lines": [
            {"purchase_line_id": current.json()["lines"][0]["id"], "quantity": "1"},
        ]},
    )
    assert returned.status_code == 201, returned.text
    async with get_session_factory()() as session:
        return_movement = (await session.execute(text(
            "SELECT total_cost FROM stock_movements WHERE reference_type='purchase_return' AND reference_id=:rid AND trigger='purchase_return'",
        ), {"rid": returned.json()["id"]})).mappings().one()
        assert Decimal(str(return_movement["total_cost"])) == Decimal("-575.00")
    pnl_after_return = await app.get("/api/v1/reports/p-and-l", headers=headers, params=report_args)
    assert pnl_after_return.status_code == 200, pnl_after_return.text
    assert Decimal(str(pnl_after_return.json()["data"]["operating_expenses"])) == Decimal("25.0")

    current = await app.get(f"/api/v1/purchases/{purchase_id}")
    delete_posted_cost = await app.delete(
        f"/api/v1/purchases/{purchase_id}/costs/{op_cost.json()['id']}",
        headers={**headers, "If-Match": current.headers["ETag"], "Idempotency-Key": _idem()},
    )
    assert delete_posted_cost.status_code == 409


@pytest.mark.asyncio
async def test_purchase_cost_default_is_owner_managed_and_categories_are_protected(
    app: AsyncClient,
    owner_user: dict[str, Any],
    staff_user: dict[str, Any],
) -> None:
    owner_headers = await _owner_headers(app, owner_user)
    current = await app.get("/api/v1/settings", headers=owner_headers)
    assert current.status_code == 200, current.text
    assert current.json()["settings"]["default_purchase_cost_allocation"] == "line_value"
    changed = await app.patch(
        "/api/v1/settings",
        headers={**owner_headers, "If-Match": current.headers["ETag"], "Idempotency-Key": _idem()},
        json={"default_purchase_cost_allocation": "manual"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["settings"]["default_purchase_cost_allocation"] == "manual"
    restored = await app.patch(
        "/api/v1/settings",
        headers={**owner_headers, "If-Match": changed.headers["ETag"], "Idempotency-Key": _idem()},
        json={"default_purchase_cost_allocation": "line_value"},
    )
    assert restored.status_code == 200, restored.text

    staff_headers = await _login_headers(app, staff_user)
    denied = await app.post(
        "/api/v1/purchase-cost-categories",
        headers=staff_headers,
        json={"name": f"blocked-{uuid.uuid4()}", "capitalizable": True},
    )
    assert denied.status_code == 403


@pytest.mark.asyncio
async def test_cancelled_purchase_reverses_posted_operating_cost_in_cancellation_period(
    app: AsyncClient, owner_user: dict[str, Any],
) -> None:
    await _seed_product()
    headers = await _owner_headers(app, owner_user)
    category_response = await app.post(
        "/api/v1/purchase-cost-categories",
        headers=headers,
        json={"name": f"Cancellation expense {uuid.uuid4().hex[:8]}", "capitalizable": False,
              "financial_category_id": await _expense_category_id()},
    )
    assert category_response.status_code == 201, category_response.text
    purchase_response = await app.post(
        "/api/v1/purchases", headers={**headers, "Idempotency-Key": _idem()}, json={"supplier_id": 1},
    )
    purchase_id = purchase_response.json()["id"]
    line_response = await app.post(
        f"/api/v1/purchases/{purchase_id}/lines",
        headers={**headers, "Idempotency-Key": _idem(), "If-Match": purchase_response.headers["ETag"]},
        json={"product_id": 1, "quantity": "1", "unit_price": "100"},
    )
    assert line_response.status_code == 201, line_response.text
    current = await app.get(f"/api/v1/purchases/{purchase_id}")
    added = await app.post(
        f"/api/v1/purchases/{purchase_id}/costs",
        headers={**headers, "Idempotency-Key": _idem(), "If-Match": current.headers["ETag"]},
        json={"category_id": category_response.json()["id"], "amount": "20"},
    )
    assert added.status_code == 201, added.text
    current = await app.get(f"/api/v1/purchases/{purchase_id}")
    posted = await app.post(
        f"/api/v1/purchases/{purchase_id}/post",
        headers={**headers, "Idempotency-Key": _idem(), "If-Match": current.headers["ETag"]}, json={},
    )
    assert posted.status_code == 200, posted.text
    report_args = {"from": "2000-01-01T00:00:00Z", "to": "2100-01-01T00:00:00Z"}
    before_cancel = await app.get("/api/v1/reports/p-and-l", headers=headers, params=report_args)
    assert Decimal(str(before_cancel.json()["data"]["operating_expenses"])) == Decimal("20.0")
    cancelled = await app.post(
        f"/api/v1/purchases/{purchase_id}/cancel",
        headers={**headers, "Idempotency-Key": _idem()},
        json={"reason": "cancellation accounting verification"},
    )
    assert cancelled.status_code == 200, cancelled.text
    after_cancel = await app.get("/api/v1/reports/p-and-l", headers=headers, params=report_args)
    assert Decimal(str(after_cancel.json()["data"]["operating_expenses"])) == Decimal("0.0")


async def _expense_category_id() -> int:
    async with get_session_factory()() as session:
        return int((await session.execute(text(
            "SELECT id FROM financial_categories WHERE entry_type='expense' AND is_active ORDER BY id LIMIT 1"
        ))).scalar_one())


async def _login_headers(app: AsyncClient, user: dict[str, Any]) -> dict[str, str]:
    response = await app.post(
        "/api/v1/auth/login",
        json={"username": user["username"], "password": user["password"]},
        headers={"Idempotency-Key": _idem()},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
