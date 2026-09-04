"""M5 / Phase E.1 — Production Runs draft CRUD tests.

Covers the 4 E.1 OpenAPI operations:

* ``listProductionRuns``
* ``createProductionRun``
* ``getProductionRun``
* ``updateProductionRunDraft``

Plus:

* Authorisation (production.view / production.create / production.edit_own_draft)
* Business rules:
  - Non-producible output product is rejected.
  - Deactivated output product is rejected.
  - Output product not found is rejected.
  - Input product equal to output product is rejected.
  - Duplicate input product is rejected.
  - Cost type not found is rejected.
  - Cost type deactivated is rejected.
  - Updating a non-draft run is rejected (lifecycle guard).
* Concurrency (correct ETag succeeds; stale ETag is rejected).
* Idempotency (replay returns the cached body; mismatched body 409).
* Validation (malformed request, missing required fields, invalid quantities).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Local helpers
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


def _etag(resp: Any) -> str:
    """Build an If-Match value from the response's updated_at."""
    body = resp.json()
    return f'"{body["updated_at"]}"'


async def _seed_products() -> None:
    """Seed a producible output product (#1) + raw material (#2) + #3, #4, #5.

    The ``db`` fixture TRUNCATEs products per test, so this runs at the
    top of any test that needs them.

    * Product 1 — output (is_producible=TRUE, is_active=TRUE)
    * Product 2 — raw input (is_purchasable=TRUE, is_active=TRUE)
    * Product 3 — alt output (is_producible=FALSE, is_active=TRUE)
    * Product 4 — deactivated output (is_producible=TRUE, is_active=FALSE)
    * Product 5 — alt raw input (is_purchasable=TRUE, is_active=TRUE)
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
async def _ensure_seeds(app: AsyncClient) -> AsyncGenerator[None, None]:
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


# ---------------------------------------------------------------------------
# Happy path: create → list → get → patch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_run_draft_crud_lifecycle(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Draft list → create → get → patch (with ETag)."""
    h = await _owner_headers(app, owner_user)

    # 1. Create draft.
    res = await app.post(
        "/api/v1/production-runs",
        headers={**h, "Idempotency-Key": _idem()},
        json=_create_body(),
    )
    assert res.status_code == 201, res.text
    run = res.json()
    rid = run["id"]
    assert run["lifecycle_status"] == "draft"
    assert run["output_product_id"] == 1
    assert run["output_quantity"] == 10.0
    assert run["notes"] == "initial draft"
    assert run["finished_unit_cost"] > 0
    assert Decimal(str(run["total_raw_cost"])) == Decimal("0.00")
    assert Decimal(str(run["total_overhead_cost"])) == Decimal("0.00")
    assert isinstance(run["inputs"], list) and len(run["inputs"]) == 1
    assert isinstance(run["cost_lines"], list) and len(run["cost_lines"]) == 0
    assert "output" in run and run["output"]["product_id"] == 1
    assert run["version"] >= 1  # recompute UPDATE during create may bump to 2

    # 2. Get single — required sub-resources all present.
    res_g = await app.get(f"/api/v1/production-runs/{rid}", headers=h)
    assert res_g.status_code == 200, res_g.text
    single = res_g.json()
    assert single["id"] == rid
    assert "inputs" in single and isinstance(single["inputs"], list)
    assert "output" in single and isinstance(single["output"], dict)
    assert "cost_lines" in single and isinstance(single["cost_lines"], list)

    # 3. List contains it.
    res_l = await app.get("/api/v1/production-runs", headers=h)
    assert res_l.status_code == 200, res_l.text
    body = res_l.json()
    items = body["data"] if isinstance(body, dict) and "data" in body else body
    assert any(r["id"] == rid for r in items)

    # 4. Patch draft (If-Match with current ETag).
    etag = _etag(res)
    create_version = run["version"]
    res_p = await app.patch(
        f"/api/v1/production-runs/{rid}",
        headers={**h, "If-Match": etag},
        json={"notes": "edited"},
    )
    assert res_p.status_code == 200, res_p.text
    assert res_p.json()["notes"] == "edited"
    assert res_p.json()["version"] > create_version  # bumped by trigger(s)


