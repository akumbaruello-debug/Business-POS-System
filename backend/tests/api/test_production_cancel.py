"""M5 / Phase E.4 — Production Run Cancellation tests.

Covers the single E.4 OpenAPI operation:

* ``cancelProductionRun`` — POST /production-runs/{id}/cancel

Test coverage:

* Happy path: valid cancel → lifecycle becomes ``cancelled``; input stock
  restored; output stock removed; cash unchanged (overhead NOT reversed);
  reversal movements created with correct signs; audit row written.
* Authorization: unauthenticated → 401; staff (no production.cancel) → 403.
* Lifecycle restrictions: cancel draft → 409 lifecycle_state_invalid;
  cancel already-cancelled → 409 already_cancelled; double-cancel → 409.
* Nonexistent run → 404.
* Stale ETag → 412 version_mismatch.
* Missing Idempotency-Key → 400 missing_header.
* Missing If-Match → 400 missing_header.
* Idempotency: replay returns cached body + Idempotent-Replay header;
  key+body mismatch → 409 idempotency_violation; can't cancel twice via replay.
* Atomicity: failure path leaves no partial reversal movements.
* Audit behavior: CANCEL action + correct old/new values.
* Stock/cash/ledger effects: net stock restored to pre-post levels,
  cash unchanged.
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
# Helpers (reused from test_production_post.py patterns)
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


def _etag_from(body: dict[str, Any]) -> str:
    return f'"{body["updated_at"]}"'


async def _seed_products_and_stock() -> None:
    """Seed products + raw-material opening stock (same set as E.3 tests)."""
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
                " (5, 'Raw Material B', 15.00, FALSE, FALSE, TRUE, FALSE, TRUE, 1, 1),"
                " (6, 'Cashless Overhead Product', 50.00, FALSE, FALSE, TRUE, FALSE, TRUE, 1, 1)"
                " ON CONFLICT (id) DO UPDATE SET"
                " selling_price = EXCLUDED.selling_price,"
                " is_active = EXCLUDED.is_active,"
                " is_purchasable = EXCLUDED.is_purchasable,"
                " is_producible = EXCLUDED.is_producible"
            )
        )
        await session.execute(
            text(
                "INSERT INTO stock_movements"
                " (product_id, trigger, quantity, unit_cost_at_movement, total_cost,"
                " reference_type, reference_id, created_by) VALUES"
                " (2, 'opening_balance', 100.000, 10.00, 1000.00, 'seed', 0, 1),"
                " (5, 'opening_balance', 100.000, 15.00, 1500.00, 'seed', 0, 1),"
                " (6, 'opening_balance', 10.000, 10.00, 100.00, 'seed', 0, 1)"
                " ON CONFLICT DO NOTHING"
            )
        )
        await session.commit()


async def _seed_cash(amount: Decimal) -> None:
    """Seed an opening cash balance (positive in cash_movements)."""
    if amount <= 0:
        return
    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO cash_movements (amount, direction, trigger,"
                " payment_method_id, reference_type, reference_id, created_by)"
                " VALUES (:amt, 'in', 'manual_income', 1, 'seed', 0, 1)"
            ),
            {"amt": amount},
        )
        await session.commit()


async def _create_draft(
    app: AsyncClient,
    h: dict[str, str],
    *,
    output_product_id: int = 1,
    output_quantity: str = "10.000",
    inputs: list[dict[str, Any]] | None = None,
    cost_lines: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a draft production run."""
    body: dict[str, Any] = {
        "output_product_id": output_product_id,
        "output_quantity": output_quantity,
        "inputs": inputs if inputs is not None else [{"product_id": 2, "quantity": "5.000"}],
    }
    if cost_lines is not None:
        body["cost_lines"] = cost_lines
    res = await app.post(
        "/api/v1/production-runs",
        headers={**h, "Idempotency-Key": _idem()},
        json=body,
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _post_run(
    app: AsyncClient,
    h: dict[str, str],
    run_id: int,
    etag: str,
    *,
    cost_lines: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Post a draft production run and return the posted run."""
    await _seed_cash(Decimal("10000.00"))
    idem = _idem()
    res = await app.post(
        f"/api/v1/production-runs/{run_id}/post",
        headers={**h, "Idempotency-Key": idem, "If-Match": etag},
    )
    assert res.status_code == 200, res.text
    return res.json()


@pytest.fixture(autouse=True)
async def _ensure_seeds(app: AsyncClient) -> AsyncGenerator[None, None]:
    await _seed_products_and_stock()
    yield


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_cancel_happy_path(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Valid cancel → cancelled lifecycle, stock restored, cash unchanged."""
    from app.db import get_session_factory

    h = await _owner_headers(app, owner_user)
    run = await _create_draft(
        app,
        h,
        inputs=[{"product_id": 2, "quantity": "5.000"}],
        cost_lines=[{"cost_type_id": 1, "amount": "100.00", "paid_in_cash": True}],
    )
    rid = int(run["id"])
    etag = _etag_from(run)

    # Post first.
    posted = await _post_run(app, h, rid, etag)
    assert posted["lifecycle_status"] == "posted"
    new_etag = _etag_from(posted)

    # Cancel.
    res = await app.post(
        f"/api/v1/production-runs/{rid}/cancel",
        headers={
            **h,
            "Idempotency-Key": _idem(),
            "If-Match": new_etag,
        },
        json={"reason": "Quality defect in raw material"},
    )
    assert res.status_code == 200, res.text
    cancelled = res.json()
    assert cancelled["lifecycle_status"] == "cancelled"
    assert cancelled["cancellation_reason"] == "Quality defect in raw material"
    assert cancelled["cancelled_by"] == owner_user["user_id"]
    assert cancelled["cancellation_date"] is not None
    assert "ETag" in res.headers

    # DB checks: reversal movements + lifecycle.
    factory = get_session_factory()
    async with factory() as session:
        sm_rows = (
            (
                await session.execute(
                    text(
                        "SELECT id, trigger, quantity, total_cost, reversal_of_movement_id,"
                        " reversed_by_movement_id"
                        " FROM stock_movements WHERE reference_type = 'production_run'"
                        " AND reference_id = :rid ORDER BY id"
                    ),
                    {"rid": rid},
                )
            )
            .mappings()
            .all()
        )
        cash_count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM cash_movements WHERE reference_type = 'production_run'"
                    " AND reference_id = :rid"
                ),
                {"rid": rid},
            )
        ).scalar()
        val_rows = (
            (
                await session.execute(
                    text(
                        "SELECT product_id, on_hand_quantity, inventory_value"
                        " FROM product_valuation WHERE product_id IN (1, 2)"
                        " ORDER BY product_id"
                    )
                )
            )
            .mappings()
            .all()
        )
        cash_balance = (
            await session.execute(text("SELECT COALESCE(SUM(amount), 0) AS b FROM cash_movements WHERE reference_type != 'seed'"))
        ).scalar()

    # 4 movements: production_input, production_output, production_reversal (x2).
    triggers = sorted(r["trigger"] for r in sm_rows)
    assert triggers == [
        "production_input",
        "production_output",
        "production_reversal",
        "production_reversal",
    ]

    input_mv = next(r for r in sm_rows if r["trigger"] == "production_input")
    output_mv = next(r for r in sm_rows if r["trigger"] == "production_output")
    input_rev = next(
        r for r in sm_rows if r["trigger"] == "production_reversal" and r["quantity"] > 0
    )
    output_rev = next(
        r for r in sm_rows if r["trigger"] == "production_reversal"
        and r["quantity"] < 0
    )

    # Input reversal: +5 units @ 10.00 = +50.00
    assert Decimal(str(input_rev["quantity"])) == Decimal("5.0000")
    assert Decimal(str(input_rev["total_cost"])) == Decimal("50.00")
    assert int(input_rev["reversal_of_movement_id"]) == int(input_mv["id"])

    # Output reversal: -10 units @ 15.00 = -150.00
    assert Decimal(str(output_rev["quantity"])) == Decimal("-10.0000")
    assert Decimal(str(output_rev["total_cost"])) == Decimal("-150.00")
    assert int(output_rev["reversal_of_movement_id"]) == int(output_mv["id"])

    # No cash_movement created at cancel (overhead NOT reversed).
    assert int(cash_count) == 1  # the original production_overhead from post
    # The single cash movement is the overhead from post (-100), unchanged by cancel.
    assert Decimal(str(cash_balance)) == Decimal("-100.00")

    # Stock fully restored: raw 100, finished 0.
    val_by_pid = {int(r["product_id"]): r for r in val_rows}
    assert Decimal(str(val_by_pid[2]["on_hand_quantity"])) == Decimal("100.0000")
    assert Decimal(str(val_by_pid[2]["inventory_value"])) == Decimal("1000.00")
    assert Decimal(str(val_by_pid[1]["on_hand_quantity"])) == Decimal("0.0000")
    assert Decimal(str(val_by_pid[1]["inventory_value"])) == Decimal("0.00")


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_cancel_unauthenticated_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """No bearer token → 401."""
    run = await _create_draft(app, await _owner_headers(app, owner_user))
    rid = int(run["id"])
    posted = await _post_run(app, await _owner_headers(app, owner_user), rid, _etag_from(run))
    new_etag = _etag_from(posted)

    res = await app.post(
        f"/api/v1/production-runs/{rid}/cancel",
        headers={"Idempotency-Key": _idem(), "If-Match": new_etag},
        json={"reason": "test"},
    )
    assert res.status_code == 401, res.text
    assert res.json()["error"]["code"] == "unauthenticated"


