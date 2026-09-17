"""Phase-E purchase-return finalization, arrival, override + cancel-gate tests.

Covers the new endpoints added for the locked finalization contract:
* POST /purchase-returns/{id}/finalize   (owner + staff)  -> finalized_at
* POST /purchase-returns/{id}/arrival     (owner + staff)  -> supplier_arrival_at
* POST /purchase-returns/{id}/override    (owner only)     -> overdue_override_at
* cancel gate: finalized returns cannot be cancelled
* parent-purchase lifecycle recovery after unfinalized-return cancellation

Mutations omit the If-Match header (it is optional on these endpoints via
parse_if_match_optional; passing the stale create-time ETag yields 412 since
create's ETag is updated_at while cancel/finalize/arrival compare version).
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient

from tests.api.test_purchases import (
    _idem,
    _owner_headers,
    _seed_supplier_and_product,
    _staff_headers,
)


@pytest.fixture(autouse=True)
async def _ensure_seeds(app: AsyncClient) -> AsyncGenerator[None, None]:
    await _seed_supplier_and_product()
    yield


async def _create_posted_purchase_with_line(
    app: AsyncClient, headers: dict[str, str]
) -> tuple[int, int]:
    """Return (purchase_id, line_id) for a posted 20-unit @ 10.00 purchase."""
    res = await app.post(
        "/api/v1/purchases",
        headers={**headers, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]
    res_line = await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**headers, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "20.000", "unit_price": "10.00"},
    )
    line_id = res_line.json()["id"]
    await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**headers, "Idempotency-Key": _idem()},
        json={},
    )
    return pid, line_id


async def _create_return(
    app: AsyncClient, headers: dict[str, str], pid: int, line_id: int, qty: str = "5.000"
) -> int:
    res = await app.post(
        f"/api/v1/purchases/{pid}/returns",
        headers={**headers, "Idempotency-Key": _idem()},
        json={
            "reason": "damaged",
            "lines": [{"purchase_line_id": line_id, "quantity": qty}],
        },
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


# ---------------------------------------------------------------------------
# Finalize (owner + staff)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_return_owner_then_cancel_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Finalizing a return blocks its cancellation; finalized_at is set."""
    h = await _owner_headers(app, owner_user)
    pid, line_id = await _create_posted_purchase_with_line(app, h)
    ret_id = await _create_return(app, h, pid, line_id)

    res = await app.post(
        f"/api/v1/purchase-returns/{ret_id}/finalize",
        headers={**h, "Idempotency-Key": _idem()},
        json={"reason": "courier confirmed"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["finalized_at"] is not None

    # Cancel after finalize -> 409 return_finalized
    res_cancel = await app.post(
        f"/api/v1/purchase-returns/{ret_id}/cancel",
        headers={**h, "Idempotency-Key": _idem()},
        json={"reason": "vendor dispute"},
    )
    assert res_cancel.status_code == 409
    assert "finalized" in res_cancel.json()["error"]["message"].lower()


@pytest.mark.asyncio
async def test_finalize_return_idempotent_replay(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Same Idempotency-Key + same body -> 200 replay."""
    h = await _owner_headers(app, owner_user)
    pid, line_id = await _create_posted_purchase_with_line(app, h)
    ret_id = await _create_return(app, h, pid, line_id)

    idk = _idem()
    body = {"reason": "courier confirmed"}
    r1 = await app.post(
        f"/api/v1/purchase-returns/{ret_id}/finalize",
        headers={**h, "Idempotency-Key": idk},
        json=body,
    )
    assert r1.status_code == 200, r1.text
    r2 = await app.post(
        f"/api/v1/purchase-returns/{ret_id}/finalize",
        headers={**h, "Idempotency-Key": idk},
        json=body,
    )
    assert r2.status_code == 200
    assert r2.headers.get("Idempotent-Replay") == "true"


@pytest.mark.asyncio
async def test_finalize_staff_allowed(
    app: AsyncClient,
    owner_user: dict[str, Any],
    staff_user: dict[str, Any],
) -> None:
    """Staff HAS purchase.return.finalize -> can finalize."""
    h_own = await _owner_headers(app, owner_user)
    h_staff = await _staff_headers(app, staff_user)
    pid, line_id = await _create_posted_purchase_with_line(app, h_own)
    ret_id = await _create_return(app, h_own, pid, line_id)

    res = await app.post(
        f"/api/v1/purchase-returns/{ret_id}/finalize",
        headers={**h_staff, "Idempotency-Key": _idem()},
        json={"reason": "courier confirmed"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["finalized_at"] is not None


# ---------------------------------------------------------------------------
# Arrival (owner + staff), 5-day deadline, overdue override (owner only)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_override_without_arrival_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """No supplier_arrival_at -> override rejected (409)."""
    h = await _owner_headers(app, owner_user)
    pid, line_id = await _create_posted_purchase_with_line(app, h)
    ret_id = await _create_return(app, h, pid, line_id)

    res = await app.post(
        f"/api/v1/purchase-returns/{ret_id}/override-expired-window",
        headers={**h, "Idempotency-Key": _idem()},
        json={"reason": "owner says yes"},
    )
    assert res.status_code == 409
    assert "supplier arrival" in res.json()["error"]["message"].lower()


@pytest.mark.asyncio
async def test_arrival_staff_then_duplicate_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
    staff_user: dict[str, Any],
) -> None:
    """Staff can record arrival; duplicate arrival is a 409 conflict."""
    h_own = await _owner_headers(app, owner_user)
    h_staff = await _staff_headers(app, staff_user)
    pid, line_id = await _create_posted_purchase_with_line(app, h_own)
    ret_id = await _create_return(app, h_own, pid, line_id)

    res = await app.post(
        f"/api/v1/purchase-returns/{ret_id}/arrival",
        headers={**h_staff, "Idempotency-Key": _idem()},
        json={},
    )
    assert res.status_code == 200, res.text
    assert res.json()["supplier_arrival_at"] is not None

    # Duplicate arrival -> 409
    res2 = await app.post(
        f"/api/v1/purchase-returns/{ret_id}/arrival",
        headers={**h_staff, "Idempotency-Key": _idem()},
        json={},
    )
    assert res2.status_code == 409


@pytest.mark.asyncio
async def test_arrival_then_override_rejected_not_overdue(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """After arrival, override is rejected until the 5-day window expires."""
    h = await _owner_headers(app, owner_user)
    pid, line_id = await _create_posted_purchase_with_line(app, h)
    ret_id = await _create_return(app, h, pid, line_id)

    res_arr = await app.post(
        f"/api/v1/purchase-returns/{ret_id}/arrival",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )
    assert res_arr.status_code == 200, res_arr.text

    res_ov = await app.post(
        f"/api/v1/purchase-returns/{ret_id}/override-expired-window",
        headers={**h, "Idempotency-Key": _idem()},
        json={"reason": "too early"},
    )
    assert res_ov.status_code == 409
    assert "overdue" in res_ov.json()["error"]["message"].lower()


@pytest.mark.asyncio
async def test_owner_can_override_expired_window(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """After advancing the DB clock past 5 days, Owner can override."""
    h = await _owner_headers(app, owner_user)
    pid, line_id = await _create_posted_purchase_with_line(app, h)
    ret_id = await _create_return(app, h, pid, line_id)

    res_arr = await app.post(
        f"/api/v1/purchase-returns/{ret_id}/arrival",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )
    assert res_arr.status_code == 200, res_arr.text

    # Fast-forward the DB-side supplier_arrival_at past the 5-day deadline so
    # the derived overdue flag flips true.
    past = datetime.now(UTC) - timedelta(days=6)
    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        from sqlalchemy import text

        await session.execute(
            text("UPDATE purchase_returns SET supplier_arrival_at = :ts "
                 "WHERE id = :id"),
            {"ts": past, "id": ret_id},
        )
        await session.commit()

    res_ov = await app.post(
        f"/api/v1/purchase-returns/{ret_id}/override-expired-window",
        headers={**h, "Idempotency-Key": _idem()},
        json={"reason": "supplier confirmed late"},
    )
    assert res_ov.status_code == 200, res_ov.text
    assert res_ov.json()["overdue_override_at"] is not None


@pytest.mark.asyncio
async def test_override_staff_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
    staff_user: dict[str, Any],
) -> None:
    """Staff lacks purchase.return.override -> 403."""
    h_own = await _owner_headers(app, owner_user)
    h_staff = await _staff_headers(app, staff_user)
    pid, line_id = await _create_posted_purchase_with_line(app, h_own)
    ret_id = await _create_return(app, h_own, pid, line_id)

    # Staff attempt to override
    res = await app.post(
        f"/api/v1/purchase-returns/{ret_id}/override-expired-window",
        headers={**h_staff, "Idempotency-Key": _idem()},
        json={"reason": "staff tries"},
    )
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# Parent-purchase lifecycle recovery after unfinalized-return cancellation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_unfinalized_return_recovery_to_posted(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Full return then cancel (unfinalized) -> parent recovers to 'posted'
    (payment_state unpaid, active_returned == 0)."""
    h = await _owner_headers(app, owner_user)
    pid, line_id = await _create_posted_purchase_with_line(app, h)
    ret_id = await _create_return(app, h, pid, line_id, qty="20.000")

    # Parent should be 'returned' after the full return is posted
    res_p = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    assert res_p.status_code == 200
    assert res_p.json()["lifecycle_status"] == "returned"

    # Cancel the unfinalized return (no If-Match — optional on cancel)
    res_c = await app.post(
        f"/api/v1/purchase-returns/{ret_id}/cancel",
        headers={**h, "Idempotency-Key": _idem()},
        json={"reason": "vendor dispute resolved"},
    )
    assert res_c.status_code == 200, res_c.text

    # Parent should recover to 'posted' (unpaid, no active returns)
    res_p2 = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    assert res_p2.json()["lifecycle_status"] == "posted"


@pytest.mark.asyncio
async def test_cancel_partial_return_recovery_to_partially_returned(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Full return cancelled -> 'posted'. Partial return ->
    'partially_returned'. Cancel partial -> 'posted'."""
    h = await _owner_headers(app, owner_user)
    pid, line_id = await _create_posted_purchase_with_line(app, h)

    # Return full 20 units
    ret_full = await _create_return(app, h, pid, line_id, qty="20.000")
    # Cancel that full return (unfinalized) -> active_returned drops to 0 -> posted
    res_c = await app.post(
        f"/api/v1/purchase-returns/{ret_full}/cancel",
        headers={**h, "Idempotency-Key": _idem()},
        json={"reason": "changed mind"},
    )
    assert res_c.status_code == 200, res_c.text
    res_p1 = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    assert res_p1.json()["lifecycle_status"] == "posted"

    # Now create a partial return of 5 units -> 'partially_returned'
    res_ret = await app.post(
        f"/api/v1/purchases/{pid}/returns",
        headers={**h, "Idempotency-Key": _idem()},
        json={
            "reason": "damaged 5",
            "lines": [{"purchase_line_id": line_id, "quantity": "5.000"}],
        },
    )
    assert res_ret.status_code == 201, res_ret.text
    ret_partial = res_ret.json()["id"]

    res_p2 = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    assert res_p2.json()["lifecycle_status"] == "partially_returned"

    # Cancel the partial -> active 0 -> recover to posted
    res_c2 = await app.post(
        f"/api/v1/purchase-returns/{ret_partial}/cancel",
        headers={**h, "Idempotency-Key": _idem()},
        json={"reason": "resolved"},
    )
    assert res_c2.status_code == 200, res_c2.text

    res_p3 = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    assert res_p3.json()["lifecycle_status"] == "posted"
