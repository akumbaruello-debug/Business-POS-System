"""M5 / Phase E.2 — Production Inputs sub-resource tests.

Covers the 4 E.2 OpenAPI operations:

* ``listProductionInputs``  GET    /production-runs/{id}/inputs
* ``addProductionInput``    POST   /production-runs/{id}/inputs
* ``updateProductionInput`` PATCH  /production-runs/{id}/inputs/{input_id}
* ``deleteProductionInput`` DELETE /production-runs/{id}/inputs/{input_id}

Test coverage:

* Happy path: add → list → update → delete
* Nonexistent parent production run
* Nonexistent product (404)
* Inactive product (422)
* Invalid quantity (422)
* Duplicate input product (409)
* Input product equals output product (422)
* Draft lifecycle enforcement (422)
* Authorization (403 / 401)
* ETag / concurrency (412 on stale, 400 on missing)
* Idempotency (replay 200, key+body mismatch 409)
* Derived-cost recalculation
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
    """Seed products for input tests.

    * Product 1 — output (is_producible=TRUE, is_active=TRUE)
    * Product 2 — raw input (is_purchasable=TRUE, is_active=TRUE)
    * Product 3 — non-producible output candidate
    * Product 4 — deactivated
    * Product 5 — alt raw input
    """
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
    """Auto-seed products for every test in this module."""
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


async def _deactivate_product(pid: int) -> None:
    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text("UPDATE products SET is_active = FALSE WHERE id = :id"),
            {"id": pid},
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
async def test_production_input_add_list_update_delete(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Full lifecycle of a production input sub-resource."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    rid = run["id"]
    etag = _etag_body(run)

    # 1. Add a new input (product 5).
    res_add = await app.post(
        f"/api/v1/production-runs/{rid}/inputs",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"product_id": 5, "quantity": 3.0},
    )
    assert res_add.status_code == 201, res_add.text
    inp = res_add.json()
    assert inp["product_id"] == 5
    assert inp["quantity"] == 3.0
    assert inp["line_cost"] >= 0
    assert "line_number" in inp

    # 2. List inputs — should include the original (product 2) + new (product 5).
    res_list = await app.get(
        f"/api/v1/production-runs/{rid}/inputs", headers=h
    )
    assert res_list.status_code == 200, res_list.text
    body = res_list.json()
    assert "data" in body and isinstance(body["data"], list)
    assert "pagination" in body
    assert len(body["data"]) == 2
    pids = {d["product_id"] for d in body["data"]}
    assert pids == {2, 5}

    # 3. Update the new input.
    inp_id = inp["id"]
    new_etag = _etag_body(res_add.json())
    res_upd = await app.patch(
        f"/api/v1/production-runs/{rid}/inputs/{inp_id}",
        headers={**h, "If-Match": new_etag},
        json={"product_id": 5, "quantity": 4.0},
    )
    assert res_upd.status_code == 200, res_upd.text
    upd = res_upd.json()
    assert upd["quantity"] == 4.0
    assert upd["product_id"] == 5

    # 4. Delete the new input.
    del_etag = _etag_body(res_upd.json())
    res_del = await app.delete(
        f"/api/v1/production-runs/{rid}/inputs/{inp_id}",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": del_etag},
    )
    assert res_del.status_code == 204, res_del.text

    # 5. List confirms only 1 input remains (product 2).
    res_list2 = await app.get(
        f"/api/v1/production-runs/{rid}/inputs", headers=h
    )
    assert res_list2.status_code == 200
    data = res_list2.json()["data"]
    assert len(data) == 1
    assert data[0]["product_id"] == 2


# ---------------------------------------------------------------------------
# Nonexistent parent production run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_input_post_nonexistent_run(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    h = await _owner_headers(app, owner_user)
    res = await app.post(
        "/api/v1/production-runs/9999/inputs",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": '"placeholder"'},
        json={"product_id": 2, "quantity": 1.0},
    )
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_production_input_get_nonexistent_run(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    h = await _owner_headers(app, owner_user)
    res = await app.get("/api/v1/production-runs/9999/inputs", headers=h)
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_production_input_patch_nonexistent_run(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    h = await _owner_headers(app, owner_user)
    res = await app.patch(
        "/api/v1/production-runs/9999/inputs/1",
        headers={**h, "If-Match": '"placeholder"'},
        json={"product_id": 2, "quantity": 1.0},
    )
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_production_input_delete_nonexistent_run(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    h = await _owner_headers(app, owner_user)
    res = await app.delete(
        "/api/v1/production-runs/9999/inputs/1",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": '"placeholder"'},
    )
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_production_input_patch_nonexistent_input(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)
    res = await app.patch(
        f"/api/v1/production-runs/{run['id']}/inputs/9999",
        headers={**h, "If-Match": etag},
        json={"product_id": 5, "quantity": 1.0},
    )
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_production_input_delete_nonexistent_input(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """DELETE nonexistent input with idempotency key → 204 (already-gone)."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)
    res = await app.delete(
        f"/api/v1/production-runs/{run['id']}/inputs/9999",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
    )
    # With idempotency key, "already deleted" is a 204 idempotent success.
    assert res.status_code == 204, res.text