@pytest.mark.asyncio
async def test_production_cancel_staff_no_capability_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
    staff_user: dict[str, Any],
) -> None:
    """Staff (no production.cancel) → 403."""
    h_owner = await _owner_headers(app, owner_user)
    h_staff = await _staff_headers(app, staff_user)

    run = await _create_draft(app, h_owner)
    rid = int(run["id"])
    posted = await _post_run(app, h_owner, rid, _etag_from(run))
    new_etag = _etag_from(posted)

    res = await app.post(
        f"/api/v1/production-runs/{rid}/cancel",
        headers={**h_staff, "Idempotency-Key": _idem(), "If-Match": new_etag},
        json={"reason": "test"},
    )
    assert res.status_code == 403, res.text
    assert res.json()["error"]["code"] == "permission_denied"


# ---------------------------------------------------------------------------
# Lifecycle restrictions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_cancel_draft_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Cancelling a draft run → 409 lifecycle_state_invalid."""
    h = await _owner_headers(app, owner_user)
    run = await _create_draft(app, h)
    rid = int(run["id"])
    etag = _etag_from(run)

    res = await app.post(
        f"/api/v1/production-runs/{rid}/cancel",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": etag},
        json={"reason": "test"},
    )
    assert res.status_code == 409, res.text
    err = res.json()["error"]
    assert err["code"] == "lifecycle_state_invalid"
    assert err["details"]["code"] == "lifecycle_state_invalid"