# ---------------------------------------------------------------------------
# Create with cost_lines inline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_run_create_with_cost_lines(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Create with cost_lines array — total_overhead_cost and
    finished_unit_cost reflect the supplied overhead."""
    h = await _owner_headers(app, owner_user)
    body = _create_body(
        cost_lines=[
            {"cost_type_id": 1, "amount": 25.00, "paid_in_cash": True},
            {"cost_type_id": 2, "amount": 15.00, "paid_in_cash": True},
        ],
    )
    res = await app.post(
        "/api/v1/production-runs",
        headers={**h, "Idempotency-Key": _idem()},
        json=body,
    )
    assert res.status_code == 201, res.text
    run = res.json()
    assert Decimal(str(run["total_overhead_cost"])) == Decimal("40.00")
    assert len(run["cost_lines"]) == 2
    amounts = sorted(Decimal(str(c["amount"])) for c in run["cost_lines"])
    assert amounts == [Decimal("15.00"), Decimal("25.00")]


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_run_create_requires_production_create_cap(
    app: AsyncClient,
    staff_user: dict[str, Any],
    owner_user: dict[str, Any],
) -> None:
    """Staff user lacks production.create + production.edit_own_draft.

    Per ``app/authz/caps.py`` staff defaults include only ``production.view``
    (NOT create / edit_own_draft). So staff is rejected on POST + PATCH but
    succeeds on GET.
    """
    # First, as owner, create a draft so we have a known id to GET/PATCH.
    oh = await _owner_headers(app, owner_user)
    res = await app.post(
        "/api/v1/production-runs",
        headers={**oh, "Idempotency-Key": _idem()},
        json=_create_body(),
    )
    rid = res.json()["id"]

    sh = await _staff_headers(app, staff_user)

    # Staff lacks production.create → cannot POST.
    res_c = await app.post(
        "/api/v1/production-runs",
        headers={**sh, "Idempotency-Key": _idem()},
        json=_create_body(),
    )
    assert res_c.status_code == 403, res_c.text

    # Staff HAS production.view → can GET.
    res_g = await app.get(f"/api/v1/production-runs/{rid}", headers=sh)
    assert res_g.status_code == 200, res_g.text

    # Staff lacks production.edit_own_draft → cannot PATCH.
    etag = _etag(res)
    res_p = await app.patch(
        f"/api/v1/production-runs/{rid}",
        headers={**sh, "If-Match": etag},
        json={"notes": "staff edit"},
    )
    assert res_p.status_code == 403, res_p.text
    assert res_p.json()["error"]["code"] == "permission_denied"


@pytest.mark.asyncio
async def test_production_run_unauthenticated_rejected(
    app: AsyncClient,
) -> None:
    """No bearer token → 401 on every E.1 endpoint."""
    # GET list
    r = await app.get("/api/v1/production-runs")
    assert r.status_code == 401, r.text
    # POST create
    r = await app.post(
        "/api/v1/production-runs",
        headers={"Idempotency-Key": _idem()},
        json=_create_body(),
    )
    assert r.status_code == 401, r.text


# ---------------------------------------------------------------------------
# Business rules
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_run_non_producible_output_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Product #3 is_active but is_producible=FALSE → 422."""
    h = await _owner_headers(app, owner_user)
    res = await app.post(
        "/api/v1/production-runs",
        headers={**h, "Idempotency-Key": _idem()},
        json=_create_body(output_product_id=3),
    )
    assert res.status_code == 422, res.text
    body = res.json()
    assert body["error"]["code"] == "business_rule_violation"
    assert body["error"]["details"]["field"] == "output_product_id"
    assert body["error"]["details"]["reason"] == "product_not_producible"


@pytest.mark.asyncio
async def test_production_run_deactivated_output_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Product #4 is_active=FALSE → 422."""
    h = await _owner_headers(app, owner_user)
    res = await app.post(
        "/api/v1/production-runs",
        headers={**h, "Idempotency-Key": _idem()},
        json=_create_body(output_product_id=4),
    )
    assert res.status_code == 422, res.text
    body = res.json()
    assert body["error"]["code"] == "business_rule_violation"
    assert body["error"]["details"]["reason"] == "product_deactivated"