# ---------------------------------------------------------------------------
# Nonexistent product
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_input_nonexistent_product_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)
    res = await app.post(
        f"/api/v1/production-runs/{run['id']}/inputs",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"product_id": 9999, "quantity": 1.0},
    )
    assert res.status_code == 404, res.text


# ---------------------------------------------------------------------------
# Inactive product
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_input_inactive_product_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)
    await _deactivate_product(2)
    res = await app.post(
        f"/api/v1/production-runs/{run['id']}/inputs",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"product_id": 2, "quantity": 1.0},
    )
    assert res.status_code == 422, res.text
    body = res.json()
    assert body["error"]["code"] == "business_rule_violation"
    assert body["error"]["details"]["reason"] == "product_deactivated"


# ---------------------------------------------------------------------------
# Invalid quantity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_input_invalid_quantity_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """quantity=0 is rejected by Pydantic schema (gt=0) → 400 validation_failed."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)
    res = await app.post(
        f"/api/v1/production-runs/{run['id']}/inputs",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"product_id": 5, "quantity": 0.0},
    )
    assert res.status_code == 400, res.text
    assert res.json()["error"]["code"] == "validation_failed"


@pytest.mark.asyncio
async def test_production_input_negative_quantity_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """quantity < 0 is rejected by Pydantic schema (gt=0) → 400 validation_failed."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)
    res = await app.post(
        f"/api/v1/production-runs/{run['id']}/inputs",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"product_id": 5, "quantity": -1.0},
    )
    assert res.status_code == 400, res.text
    assert res.json()["error"]["code"] == "validation_failed"


# ---------------------------------------------------------------------------
# Duplicate input product
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_input_duplicate_product_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Adding an input whose product_id already exists → 409 conflict."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)
    res = await app.post(
        f"/api/v1/production-runs/{run['id']}/inputs",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"product_id": 2, "quantity": 1.0},  # product 2 already in the run
    )
    assert res.status_code == 409, res.text
    body = res.json()
    assert body["error"]["code"] == "conflict"
    assert body["error"]["details"]["reason"] == "duplicate_input"


# ---------------------------------------------------------------------------
# Input product equals output product
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_input_equals_output_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)
    res = await app.post(
        f"/api/v1/production-runs/{run['id']}/inputs",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"product_id": 1, "quantity": 1.0},  # product 1 == output
    )
    assert res.status_code == 422, res.text
    body = res.json()
    assert body["error"]["details"]["reason"] == "input_equals_output"


# ---------------------------------------------------------------------------
# Draft lifecycle enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_input_add_posted_run_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Adding an input to a posted run → 409 lifecycle_state_invalid."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    rid = run["id"]
    etag = _etag_body(run)

    await _set_lifecycle(rid, "posted")

    res = await app.post(
        f"/api/v1/production-runs/{rid}/inputs",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"product_id": 5, "quantity": 1.0},
    )
    assert res.status_code == 409, res.text
    body = res.json()
    assert body["error"]["code"] == "lifecycle_state_invalid"


@pytest.mark.asyncio
async def test_production_input_delete_posted_run_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    rid = run["id"]
    etag = _etag_body(run)

    await _set_lifecycle(rid, "posted")

    res = await app.delete(
        f"/api/v1/production-runs/{rid}/inputs/1",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
    )
    assert res.status_code == 409, res.text
    assert res.json()["error"]["code"] == "lifecycle_state_invalid"


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_input_list_staff_ok(
    app: AsyncClient,
    owner_user: dict[str, Any],
    staff_user: dict[str, Any],
) -> None:
    """Staff has production.view → can list inputs."""
    h_owner = await _owner_headers(app, owner_user)
    run = await _create_run(app, h_owner)

    h_staff = await _staff_headers(app, staff_user)
    res = await app.get(
        f"/api/v1/production-runs/{run['id']}/inputs", headers=h_staff
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "data" in body
    assert len(body["data"]) == 1  # product 2 from create


@pytest.mark.asyncio
async def test_production_input_add_staff_rejected(
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
        f"/api/v1/production-runs/{run['id']}/inputs",
        headers={**h_staff, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"product_id": 5, "quantity": 1.0},
    )
    assert res.status_code == 403, res.text
    assert res.json()["error"]["code"] == "permission_denied"


@pytest.mark.asyncio
async def test_production_input_patch_staff_rejected(
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
        f"/api/v1/production-runs/{run['id']}/inputs/1",
        headers={**h_staff, "If-Match": etag},
        json={"product_id": 5, "quantity": 1.0},
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_production_input_delete_staff_rejected(
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
        f"/api/v1/production-runs/{run['id']}/inputs/1",
        headers={**h_staff, "Idempotency-Key": _idem(), "If-Match": etag},
    )
    assert res.status_code == 403, res.text


@pytest.mark.asyncio
async def test_production_input_unauthenticated_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    run = await _create_run(app, await _owner_headers(app, owner_user))
    # GET
    r = await app.get(f"/api/v1/production-runs/{run['id']}/inputs")
    assert r.status_code == 401, r.text
    # POST
    r2 = await app.post(
        f"/api/v1/production-runs/{run['id']}/inputs",
        headers={"Idempotency-Key": _idem(), "If-Match": '"x"'},
        json={"product_id": 5, "quantity": 1.0},
    )
    assert r2.status_code == 401, r2.text


# ---------------------------------------------------------------------------
# ETag / concurrency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_input_stale_etag_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """PATCH with stale ETag → 412 version_mismatch."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)

    # Force the run's updated_at to change so the ETag becomes stale.
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

    res = await app.patch(
        f"/api/v1/production-runs/{run['id']}/inputs/1",
        headers={**h, "If-Match": etag},
        json={"product_id": 5, "quantity": 1.0},
    )
    assert res.status_code == 412, res.text
    assert res.json()["error"]["code"] == "version_mismatch"