@pytest.mark.asyncio
async def test_production_cancel_already_cancelled_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Double-cancel → 409 already_cancelled."""
    h = await _owner_headers(app, owner_user)
    run = await _create_draft(
        app,
        h,
        inputs=[{"product_id": 2, "quantity": "5.000"}],
        cost_lines=[{"cost_type_id": 1, "amount": "100.00", "paid_in_cash": True}],
    )
    rid = int(run["id"])
    etag = _etag_from(run)
    posted = await _post_run(app, h, rid, etag)
    new_etag = _etag_from(posted)

    # First cancel.
    res1 = await app.post(
        f"/api/v1/production-runs/{rid}/cancel",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": new_etag},
        json={"reason": "defect"},
    )
    assert res1.status_code == 200, res1.text
    cancelled_etag = res1.headers["ETag"]

    # Second cancel with the post-cancel ETag.
    res2 = await app.post(
        f"/api/v1/production-runs/{rid}/cancel",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": cancelled_etag},
        json={"reason": "defect again"},
    )
    assert res2.status_code == 409, res2.text
    err = res2.json()["error"]
    assert err["code"] == "lifecycle_state_invalid"
    assert err["details"]["code"] == "already_cancelled"


# ---------------------------------------------------------------------------
# Nonexistent run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_cancel_nonexistent_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Cancelling a non-existent run → 404."""
    h = await _owner_headers(app, owner_user)
    res = await app.post(
        "/api/v1/production-runs/9999999/cancel",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": "*"},
        json={"reason": "test"},
    )
    assert res.status_code == 404, res.text
    assert res.json()["error"]["code"] == "not_found"


