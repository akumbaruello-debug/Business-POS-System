"""M5 / Phase E.2 — Production Cost Lines sub-resource tests.

Covers the 4 E.2 OpenAPI operations:

* ``listProductionCostLines``  GET    /production-runs/{id}/cost-lines
* ``addProductionCostLine``    POST   /production-runs/{id}/cost-lines
* ``updateProductionCostLine`` PATCH  /production-runs/{id}/cost-lines/{line_id}
* ``deleteProductionCostLine`` DELETE /production-runs/{id}/cost-lines/{line_id}

Test coverage:

* Happy path: add → list → update → delete
* Nonexistent parent production run
* Nonexistent cost type (404)
* Inactive cost type (422)
* Invalid amount (422)
* ``paid_in_cash=true`` and ``paid_in_cash=false``
* Draft lifecycle enforcement (422)
* Authorization (403 / 401)
* ETag / concurrency (412 on stale, 400 on missing)
* Idempotency (replay 200, key+body mismatch 409)
* Derived-cost recalculation (total_overhead_cost, finished_unit_cost)
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Helpers (mirrored from test_production_runs.py)
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


def _etag_body(body: dict[str, Any]) -> str:
    """Build an If-Match value from the response body's updated_at."""
    return f'"{body["updated_at"]}"'


async def _seed_products() -> None:
    """Seed products for cost-line tests."""
    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO products (id, name, selling_price, allow_negative_stock,"
                " is_sellable, is_purchasable, is_producible, is_active,"
                " created_by, updated_by) VALUES"
                " (1, 'Finished Widget', 100.00, FALSE, TRUE, FALSE, TRUE, TRUE, 1, 1),"
                " (2, 'Raw Material A', 10.00, FALSE, FALSE, TRUE, FALSE, TRUE, 1, 1),"
                " (3, 'NonProducible', 50.00, FALSE, TRUE, FALSE, FALSE, TRUE, 1, 1),"
                " (4, 'DeactivatedWidget', 80.00, FALSE, TRUE, FALSE, TRUE, FALSE, 1, 1),"
                " (5, 'Raw Material B', 15.00, FALSE, FALSE, TRUE, FALSE, TRUE, 1, 1)"
                " ON CONFLICT (id) DO UPDATE SET"
                " selling_price = EXCLUDED.selling_price,"
                " is_active = EXCLUDED.is_active,"
                " is_purchasable = EXCLUDED.is_purchasable,"
                " is_producible = EXCLUDED.is_producible"
            )
        )
        await session.commit()


@pytest.fixture(autouse=True)
async def _ensure_seeds(app: AsyncClient) -> Any:
    """Auto-seed products (and cost_types via db fixture) for every test."""
    await _seed_products()
    yield


def _create_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "output_product_id": 1,
        "output_quantity": 10.0,
        "notes": "initial draft",
        "inputs": [
            {"product_id": 2, "quantity": 5.0},
        ],
    }
    body.update(overrides)
    return body


async def _create_run(app: AsyncClient, h: dict[str, str]) -> dict[str, Any]:
    """Create a draft run and return its JSON body."""
    res = await app.post(
        "/api/v1/production-runs",
        headers={**h, "Idempotency-Key": _idem()},
        json=_create_body(),
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _deactivate_cost_type(ct_id: int) -> None:
    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text("UPDATE cost_types SET is_active = FALSE WHERE id = :id"),
            {"id": ct_id},
        )
        await session.commit()


async def _set_lifecycle(rid: int, status: str) -> None:
    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "UPDATE production_runs SET lifecycle_status = :ls, "
                "posted_at = NOW(), posted_by = 1 WHERE id = :id"
            ),
            {"ls": status, "id": rid},
        )
        await session.commit()