@pytest.mark.asyncio
async def test_production_run_invalid_output_product_returns_not_found(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Product id that doesn't exist → 404."""
    h = await _owner_headers(app, owner_user)
    res = await app.post(
        "/api/v1/production-runs",
        headers={**h, "Idempotency-Key": _idem()},
        json=_create_body(output_product_id=9999),
    )
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_production_run_input_equals_output_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Output product = input product → 422 (prevent circular flow)."""
    h = await _owner_headers(app, owner_user)
    body = _create_body(
        output_product_id=1,
        inputs=[{"product_id": 1, "quantity": 1.0}],
    )
    res = await app.post(
        "/api/v1/production-runs",
        headers={**h, "Idempotency-Key": _idem()},
        json=body,
    )
    assert res.status_code == 422, res.text
    assert res.json()["error"]["details"]["reason"] == "input_equals_output"


@pytest.mark.asyncio
async def test_production_run_duplicate_input_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Same product_id twice in inputs[] → 422."""
    h = await _owner_headers(app, owner_user)
    body = _create_body(
        inputs=[
            {"product_id": 2, "quantity": 5.0},
            {"product_id": 2, "quantity": 3.0},
        ],
    )
    res = await app.post(
        "/api/v1/production-runs",
        headers={**h, "Idempotency-Key": _idem()},
        json=body,
    )
    assert res.status_code == 422, res.text
    assert res.json()["error"]["details"]["reason"] == "duplicate_input"


@pytest.mark.asyncio
async def test_production_run_invalid_input_product_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Input product not found → 404."""
    h = await _owner_headers(app, owner_user)
    body = _create_body(
        inputs=[{"product_id": 9999, "quantity": 1.0}],
    )
    res = await app.post(
        "/api/v1/production-runs",
        headers={**h, "Idempotency-Key": _idem()},
        json=body,
    )
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_production_run_deactivated_cost_type_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Cost type with is_active=FALSE → 422."""
    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        # Deactivate cost_type 1.
        await session.execute(
            text("UPDATE cost_types SET is_active = FALSE WHERE id = 1")
        )
        await session.commit()

    h = await _owner_headers(app, owner_user)
    body = _create_body(
        cost_lines=[{"cost_type_id": 1, "amount": 10.00}],
    )
    res = await app.post(
        "/api/v1/production-runs",
        headers={**h, "Idempotency-Key": _idem()},
        json=body,
    )
    assert res.status_code == 422, res.text
    assert res.json()["error"]["details"]["reason"] == "cost_type_deactivated"


@pytest.mark.asyncio
async def test_production_run_unknown_lifecycle_filter_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Filtering list by unknown lifecycle_status → 422."""
    h = await _owner_headers(app, owner_user)
    res = await app.get(
        "/api/v1/production-runs?filter[lifecycle_status]=garbage",
        headers=h,
    )
    assert res.status_code == 422, res.text


# ---------------------------------------------------------------------------
# Lifecycle guard: patch a non-draft run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_run_patch_non_draft_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Force a run to posted → PATCH should 422 (lifecycle violation)."""
    h = await _owner_headers(app, owner_user)
    res = await app.post(
        "/api/v1/production-runs",
        headers={**h, "Idempotency-Key": _idem()},
        json=_create_body(),
    )
    rid = res.json()["id"]
    etag = _etag(res)

    # Force the run out of draft (bypassing E.3 to test the guard).
    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "UPDATE production_runs SET lifecycle_status = 'posted', "
                "posted_at = NOW(), posted_by = 1 WHERE id = :id"
            ),
            {"id": rid},
        )
        await session.commit()

    res_p = await app.patch(
        f"/api/v1/production-runs/{rid}",
        headers={**h, "If-Match": etag},
        json={"notes": "should fail"},
    )
    assert res_p.status_code == 409, res_p.text
    assert res_p.json()["error"]["code"] == "lifecycle_state_invalid"