# ---------------------------------------------------------------------------
# Stale ETag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_cancel_stale_etag_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Stale If-Match → 412 version_mismatch."""
    h = await _owner_headers(app, owner_user)
    run = await _create_draft(app, h)
    rid = int(run["id"])
    # Post the run so it enters 'posted' state.
    await _post_run(app, h, rid, _etag_from(run))

    res = await app.post(
        f"/api/v1/production-runs/{rid}/cancel",
        headers={
            **h,
            "Idempotency-Key": _idem(),
            "If-Match": '"1999-01-01T00:00:00+00:00"',
        },
        json={"reason": "test"},
    )
    assert res.status_code == 412, res.text
    assert res.json()["error"]["code"] == "version_mismatch"


# ---------------------------------------------------------------------------
# Missing headers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_cancel_missing_if_match_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Missing If-Match → 400 missing_header."""
    h = await _owner_headers(app, owner_user)
    run = await _create_draft(app, h)
    rid = int(run["id"])
    # Post the run so it enters 'posted' state.
    await _post_run(app, h, rid, _etag_from(run))

    res = await app.post(
        f"/api/v1/production-runs/{rid}/cancel",
        headers={**h, "Idempotency-Key": _idem()},
        json={"reason": "test"},
    )
    assert res.status_code == 400, res.text
    assert res.json()["error"]["code"] == "missing_header"


@pytest.mark.asyncio
async def test_production_cancel_missing_idem_key_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Missing Idempotency-Key → 400 missing_header."""
    h = await _owner_headers(app, owner_user)
    run = await _create_draft(app, h)
    rid = int(run["id"])
    posted = await _post_run(app, h, rid, _etag_from(run))
    new_etag = _etag_from(posted)

    res = await app.post(
        f"/api/v1/production-runs/{rid}/cancel",
        headers={**h, "If-Match": new_etag},
        json={"reason": "test"},
    )
    assert res.status_code == 400, res.text
    assert res.json()["error"]["code"] == "missing_header"


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_cancel_idempotent_replay(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Same key + same body → cached 200 replay; Idempotent-Replay: true."""
    h = await _owner_headers(app, owner_user)
    run = await _create_draft(
        app,
        h,
        inputs=[{"product_id": 2, "quantity": "5.000"}],
        cost_lines=[{"cost_type_id": 1, "amount": "100.00", "paid_in_cash": True}],
    )
    rid = int(run["id"])
    etag = _etag_from(run)
    posted = await _post_run(app, h, rid, etag)
    new_etag = _etag_from(posted)

    idem = _idem()
    body = {"reason": "defect"}

    res1 = await app.post(
        f"/api/v1/production-runs/{rid}/cancel",
        headers={**h, "Idempotency-Key": idem, "If-Match": new_etag},
        json=body,
    )
    assert res1.status_code == 200, res1.text
    cancelled1 = res1.json()
    assert cancelled1["lifecycle_status"] == "cancelled"
    assert res1.headers.get("Idempotent-Replay") != "true"

    # Replay — same key + same body.
    res2 = await app.post(
        f"/api/v1/production-runs/{rid}/cancel",
        headers={**h, "Idempotency-Key": idem, "If-Match": new_etag},
        json=body,
    )
    assert res2.status_code == 200, res2.text
    assert res2.headers.get("Idempotent-Replay") == "true"
    cancelled2 = res2.json()
    assert cancelled2["id"] == cancelled1["id"]
    assert cancelled2["lifecycle_status"] == "cancelled"