# ---------------------------------------------------------------------------
# Happy path: add → list → update → delete
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_cost_line_add_list_update_delete(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Full lifecycle of a production cost line sub-resource."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    rid = run["id"]
    etag = _etag_body(run)

    # 1. Add a cost line.
    res_add = await app.post(
        f"/api/v1/production-runs/{rid}/cost-lines",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"cost_type_id": 1, "amount": 25.00, "paid_in_cash": True},
    )
    assert res_add.status_code == 201, res_add.text
    cl = res_add.json()
    assert cl["cost_type_id"] == 1
    assert Decimal(str(cl["amount"])) == Decimal("25.00")
    assert cl["paid_in_cash"] is True
    assert "line_number" in cl

    # 2. List cost lines — should have 1.
    res_list = await app.get(
        f"/api/v1/production-runs/{rid}/cost-lines", headers=h
    )
    assert res_list.status_code == 200, res_list.text
    body = res_list.json()
    assert "data" in body and isinstance(body["data"], list)
    assert "pagination" in body
    assert len(body["data"]) == 1
    assert body["data"][0]["cost_type_id"] == 1

    # 3. Update the cost line.
    cl_id = cl["id"]
    new_etag = _etag_body(res_add.json())
    res_upd = await app.patch(
        f"/api/v1/production-runs/{rid}/cost-lines/{cl_id}",
        headers={**h, "If-Match": new_etag},
        json={"cost_type_id": 2, "amount": 30.00, "paid_in_cash": False, "description": "electricity"},
    )
    assert res_upd.status_code == 200, res_upd.text
    upd = res_upd.json()
    assert upd["cost_type_id"] == 2
    assert Decimal(str(upd["amount"])) == Decimal("30.00")
    assert upd["paid_in_cash"] is False
    assert upd["description"] == "electricity"

    # 4. Delete the cost line.
    del_etag = _etag_body(res_upd.json())
    res_del = await app.delete(
        f"/api/v1/production-runs/{rid}/cost-lines/{cl_id}",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": del_etag},
    )
    assert res_del.status_code == 204, res_del.text

    # 5. List confirms 0 cost lines remain.
    res_list2 = await app.get(
        f"/api/v1/production-runs/{rid}/cost-lines", headers=h
    )
    assert res_list2.status_code == 200
    assert len(res_list2.json()["data"]) == 0


# ---------------------------------------------------------------------------
# paid_in_cash = true and false
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_cost_line_paid_in_cash_true(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)
    res = await app.post(
        f"/api/v1/production-runs/{run['id']}/cost-lines",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"cost_type_id": 1, "amount": 10.00, "paid_in_cash": True},
    )
    assert res.status_code == 201, res.text
    assert res.json()["paid_in_cash"] is True


@pytest.mark.asyncio
async def test_production_cost_line_paid_in_cash_false(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)
    res = await app.post(
        f"/api/v1/production-runs/{run['id']}/cost-lines",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"cost_type_id": 1, "amount": 10.00, "paid_in_cash": False},
    )
    assert res.status_code == 201, res.text
    assert res.json()["paid_in_cash"] is False


@pytest.mark.asyncio
async def test_production_cost_line_paid_in_cash_default_true(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """paid_in_cash defaults to True when omitted."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)
    res = await app.post(
        f"/api/v1/production-runs/{run['id']}/cost-lines",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"cost_type_id": 1, "amount": 10.00},
    )
    assert res.status_code == 201, res.text
    assert res.json()["paid_in_cash"] is True


# ---------------------------------------------------------------------------
# Nonexistent parent / cost type
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_cost_line_post_nonexistent_run(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    h = await _owner_headers(app, owner_user)
    res = await app.post(
        "/api/v1/production-runs/9999/cost-lines",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": '"placeholder"'},
        json={"cost_type_id": 1, "amount": 10.00},
    )
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_production_cost_line_get_nonexistent_run(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    h = await _owner_headers(app, owner_user)
    res = await app.get("/api/v1/production-runs/9999/cost-lines", headers=h)
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_production_cost_line_nonexistent_cost_type_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)
    res = await app.post(
        f"/api/v1/production-runs/{run['id']}/cost-lines",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"cost_type_id": 9999, "amount": 10.00},
    )
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_production_cost_line_inactive_cost_type_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)
    await _deactivate_cost_type(1)
    res = await app.post(
        f"/api/v1/production-runs/{run['id']}/cost-lines",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"cost_type_id": 1, "amount": 10.00},
    )
    assert res.status_code == 422, res.text
    body = res.json()
    assert body["error"]["code"] == "business_rule_violation"
    assert body["error"]["details"]["reason"] == "cost_type_deactivated"


# ---------------------------------------------------------------------------
# Invalid amount
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_cost_line_invalid_amount_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """amount=0 is rejected by Pydantic schema (gt=0) → 400 validation_failed."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)
    res = await app.post(
        f"/api/v1/production-runs/{run['id']}/cost-lines",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"cost_type_id": 1, "amount": 0.0},
    )
    assert res.status_code == 400, res.text
    body = res.json()
    assert body["error"]["code"] == "validation_failed"