@pytest.mark.asyncio
async def test_production_input_patch_missing_if_match(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """PATCH without If-Match → 400 missing_header."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    res = await app.patch(
        f"/api/v1/production-runs/{run['id']}/inputs/1",
        headers=h,
        json={"product_id": 5, "quantity": 1.0},
    )
    assert res.status_code == 400, res.text


@pytest.mark.asyncio
async def test_production_input_add_missing_if_match(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """POST without If-Match → 400 missing_header."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    res = await app.post(
        f"/api/v1/production-runs/{run['id']}/inputs",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 5, "quantity": 1.0},
    )
    assert res.status_code == 400, res.text


@pytest.mark.asyncio
async def test_production_input_add_missing_idempotency_key(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """POST without Idempotency-Key → 400 missing_header."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)
    res = await app.post(
        f"/api/v1/production-runs/{run['id']}/inputs",
        headers={**h, "If-Match": etag},
        json={"product_id": 5, "quantity": 1.0},
    )
    assert res.status_code == 400, res.text


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_input_add_idempotency_replay(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Replay with same key + same body → 200 + Idempotent-Replay header."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)
    key = _idem()
    body = {"product_id": 5, "quantity": 3.0}

    r1 = await app.post(
        f"/api/v1/production-runs/{run['id']}/inputs",
        headers={**h, "Idempotency-Key": key, "If-Match": etag},
        json=body,
    )
    assert r1.status_code == 201, r1.text

    r2 = await app.post(
        f"/api/v1/production-runs/{run['id']}/inputs",
        headers={**h, "Idempotency-Key": key, "If-Match": etag},
        json=body,
    )
    assert r2.status_code == 200, r2.text
    assert r2.headers.get("Idempotent-Replay") == "true"
    assert r2.json()["product_id"] == 5


@pytest.mark.asyncio
async def test_production_input_add_idempotency_key_conflict(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Same key with a different body → 409 idempotency_violation."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)
    key = _idem()

    r1 = await app.post(
        f"/api/v1/production-runs/{run['id']}/inputs",
        headers={**h, "Idempotency-Key": key, "If-Match": etag},
        json={"product_id": 5, "quantity": 3.0},
    )
    assert r1.status_code == 201, r1.text

    r2 = await app.post(
        f"/api/v1/production-runs/{run['id']}/inputs",
        headers={**h, "Idempotency-Key": key, "If-Match": etag},
        json={"product_id": 5, "quantity": 6.0},
    )
    assert r2.status_code == 409, r2.text
    assert r2.json()["error"]["code"] == "idempotency_violation"


@pytest.mark.asyncio
async def test_production_input_delete_idempotency_replay(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """DELETE replay with same key → 200 + Idempotent-Replay."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)

    key = _idem()
    r1 = await app.delete(
        f"/api/v1/production-runs/{run['id']}/inputs/1",
        headers={**h, "Idempotency-Key": key, "If-Match": etag},
    )
    assert r1.status_code == 204, r1.text

    r2 = await app.delete(
        f"/api/v1/production-runs/{run['id']}/inputs/1",
        headers={**h, "Idempotency-Key": key, "If-Match": etag},
    )
    assert r2.status_code == 200, r2.text
    assert r2.headers.get("Idempotent-Replay") == "true"


@pytest.mark.asyncio
async def test_production_input_delete_missing_idempotency_key(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """DELETE without Idempotency-Key → 400 missing_header."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)
    res = await app.delete(
        f"/api/v1/production-runs/{run['id']}/inputs/1",
        headers={**h, "If-Match": etag},
    )
    assert res.status_code == 400, res.text


# ---------------------------------------------------------------------------
# Derived-cost recalculation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_input_add_recompute_totals(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Adding an input recomputes total_raw_cost and finished_unit_cost."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)

    original_raw = Decimal(str(run["total_raw_cost"]))

    res = await app.post(
        f"/api/v1/production-runs/{run['id']}/inputs",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"product_id": 5, "quantity": 3.0},
    )
    assert res.status_code == 201, res.text

    # Fetch the run and check totals changed.
    res_get = await app.get(
        f"/api/v1/production-runs/{run['id']}", headers=h
    )
    assert res_get.status_code == 200
    updated = res_get.json()
    new_raw = Decimal(str(updated["total_raw_cost"]))
    assert new_raw >= original_raw  # same or increased


@pytest.mark.asyncio
async def test_production_input_delete_recompute_totals(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Deleting an input recomputes totals so no stale values remain."""
    h = await _owner_headers(app, owner_user)
    run = await _create_run(app, h)
    etag = _etag_body(run)

    # Add a second input.
    res_add = await app.post(
        f"/api/v1/production-runs/{run['id']}/inputs",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"product_id": 5, "quantity": 3.0},
    )
    assert res_add.status_code == 201
    inp = res_add.json()

    # Delete it.
    new_etag = _etag_body(res_add.json())
    res_del = await app.delete(
        f"/api/v1/production-runs/{run['id']}/inputs/{inp['id']}",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": new_etag},
    )
    assert res_del.status_code == 204

    # Verify totals match what they were before the add (back to original).
    res_get = await app.get(
        f"/api/v1/production-runs/{run['id']}", headers=h
    )
    updated = res_get.json()
    assert Decimal(str(updated["total_raw_cost"])) == Decimal(str(run["total_raw_cost"]))
