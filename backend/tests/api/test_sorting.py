"""Focused Phase 4 mixed-receipt and sorting integration coverage."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.db import get_session_factory

pytestmark = pytest.mark.integration


def _idem() -> str:
    return str(uuid.uuid4())


async def _owner_headers(app: AsyncClient, owner_user: dict[str, Any]) -> dict[str, str]:
    response = await app.post("/api/v1/auth/login", json={"username": owner_user["username"],
        "password": owner_user["password"]}, headers={"Idempotency-Key": _idem()})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _mass_unit(app: AsyncClient, headers: dict[str, str], code: str) -> dict[str, Any]:
    response = await app.post("/api/v1/units/", headers={**headers, "Idempotency-Key": _idem()},
        json={"code": code, "name": "Kilogram", "dimension": "mass", "factor_to_base": 1, "is_active": True})
    assert response.status_code == 201, response.text
    return response.json()


async def _product(app: AsyncClient, headers: dict[str, str], unit_id: int, name: str) -> dict[str, Any]:
    response = await app.post("/api/v1/products", headers={**headers, "Idempotency-Key": _idem()},
        json={"name": name, "unit_id": unit_id, "purchase_price": 0, "selling_price": 100,
              "is_active": True, "is_sellable": True, "is_purchasable": True})
    assert response.status_code == 201, response.text
    return response.json()


async def test_mixed_receipt_sorting_allocates_carrying_value_and_loss_once(
    app: AsyncClient, owner_user: dict[str, Any],
) -> None:
    headers = await _owner_headers(app, owner_user)
    kg = await _mass_unit(app, headers, f"kg_{uuid.uuid4().hex[:7]}")
    out_a = await _product(app, headers, kg["id"], "Grade A")
    out_b = await _product(app, headers, kg["id"], "Grade B")
    purchase = await app.post("/api/v1/purchases", headers={**headers, "Idempotency-Key": _idem()}, json={})
    assert purchase.status_code == 201, purchase.text
    purchase_id = purchase.json()["id"]
    line = await app.post(f"/api/v1/purchases/{purchase_id}/lines",
        headers={**headers, "If-Match": purchase.headers["ETag"], "Idempotency-Key": _idem()},
        json={"receipt_type": "unsorted", "unit_id": kg["id"], "quantity": "10", "unit_price": "100"})
    assert line.status_code == 201, line.text
    posted = await app.post(f"/api/v1/purchases/{purchase_id}/post",
        headers={**headers, "If-Match": line.headers["ETag"], "Idempotency-Key": _idem()}, json={})
    assert posted.status_code == 200, posted.text

    pending = await app.get("/api/v1/pending-receipts", headers=headers)
    assert pending.status_code == 200, pending.text
    batch = next(item for item in pending.json()["data"] if item["purchase_line_id"] == line.json()["id"])
    assert float(batch["available_quantity"]) == 10
    assert float(batch["inventory_value"]) == 1000
    assert float(batch["original_value"]) == 1000
    assert float(batch["factor_to_base_snapshot"]) == 1
    async with get_session_factory()() as session:
        sellable_stock = await session.execute(text(
            "SELECT on_hand_quantity FROM product_valuation WHERE product_id=:id"), {"id": out_a["id"]})
        pending_value = await session.execute(text("SELECT SUM(total_cost) FROM pending_inventory_movements"))
        assert float(sellable_stock.scalar_one()) == 0
        assert float(pending_value.scalar_one()) == 1000

    payload = {"pending_receipt_batch_id": batch["id"], "input_quantity": 10, "loss_quantity": 1,
        "remaining_quantity": 0, "financial_category_id": 11, "reason": "Grade and weigh mixed delivery",
        "outputs": [{"product_id": out_a["id"], "quantity": 4}, {"product_id": out_b["id"], "quantity": 5}]}
    draft = await app.post("/api/v1/sorting-events", headers=headers, json=payload)
    assert draft.status_code == 201, draft.text
    preview = await app.post(f"/api/v1/sorting-events/{draft.json()['id']}/preview", headers=headers, json={})
    assert preview.status_code == 200, preview.text
    assert preview.json()["reconciles"] is True
    assert preview.json()["source_value"] == 1000
    assert sum(row["value_snapshot"] for row in preview.json()["outputs"]) + preview.json()["loss_value"] == 1000

    key = _idem()
    post_headers = {**headers, "If-Match": f'"{draft.json()["version"]}"', "Idempotency-Key": key}
    posted_sort = await app.post(f"/api/v1/sorting-events/{draft.json()['id']}/post", headers=post_headers, json={})
    assert posted_sort.status_code == 200, posted_sort.text
    replay = await app.post(f"/api/v1/sorting-events/{draft.json()['id']}/post", headers=post_headers, json={})
    assert replay.status_code == 200, replay.text
    pending_after = await app.get("/api/v1/pending-receipts", headers=headers)
    assert all(item["id"] != batch["id"] for item in pending_after.json()["data"])
    purchase_after = await app.get(f"/api/v1/purchases/{purchase_id}?include=lines", headers=headers)
    blocked_cancel = await app.post(f"/api/v1/purchases/{purchase_id}/cancel",
        headers={**headers, "If-Match": purchase_after.headers["ETag"], "Idempotency-Key": _idem()},
        json={"reason": "Test cancellation after sorting"})
    assert blocked_cancel.status_code == 409, blocked_cancel.text
    assert "sorting" in blocked_cancel.text.lower()

    inventory = await app.get("/api/v1/reports/inventory", headers=headers)
    assert inventory.status_code == 200, inventory.text
    assert inventory.json()["data"]["total_inventory_value"] == 900
    today = datetime.now(UTC).date().isoformat()
    pnl = await app.get("/api/v1/reports/p-and-l", headers=headers,
        params={"from": f"{today}T00:00:00Z", "to": f"{today}T23:59:59Z"})
    assert pnl.status_code == 200, pnl.text
    assert pnl.json()["data"]["operating_expenses"] == 100


async def test_unsorted_receipt_rejects_count_unit_without_partial_line(
    app: AsyncClient, owner_user: dict[str, Any],
) -> None:
    headers = await _owner_headers(app, owner_user)
    unit = await app.post("/api/v1/units/", headers={**headers, "Idempotency-Key": _idem()},
        json={"code": f"pc_{uuid.uuid4().hex[:7]}", "name": "Piece", "dimension": "count",
              "factor_to_base": 1, "is_active": True})
    assert unit.status_code == 201, unit.text
    purchase = await app.post("/api/v1/purchases", headers={**headers, "Idempotency-Key": _idem()}, json={})
    assert purchase.status_code == 201, purchase.text
    line = await app.post(f"/api/v1/purchases/{purchase.json()['id']}/lines",
        headers={**headers, "If-Match": purchase.headers["ETag"], "Idempotency-Key": _idem()},
        json={"receipt_type": "unsorted", "unit_id": unit.json()["id"], "quantity": 4, "unit_price": 10})
    assert line.status_code == 400, line.text
    detail = await app.get(f"/api/v1/purchases/{purchase.json()['id']}?include=lines", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json().get("lines", []) == []