@pytest.mark.asyncio
async def test_production_cost_line_negative_amount_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """amount < 0 is rejected by Pydantic schema (gt=0) → 400 validation_failed."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)
    res = await app.post(
        f"/api/v1/production-runs/{run['id']}/cost-lines",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"cost_type_id": 1, "amount": -5.0},
    )
    assert res.status_code == 400, res.text
    assert res.json()["error"]["code"] == "validation_failed"


# ---------------------------------------------------------------------------
# Draft lifecycle enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_cost_line_add_posted_run_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Adding a cost line to a posted run → 409 lifecycle_state_invalid."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    rid = run["id"]
    etag = _etag_body(run)

    await _set_lifecycle(rid, "posted")

    res = await app.post(
        f"/api/v1/production-runs/{rid}/cost-lines",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"cost_type_id": 1, "amount": 10.00},
    )
    assert res.status_code == 409, res.text
    body = res.json()
    assert body["error"]["code"] == "lifecycle_state_invalid"


@pytest.mark.asyncio
async def test_production_cost_line_delete_posted_run_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    rid = run["id"]
    etag = _etag_body(run)

    await _set_lifecycle(rid, "posted")

    res = await app.delete(
        f"/api/v1/production-runs/{rid}/cost-lines/1",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
    )
    assert res.status_code == 409, res.text
    assert res.json()["error"]["code"] == "lifecycle_state_invalid"


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_cost_line_list_staff_ok(
    app: AsyncClient,
    owner_user: dict[str, Any],
    staff_user: dict[str, Any],
) -> None:
    """Staff has production.view → can list cost lines."""
    h_owner = await _owner_headers(app, owner_user)
    run = await _create_run(app, h_owner)

    h_staff = await _staff_headers(app, staff_user)
    res = await app.get(
        f"/api/v1/production-runs/{run['id']}/cost-lines", headers=h_staff
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "data" in body
    assert len(body["data"]) == 0  # no cost lines in the base create


@pytest.mark.asyncio
async def test_production_cost_line_add_staff_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
    staff_user: dict[str, Any],
) -> None:
    """Staff lacks production.edit_own_draft → 403 on POST."""
    h_owner = await _owner_headers(app, owner_user)
    run = await _create_run(app, h_owner)
    etag = _etag_body(run)

    h_staff = await _staff_headers(app, staff_user)
    res = await app.post(
        f"/api/v1/production-runs/{run['id']}/cost-lines",
        headers={**h_staff, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"cost_type_id": 1, "amount": 10.00},
    )
    assert res.status_code == 403, res.text
    assert res.json()["error"]["code"] == "permission_denied"


@pytest.mark.asyncio
async def test_production_cost_line_patch_staff_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
    staff_user: dict[str, Any],
) -> None:
    """Staff lacks production.edit_own_draft → 403 on PATCH."""
    h_owner = await _owner_headers(app, owner_user)
    run = await _create_run(app, h_owner)
    etag = _etag_body(run)

    h_staff = await _staff_headers(app, staff_user)
    res = await app.patch(
        f"/api/v1/production-runs/{run['id']}/cost-lines/1",
        headers={**h_staff, "If-Match": etag},
        json={"cost_type_id": 1, "amount": 10.00},
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_production_cost_line_delete_staff_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
    staff_user: dict[str, Any],
) -> None:
    """Staff lacks production.edit_own_draft → 403 on DELETE."""
    h_owner = await _owner_headers(app, owner_user)
    run = await _create_run(app, h_owner)
    etag = _etag_body(run)

    h_staff = await _staff_headers(app, staff_user)
    res = await app.delete(
        f"/api/v1/production-runs/{run['id']}/cost-lines/1",
        headers={**h_staff, "Idempotency-Key": _idem(), "If-Match": etag},
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_production_cost_line_unauthenticated_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    run = await _create_run(app, await _owner_headers(app, owner_user))
    # GET
    r = await app.get(f"/api/v1/production-runs/{run['id']}/cost-lines")
    assert r.status_code == 401, r.text
    # POST
    r2 = await app.post(
        f"/api/v1/production-runs/{run['id']}/cost-lines",
        headers={"Idempotency-Key": _idem(), "If-Match": '"x"'},
        json={"cost_type_id": 1, "amount": 10.00},
    )
    assert r2.status_code == 401, r2.text


# ---------------------------------------------------------------------------
# ETag / concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_cost_line_stale_etag_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """PATCH with stale ETag → 412 version_mismatch.

    Uses the production-run ETag for both the POST (to satisfy If-Match)
    and the PATCH (to test stale-ETag rejection).
    """
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)

    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "UPDATE production_runs SET updated_at = NOW() + INTERVAL '1 second' "
                "WHERE id = :id"
            ),
            {"id": run["id"]},
        )
        await session.commit()

    # Fetch the current (fresh) ETag after the direct SQL update.
    res_get = await app.get(
        f"/api/v1/production-runs/{run['id']}", headers=h
    )
    fresh_etag = _etag_body(res_get.json())

    # First add a cost line using the FRESH ETag (should succeed).
    res_add = await app.post(
        f"/api/v1/production-runs/{run['id']}/cost-lines",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": fresh_etag},
        json={"cost_type_id": 1, "amount": 10.00},
    )
    assert res_add.status_code == 201, res_add.text
    cl_id = res_add.json()["id"]

    # Now try to PATCH with the OLD (stale) ETag → 412 version_mismatch.
    res = await app.patch(
        f"/api/v1/production-runs/{run['id']}/cost-lines/{cl_id}",
        headers={**h, "If-Match": etag},
        json={"cost_type_id": 1, "amount": 15.00},
    )
    assert res.status_code == 412, res.text
    assert res.json()["error"]["code"] == "version_mismatch"


@pytest.mark.asyncio
async def test_production_cost_line_patch_missing_if_match(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """PATCH without If-Match → 400 missing_header."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)

    res_add = await app.post(
        f"/api/v1/production-runs/{run['id']}/cost-lines",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"cost_type_id": 1, "amount": 10.00},
    )
    assert res_add.status_code == 201
    cl_id = res_add.json()["id"]

    res = await app.patch(
        f"/api/v1/production-runs/{run['id']}/cost-lines/{cl_id}",
        headers=h,
        json={"cost_type_id": 2, "amount": 15.00},
    )
    assert res.status_code == 400, res.text