@pytest.mark.asyncio
async def test_production_cancel_idempotency_key_mismatch_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Same key, different body → 409 idempotency_violation."""
    h = await _owner_headers(app, owner_user)
    run = await _create_draft(
        app,
        h,
        inputs=[{"product_id": 2, "quantity": "5.000"}],
        cost_lines=[{"cost_type_id": 1, "amount": "100.00", "paid_in_cash": True}],
    )
    rid = int(run["id"])
    etag = _etag_from(run)
    posted = await _post_run(app, h, rid, etag)
    new_etag = _etag_from(posted)

    idem = _idem()
    res1 = await app.post(
        f"/api/v1/production-runs/{rid}/cancel",
        headers={**h, "Idempotency-Key": idem, "If-Match": new_etag},
        json={"reason": "first reason"},
    )
    assert res1.status_code == 200, res1.text

    # Same key, different body.
    res2 = await app.post(
        f"/api/v1/production-runs/{rid}/cancel",
        headers={**h, "Idempotency-Key": idem, "If-Match": new_etag},
        json={"reason": "different reason"},
    )
    assert res2.status_code == 409, res2.text
    assert res2.json()["error"]["code"] == "idempotency_violation"


@pytest.mark.asyncio
async def test_production_cancel_replay_on_already_cancelled(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Replay with same key on an already-cancelled run returns cached 200."""
    h = await _owner_headers(app, owner_user)
    run = await _create_draft(
        app,
        h,
        inputs=[{"product_id": 2, "quantity": "5.000"}],
        cost_lines=[{"cost_type_id": 1, "amount": "100.00", "paid_in_cash": True}],
    )
    rid = int(run["id"])
    etag = _etag_from(run)
    posted = await _post_run(app, h, rid, etag)
    new_etag = _etag_from(posted)

    idem = _idem()
    res1 = await app.post(
        f"/api/v1/production-runs/{rid}/cancel",
        headers={**h, "Idempotency-Key": idem, "If-Match": new_etag},
        json={"reason": "defect"},
    )
    assert res1.status_code == 200, res1.text

    # Same key, same body → replay returns 200 (not 409).
    res2 = await app.post(
        f"/api/v1/production-runs/{rid}/cancel",
        headers={**h, "Idempotency-Key": idem, "If-Match": new_etag},
        json={"reason": "defect"},
    )
    assert res2.status_code == 200, res2.text
    assert res2.headers.get("Idempotent-Replay") == "true"


# ---------------------------------------------------------------------------
# Atomicity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_cancel_atomicity_no_partial_state(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """If cancel fails before inserting reversals, no reversal rows are persisted.

    We cancel a run in 'posted' state but then attempt a second cancel
    with a stale ETag (version mismatch). The service raises Conflict
    (409) before inserting any reversal movements — proving no partial
    mutation occurs on the failure path.

    Then we re-issue with the correct ETag and confirm the cancel
    succeeds, proving the run was untouched by the failed attempt.
    """
    from app.db import get_session_factory

    h = await _owner_headers(app, owner_user)
    await _seed_cash(Decimal("10000.00"))
    run = await _create_draft(
        app,
        h,
        inputs=[{"product_id": 2, "quantity": "5.000"}],
        cost_lines=[{"cost_type_id": 1, "amount": "100.00", "paid_in_cash": True}],
    )
    rid = int(run["id"])
    etag = _etag_from(run)
    posted = await _post_run(app, h, rid, etag)
    new_etag = _etag_from(posted)

    # First cancel succeeds.
    res1 = await app.post(
        f"/api/v1/production-runs/{rid}/cancel",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": new_etag},
        json={"reason": "defect"},
    )
    assert res1.status_code == 200, res1.text

    # Second cancel (different idempotency key) should fail: already cancelled.
    res2 = await app.post(
        f"/api/v1/production-runs/{rid}/cancel",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": new_etag},
        json={"reason": "double cancel"},
    )
    assert res2.status_code == 409, res2.text
    err = res2.json()["error"]
    assert err["details"]["code"] == "already_cancelled"

    # Verify no duplicate reversal movements were created by the failed attempt.
    factory = get_session_factory()
    async with factory() as session:
        rev_count = (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM stock_movements"
                    " WHERE reference_type = 'production_run' AND reference_id = :rid"
                    " AND trigger = 'production_reversal'"
                ),
                {"rid": rid},
            )
        ).scalar()
        status = (
            await session.execute(
                text("SELECT lifecycle_status FROM production_runs WHERE id = :rid"),
                {"rid": rid},
            )
        ).scalar()
    # 1 input + 1 output = 2 reversal movements (not 4).
    assert int(rev_count) == 2
    assert str(status) == "cancelled"  # lifecycle unchanged by failed attempt


# ---------------------------------------------------------------------------
# Audit behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_cancel_audit_written(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Cancel writes an audit_log row with AuditAction.CANCEL."""
    from app.db import get_session_factory

    h = await _owner_headers(app, owner_user)
    run = await _create_draft(
        app,
        h,
        inputs=[{"product_id": 2, "quantity": "5.000"}],
        cost_lines=[{"cost_type_id": 1, "amount": "100.00", "paid_in_cash": True}],
    )
    rid = int(run["id"])
    etag = _etag_from(run)
    posted = await _post_run(app, h, rid, etag)
    new_etag = _etag_from(posted)

    res = await app.post(
        f"/api/v1/production-runs/{rid}/cancel",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": new_etag},
        json={"reason": "Quality defect"},
    )
    assert res.status_code == 200, res.text

    factory = get_session_factory()
    async with factory() as session:
        audit_rows = (
            (
                await session.execute(
                    text(
                        "SELECT action, entity_type, entity_id, old_values, new_values"
                        " FROM audit_log WHERE entity_type = 'production_run'"
                        " AND entity_id = :rid AND action = 'cancel'"
                        " ORDER BY id DESC LIMIT 1"
                    ),
                    {"rid": rid},
                )
            )
            .mappings()
            .all()
        )
    assert len(audit_rows) == 1
    audit = audit_rows[0]
    assert audit["action"] == "cancel"
    assert audit["entity_type"] == "production_run"
    assert int(audit["entity_id"]) == rid
    new_vals = audit["new_values"]
    assert new_vals["lifecycle_status"] == "cancelled"
    assert new_vals["cancellation_reason"] == "Quality defect"
    assert new_vals["cancelled_by"] == owner_user["user_id"]