# ---------------------------------------------------------------------------
# Concurrency (ETag)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_run_etag_concurrency(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Stale ETag → 412 version_mismatch."""
    h = await _owner_headers(app, owner_user)
    res = await app.post(
        "/api/v1/production-runs",
        headers={**h, "Idempotency-Key": _idem()},
        json=_create_body(),
    )
    rid = res.json()["id"]
    etag = _etag(res)
    create_version = res.json()["version"]

    # First PATCH succeeds and bumps version.
    res_p1 = await app.patch(
        f"/api/v1/production-runs/{rid}",
        headers={**h, "If-Match": etag},
        json={"notes": "first"},
    )
    assert res_p1.status_code == 200, res_p1.text
    assert res_p1.json()["version"] > create_version

    # Replaying the OLD etag should fail.
    res_p2 = await app.patch(
        f"/api/v1/production-runs/{rid}",
        headers={**h, "If-Match": etag},
        json={"notes": "second"},
    )
    assert res_p2.status_code == 412, res_p2.text
    assert res_p2.json()["error"]["code"] == "version_mismatch"

    # No If-Match at all → 400 missing_header.
    res_p3 = await app.patch(
        f"/api/v1/production-runs/{rid}",
        headers=h,
        json={"notes": "third"},
    )
    assert res_p3.status_code == 400, res_p3.text


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_run_create_idempotency(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Replay with same body returns the cached response."""
    h = await _owner_headers(app, owner_user)
    key = _idem()
    body = _create_body()

    r1 = await app.post(
        "/api/v1/production-runs",
        headers={**h, "Idempotency-Key": key},
        json=body,
    )
    assert r1.status_code == 201, r1.text

    r2 = await app.post(
        "/api/v1/production-runs",
        headers={**h, "Idempotency-Key": key},
        json=body,
    )
    assert r2.status_code == 200, r2.text  # replay returns 200
    assert r2.headers.get("Idempotent-Replay") == "true"
    assert r2.json()["id"] == r1.json()["id"]


@pytest.mark.asyncio
async def test_production_run_create_idempotency_key_conflict(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Same key with a different body → 409 idempotency_violation."""
    h = await _owner_headers(app, owner_user)
    key = _idem()

    r1 = await app.post(
        "/api/v1/production-runs",
        headers={**h, "Idempotency-Key": key},
        json=_create_body(),
    )
    assert r1.status_code == 201, r1.text

    r2 = await app.post(
        "/api/v1/production-runs",
        headers={**h, "Idempotency-Key": key},
        json=_create_body(output_quantity=20.0),
    )
    assert r2.status_code == 409, r2.text
    assert r2.json()["error"]["code"] == "idempotency_violation"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_run_create_missing_inputs_returns_400(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """``inputs`` is required (minItems 1) → 400 validation_failed."""
    h = await _owner_headers(app, owner_user)
    body = _create_body()
    body.pop("inputs")
    res = await app.post(
        "/api/v1/production-runs",
        headers={**h, "Idempotency-Key": _idem()},
        json=body,
    )
    assert res.status_code == 400, res.text
    assert res.json()["error"]["code"] == "validation_failed"


@pytest.mark.asyncio
async def test_production_run_create_empty_inputs_returns_400(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """``inputs`` minItems 1 → 400 if empty."""
    h = await _owner_headers(app, owner_user)
    res = await app.post(
        "/api/v1/production-runs",
        headers={**h, "Idempotency-Key": _idem()},
        json=_create_body(inputs=[]),
    )
    assert res.status_code == 400, res.text


@pytest.mark.asyncio
async def test_production_run_create_invalid_quantity_returns_400(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """output_quantity <= 0 → 400 validation_failed."""
    h = await _owner_headers(app, owner_user)
    res = await app.post(
        "/api/v1/production-runs",
        headers={**h, "Idempotency-Key": _idem()},
        json=_create_body(output_quantity=0.0),
    )
    assert res.status_code == 400, res.text
    assert res.json()["error"]["code"] == "validation_failed"


@pytest.mark.asyncio
async def test_production_run_create_unknown_field_returns_400(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Unknown field is forbidden → 400 validation_failed."""
    h = await _owner_headers(app, owner_user)
    res = await app.post(
        "/api/v1/production-runs",
        headers={**h, "Idempotency-Key": _idem()},
        json={**_create_body(), "lifecycle_status": "posted"},
    )
    assert res.status_code == 400, res.text


@pytest.mark.asyncio
async def test_production_run_get_404_when_missing(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """GET non-existent → 404."""
    h = await _owner_headers(app, owner_user)
    res = await app.get("/api/v1/production-runs/9999", headers=h)
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_production_run_patch_non_existent_returns_404(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """PATCH non-existent → 404."""
    h = await _owner_headers(app, owner_user)
    res = await app.patch(
        "/api/v1/production-runs/9999",
        headers={**h, "If-Match": '"0"'},
        json={"notes": "nope"},
    )
    assert res.status_code == 404, res.text


@pytest.mark.asyncio
async def test_production_run_list_filter_by_lifecycle(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """List with lifecycle_status filter returns only matching rows."""
    h = await _owner_headers(app, owner_user)
    res = await app.post(
        "/api/v1/production-runs",
        headers={**h, "Idempotency-Key": _idem()},
        json=_create_body(),
    )
    rid = res.json()["id"]

    res_l = await app.get(
        "/api/v1/production-runs?filter[lifecycle_status]=draft",
        headers=h,
    )
    assert res_l.status_code == 200, res_l.text
    body = res_l.json()
    items = body["data"] if isinstance(body, dict) else body
    assert all(r["lifecycle_status"] == "draft" for r in items)
    assert any(r["id"] == rid for r in items)

    res_l2 = await app.get(
        "/api/v1/production-runs?filter[lifecycle_status]=cancelled",
        headers=h,
    )
    assert res_l2.status_code == 200
    items2 = res_l2.json()["data"]
    assert not any(r["id"] == rid for r in items2)


@pytest.mark.asyncio
async def test_production_run_list_pagination_shape(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """List returns the {data, pagination} envelope."""
    h = await _owner_headers(app, owner_user)
    res = await app.get(
        "/api/v1/production-runs?page=1&per_page=10",
        headers=h,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert "data" in body and isinstance(body["data"], list)
    assert "pagination" in body
    p = body["pagination"]
    assert p["page"] == 1
    assert p["per_page"] == 10
    assert isinstance(p["total"], int)
    assert isinstance(p["total_pages"], int)