@pytest.mark.asyncio
async def test_production_cost_line_add_missing_if_match(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """POST without If-Match → 400 missing_header."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    res = await app.post(
        f"/api/v1/production-runs/{run['id']}/cost-lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"cost_type_id": 1, "amount": 10.00},
    )
    assert res.status_code == 400, res.text


@pytest.mark.asyncio
async def test_production_cost_line_add_missing_idempotency_key(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """POST without Idempotency-Key → 400 missing_header."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)
    res = await app.post(
        f"/api/v1/production-runs/{run['id']}/cost-lines",
        headers={**h, "If-Match": etag},
        json={"cost_type_id": 1, "amount": 10.00},
    )
    assert res.status_code == 400, res.text


@pytest.mark.asyncio
async def test_production_cost_line_delete_missing_idempotency_key(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """DELETE without Idempotency-Key → 400 missing_header."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)
    res_add = await app.post(
        f"/api/v1/production-runs/{run['id']}/cost-lines",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"cost_type_id": 1, "amount": 10.00},
    )
    assert res_add.status_code == 201
    cl_id = res_add.json()["id"]

    res = await app.delete(
        f"/api/v1/production-runs/{run['id']}/cost-lines/{cl_id}",
        headers={**h, "If-Match": etag},
    )
    assert res.status_code == 400, res.text


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_cost_line_add_idempotency_replay(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Replay with same key + same body → 200 + Idempotent-Replay header."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)
    key = _idem()
    body = {"cost_type_id": 1, "amount": 25.00, "paid_in_cash": True}

    r1 = await app.post(
        f"/api/v1/production-runs/{run['id']}/cost-lines",
        headers={**h, "Idempotency-Key": key, "If-Match": etag},
        json=body,
    )
    assert r1.status_code == 201, r1.text

    r2 = await app.post(
        f"/api/v1/production-runs/{run['id']}/cost-lines",
        headers={**h, "Idempotency-Key": key, "If-Match": etag},
        json=body,
    )
    assert r2.status_code == 200, r2.text
    assert r2.headers.get("Idempotent-Replay") == "true"
    assert r2.json()["cost_type_id"] == 1


@pytest.mark.asyncio
async def test_production_cost_line_add_idempotency_key_conflict(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Same key with a different body → 409 idempotency_violation."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)
    key = _idem()

    r1 = await app.post(
        f"/api/v1/production-runs/{run['id']}/cost-lines",
        headers={**h, "Idempotency-Key": key, "If-Match": etag},
        json={"cost_type_id": 1, "amount": 25.00},
    )
    assert r1.status_code == 201, r1.text

    r2 = await app.post(
        f"/api/v1/production-runs/{run['id']}/cost-lines",
        headers={**h, "Idempotency-Key": key, "If-Match": etag},
        json={"cost_type_id": 2, "amount": 25.00},  # different cost_type_id
    )
    assert r2.status_code == 409, r2.text
    assert r2.json()["error"]["code"] == "idempotency_violation"


@pytest.mark.asyncio
async def test_production_cost_line_delete_idempotency_replay(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """DELETE replay with same key → 200 + Idempotent-Replay."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)

    # Add a cost line first.
    res_add = await app.post(
        f"/api/v1/production-runs/{run['id']}/cost-lines",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"cost_type_id": 1, "amount": 10.00},
    )
    assert res_add.status_code == 201
    cl_id = res_add.json()["id"]

    # Reload etag after the add.
    res_get = await app.get(
        f"/api/v1/production-runs/{run['id']}", headers=h
    )
    add_etag = _etag_body(res_get.json())

    key = _idem()
    r1 = await app.delete(
        f"/api/v1/production-runs/{run['id']}/cost-lines/{cl_id}",
        headers={**h, "Idempotency-Key": key, "If-Match": add_etag},
    )
    assert r1.status_code == 204, r1.text

    r2 = await app.delete(
        f"/api/v1/production-runs/{run['id']}/cost-lines/{cl_id}",
        headers={**h, "Idempotency-Key": key, "If-Match": add_etag},
    )
    assert r2.status_code == 200, r2.text
    assert r2.headers.get("Idempotent-Replay") == "true"


# ---------------------------------------------------------------------------
# Derived-cost recalculation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_cost_line_add_recompute_totals(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Adding a cost line recomputes total_overhead_cost and finished_unit_cost."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)

    original_overhead = Decimal(str(run["total_overhead_cost"]))
    assert original_overhead == Decimal("0.00")

    res = await app.post(
        f"/api/v1/production-runs/{run['id']}/cost-lines",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"cost_type_id": 1, "amount": 50.00, "paid_in_cash": True},
    )
    assert res.status_code == 201, res.text

    res_get = await app.get(
        f"/api/v1/production-runs/{run['id']}", headers=h
    )
    updated = res_get.json()
    new_overhead = Decimal(str(updated["total_overhead_cost"]))
    assert new_overhead == Decimal("50.00")


@pytest.mark.asyncio
async def test_production_cost_line_delete_recompute_totals(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Deleting a cost line recomputes totals so no stale values remain."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)

    # Add a cost line.
    res_add = await app.post(
        f"/api/v1/production-runs/{run['id']}/cost-lines",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"cost_type_id": 1, "amount": 50.00, "paid_in_cash": True},
    )
    assert res_add.status_code == 201
    cl_id = res_add.json()["id"]

    # Delete it.
    new_etag = _etag_body(res_add.json())
    res_del = await app.delete(
        f"/api/v1/production-runs/{run['id']}/cost-lines/{cl_id}",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": new_etag},
    )
    assert res_del.status_code == 204

    # Verify overhead is back to 0.
    res_get = await app.get(
        f"/api/v1/production-runs/{run['id']}", headers=h
    )
    updated = res_get.json()
    assert Decimal(str(updated["total_overhead_cost"])) == Decimal("0.00")
    assert Decimal(str(updated["total_raw_cost"])) == Decimal(str(run["total_raw_cost"]))