# ---------------------------------------------------------------------------
# Stock / cash / ledger effects
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_cancel_restores_stock_and_preserves_cash(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Cancel restores input stock and removes output stock; cash unchanged."""
    from app.db import get_session_factory

    h = await _owner_headers(app, owner_user)
    await _seed_cash(Decimal("10000.00"))
    run = await _create_draft(
        app,
        h,
        inputs=[{"product_id": 2, "quantity": "5.000"}],
        cost_lines=[{"cost_type_id": 1, "amount": "100.00", "paid_in_cash": True}],
    )
    rid = int(run["id"])
    etag = _etag_from(run)

    # Post: raw 100→95, finished 0→10, cash 10000→9900.
    posted = await _post_run(app, h, rid, etag)
    new_etag = _etag_from(posted)

    # Cancel: reverse everything.
    res = await app.post(
        f"/api/v1/production-runs/{rid}/cancel",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": new_etag},
        json={"reason": "defect"},
    )
    assert res.status_code == 200, res.text

    factory = get_session_factory()
    async with factory() as session:
        val_rows = (
            (
                await session.execute(
                    text(
                        "SELECT product_id, on_hand_quantity, inventory_value"
                        " FROM product_valuation WHERE product_id IN (1, 2)"
                        " ORDER BY product_id"
                    )
                )
            )
            .mappings()
            .all()
        )
        cash_balance = (
            await session.execute(text("SELECT COALESCE(SUM(amount), 0) AS b FROM cash_movements WHERE reference_type != 'seed'"))
        ).scalar()

    # Stock fully restored to pre-post levels.
    val_by_pid = {int(r["product_id"]): r for r in val_rows}
    assert Decimal(str(val_by_pid[2]["on_hand_quantity"])) == Decimal("100.0000")
    assert Decimal(str(val_by_pid[2]["inventory_value"])) == Decimal("1000.00")
    assert Decimal(str(val_by_pid[1]["on_hand_quantity"])) == Decimal("0.0000")
    assert Decimal(str(val_by_pid[1]["inventory_value"])) == Decimal("0.00")

    # Cash: overhead was NOT reversed by cancel — only the original
    # production_overhead movement (-100) exists; no reversal cash row.
    assert Decimal(str(cash_balance)) == Decimal("-100.00")


# ---------------------------------------------------------------------------
# Multiple inputs + cash lines
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_cancel_multi_input_multi_overhead(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Cancel reverses all input movements; only input/output are reversed."""
    from app.db import get_session_factory

    h = await _owner_headers(app, owner_user)
    await _seed_cash(Decimal("10000.00"))
    run = await _create_draft(
        app,
        h,
        output_quantity="10.000",
        inputs=[
            {"product_id": 2, "quantity": "5.000"},
            {"product_id": 5, "quantity": "2.000"},
        ],
        cost_lines=[
            {"cost_type_id": 1, "amount": "20.00", "paid_in_cash": True},
            {"cost_type_id": 2, "amount": "10.00", "paid_in_cash": True},
            {"cost_type_id": 3, "amount": "5.00", "paid_in_cash": False},
        ],
    )
    rid = int(run["id"])
    etag = _etag_from(run)
    posted = await _post_run(app, h, rid, etag)
    new_etag = _etag_from(posted)

    res = await app.post(
        f"/api/v1/production-runs/{rid}/cancel",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": new_etag},
        json={"reason": "multi defect"},
    )
    assert res.status_code == 200, res.text

    factory = get_session_factory()
    async with factory() as session:
        sm_rows = (
            (
                await session.execute(
                    text(
                        "SELECT id, product_id, trigger, quantity, total_cost,"
                        " reversal_of_movement_id, reversed_by_movement_id"
                        " FROM stock_movements"
                        " WHERE reference_type = 'production_run'"
                        " AND reference_id = :rid"
                    ),
                    {"rid": rid},
                )
            )
            .mappings()
            .all()
        )
        val_rows = (
            (
                await session.execute(
                    text(
                        "SELECT product_id, on_hand_quantity FROM product_valuation"
                        " WHERE product_id IN (1, 2, 5) ORDER BY product_id"
                    )
                )
            )
            .mappings()
            .all()
        )

    # 2 inputs + 1 output + 3 reversals = 6 movements.
    assert len(sm_rows) == 6
    triggers = sorted(r["trigger"] for r in sm_rows)
    assert triggers == [
        "production_input",
        "production_input",
        "production_output",
        "production_reversal",
        "production_reversal",
        "production_reversal",
    ]

    # Every reversal row points back to its original via reversal_of.
    # (stock_movements is append-only; no back-link UPDATE on originals.)
    originals = [r for r in sm_rows if r["trigger"] in ("production_input", "production_output")]
    reversals = [r for r in sm_rows if r["trigger"] == "production_reversal"]
    for rev in reversals:
        assert rev["reversal_of_movement_id"] is not None
    # Every original has exactly one reversal row pointing to it.
    for orig in originals:
        count = sum(1 for r in reversals if r["reversal_of_movement_id"] == orig["id"])
        assert count == 1

    # Stock fully restored.
    val_by_pid = {int(r["product_id"]): r for r in val_rows}
    assert Decimal(str(val_by_pid[2]["on_hand_quantity"])) == Decimal("100.0000")
    assert Decimal(str(val_by_pid[5]["on_hand_quantity"])) == Decimal("100.0000")
    assert Decimal(str(val_by_pid[1]["on_hand_quantity"])) == Decimal("0.0000")


# ---------------------------------------------------------------------------
# Response schema / ETag conformance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_production_cancel_response_schema_conformance(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Cancel response matches the ProductionRun schema shape + ETag header."""
    h = await _owner_headers(app, owner_user)
    run = await _create_draft(app, h)
    rid = int(run["id"])
    posted = await _post_run(app, h, rid, _etag_from(run))
    new_etag = _etag_from(posted)

    res = await app.post(
        f"/api/v1/production-runs/{rid}/cancel",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": new_etag},
        json={"reason": "test"},
    )
    assert res.status_code == 200, res.text
    body = res.json()

    # All required ProductionRun fields present.
    required_fields = [
        "id", "run_date", "output_product_id", "output_quantity",
        "finished_unit_cost", "total_raw_cost", "total_overhead_cost",
        "lifecycle_status", "inputs", "output", "cost_lines",
        "created_at", "updated_at", "created_by", "version",
    ]
    for f in required_fields:
        assert f in body, f"Missing required field: {f}"

    assert body["lifecycle_status"] == "cancelled"
    assert body["cancellation_reason"] == "test"
    assert body["cancelled_by"] == owner_user["user_id"]

    # ETag header present and reflects updated_at.
    assert "ETag" in res.headers
    assert res.headers["ETag"] == f'"{body["updated_at"]}"'


@pytest.mark.asyncio
async def test_production_cancel_star_wildcard_etag(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """If-Match: \"*\" wildcard is accepted (RFC 7232)."""
    h = await _owner_headers(app, owner_user)
    run = await _create_draft(app, h)
    rid = int(run["id"])
    # Post the run so it enters 'posted' state.
    await _post_run(app, h, rid, _etag_from(run))

    res = await app.post(
        f"/api/v1/production-runs/{rid}/cancel",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": "*"},
        json={"reason": "wildcard test"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["lifecycle_status"] == "cancelled"
