"""M4 Supplier Repayments lifecycle tests.

Covers all 3 OpenAPI operations: ``listSupplierRepayments``, ``createSupplierRepayment``,
``getSupplierRepayment``.

Plus:
* SREC (Supplier Receivable, asset 1300) per DB-Design §8.4:
  SREC = Σ supplier_repayments.amount − Σ supplier_repayments.received_amount
* ``repayment_exceeds_srec`` when repayment > SREC
* Idempotency
* Capability boundaries
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text


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
    hdr = resp.headers.get("ETag")
    if hdr:
        return hdr
    val = resp.json()["updated_at"]
    return f'"{val}"'


async def _seed_supplier_product_stock() -> None:
    """Seed supplier contact + product #1 with stock."""
    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO contacts (id, type, name, created_by)"
                " VALUES (1, 'supplier', 'Test Supplier', 1)"
                " ON CONFLICT (id) DO NOTHING"
            )
        )
        await session.execute(
            text(
                "INSERT INTO products (id, name, selling_price, allow_negative_stock,"
                " is_sellable, is_purchasable, is_active, created_by, updated_by)"
                " VALUES (1, 'Test Product', 50.00, FALSE, TRUE, TRUE, TRUE, 1, 1)"
                " ON CONFLICT (id) DO UPDATE SET"
                " selling_price = EXCLUDED.selling_price,"
                " is_active = TRUE,"
                " is_purchasable = TRUE"
            )
        )
        await session.commit()


async def _seed_srec_row(purchase_id: int, *, amount: Decimal, received_amount: Decimal,
                         reason: str = "purchase_return_credit") -> None:
    """Directly seed a supplier_repayments row (simulating return-created SREC).

    This is necessary because create_return (Phase E step 1) does not yet
    create supplier_repayments rows — that is a separate gap. To test the
    SREC formula and the Gap #4 upsert we seed rows directly at the DB level.

    The default ``reason`` matches the contract‑approved convention so that
    ``find_supplier_repayment_for_return`` locates the seeded row.
    """
    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO supplier_repayments "
                "(purchase_id, amount, received_amount, payment_method_id, "
                " repayment_date, reason, refundable_amount_snapshot, created_by)"
                " VALUES (:pid, :amt, :rec, 1, NOW(), :reason, :amt, 1)"
            ),
            {"pid": purchase_id, "amt": amount, "rec": received_amount, "reason": reason},
        )
        await session.commit()


async def _create_and_pay_purchase(
    app: AsyncClient, headers: dict[str, str], total: str, *, paid: str | None = None
) -> int:
    """Create a posted purchase with one line, optionally fully or partially paid.

    ``total`` must match qty * unit_price.  If ``paid`` is given, a payment
    for that amount is recorded.
    """
    res = await app.post(
        "/api/v1/purchases",
        headers={**headers, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]
    await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**headers, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "10.000", "unit_price": total},
    )
    await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**headers, "Idempotency-Key": _idem()},
        json={},
    )
    if paid is not None:
        await app.post(
            f"/api/v1/purchases/{pid}/payments",
            headers={**headers, "Idempotency-Key": _idem()},
            json={"payment_method_id": 2, "amount": paid},
        )
    return pid


@pytest.fixture(autouse=True)
async def _ensure_seeds(app: AsyncClient) -> AsyncGenerator[None, None]:
    await _seed_supplier_product_stock()
    yield


# ---------------------------------------------------------------------------
# SREC formula: SREC = Σ amount − Σ received (DB-Design §8.4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_srec_unpaid_purchase_no_srec_rows(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Unpaid purchase with no supplier_repayments rows → SREC = 0.

    A repayment attempt must be rejected (repayment_exceeds_srec) because
    there is no SREC obligation. This replaces the old behaviour that
    treated outstanding AP as SREC (now corrected per DB-Design §8.4).
    """
    h = await _owner_headers(app, owner_user)

    # Create + line + post: 200.00, paid 0
    res = await app.post(
        "/api/v1/purchases",
        headers={**h, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]
    await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "10.000", "unit_price": "20.00"},
    )
    await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )

    # No supplier_repayments rows → SREC = 0 → repayment rejected
    res_rep = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"purchase_id": pid, "payment_method_id": 2, "amount": "100.00"},
    )
    assert res_rep.status_code == 409, res_rep.text
    assert res_rep.json()["error"]["code"] == "repayment_exceeds_srec"

    # Verify supplier_receivable on purchase detail is 0
    res_get = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    assert res_get.json()["supplier_receivable"] == 0.0


@pytest.mark.asyncio
async def test_srec_fully_paid_purchase_with_return_credit(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Fully-paid purchase + return credit → SREC = return_value.

    Scenario E (contract §3): total=100, paid=100, return=40.
    Return creates SREC=40 (amount=40, received=0).
    supplier_receivable must show 40 (NOT 0 under old formula).
    """
    h = await _owner_headers(app, owner_user)

    # Create + line + post: 100.00, paid 100
    res = await app.post(
        "/api/v1/purchases",
        headers={**h, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]
    await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "10.000", "unit_price": "10.00"},
    )
    await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )
    await app.post(
        f"/api/v1/purchases/{pid}/payments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"payment_method_id": 2, "amount": "100.00"},
    )

    # Seed a return-created SREC row: amount=40, received=0 (Scenario E)
    await _seed_srec_row(pid, amount=Decimal("40.00"), received_amount=Decimal("0.00"))

    # supplier_receivable should be 40 (Σ amount − Σ received = 40 − 0)
    res_get = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    assert res_get.json()["supplier_receivable"] == 40.0
    assert res_get.json()["ap"] == 0.0  # fully paid

    # Repay 10 against SREC=40 → should succeed
    res_rep = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"purchase_id": pid, "payment_method_id": 2, "amount": "10.00"},
    )
    assert res_rep.status_code == 201, res_rep.text
    repayment = res_rep.json()
    # Post-repayment row state: obligation stays at 40, received=10.
    assert Decimal(str(repayment["amount"])) == Decimal("40.00")
    assert Decimal(str(repayment["received_amount"])) == Decimal("10.00")
    # SREC remaining = 40 − 10 = 30.
    res_get2 = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    assert res_get2.json()["supplier_receivable"] == 30.0


@pytest.mark.asyncio
async def test_srec_partially_paid_with_return_credit_split(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Partially-paid purchase + return credit → AP reduced + SREC created.

    Scenario D (contract §3): total=100, paid=60, return=50.
    remaining_AP = 40, return_value = 50.
    AP_reduction = 40, SREC_created = 10.
    Seed SREC row: amount=10, received=0.
    supplier_receivable should be 10.
    """
    h = await _owner_headers(app, owner_user)

    # Create + line + post: 100.00, paid 60
    res = await app.post(
        "/api/v1/purchases",
        headers={**h, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]
    await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "10.000", "unit_price": "10.00"},
    )
    await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )
    await app.post(
        f"/api/v1/purchases/{pid}/payments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"payment_method_id": 2, "amount": "60.00"},
    )

    # Seed SREC created by return: amount=10, received=0
    await _seed_srec_row(pid, amount=Decimal("10.00"), received_amount=Decimal("0.00"))

    # supplier_receivable = 10 − 0 = 10; AP = outstanding = 40
    res_get = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    assert res_get.json()["supplier_receivable"] == 10.0
    assert Decimal(str(res_get.json()["outstanding"])) == Decimal("40.00")

    # Repay 10 (full SREC) → succeed
    res_rep = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"purchase_id": pid, "payment_method_id": 2, "amount": "10.00"},
    )
    assert res_rep.status_code == 201, res_rep.text


@pytest.mark.asyncio
async def test_srec_repayment_exceeds_srec_after_partial(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Scenario E: SREC=40, repaid 10 → remaining SREC=30. Repay 31 → 409."""
    h = await _owner_headers(app, owner_user)

    res = await app.post(
        "/api/v1/purchases",
        headers={**h, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]
    await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "10.000", "unit_price": "10.00"},
    )
    await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )
    await app.post(
        f"/api/v1/purchases/{pid}/payments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"payment_method_id": 2, "amount": "100.00"},
    )

    # SREC = 40
    await _seed_srec_row(pid, amount=Decimal("40.00"), received_amount=Decimal("0.00"))

    # Repay 10 → ok
    r1 = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"purchase_id": pid, "payment_method_id": 2, "amount": "10.00"},
    )
    assert r1.status_code == 201, r1.text
    # Per Gap #4, obligation stays at 40, received_amount = 10.
    assert Decimal(str(r1.json()["amount"])) == Decimal("40.00")
    assert Decimal(str(r1.json()["received_amount"])) == Decimal("10.00")

    # Repay 31 → only 30 remaining SREC → 409.
    r2 = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"purchase_id": pid, "payment_method_id": 2, "amount": "31.00"},
    )
    assert r2.status_code == 409, r2.text
    assert r2.json()["error"]["code"] == "repayment_exceeds_srec"


# ---------------------------------------------------------------------------
# Full happy path: create + list + get (with seeded SREC)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supplier_repayment_full_create_list_get(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Create a supplier repayment against an existing SREC obligation.

    Per the corrected SREC formula (DB-Design §8.4), a repayment can
    only be created when there is an SREC obligation (amount − received > 0).
    We seed a return-credit SREC row (amount=200, received=0) to model
    a fully-paid purchase that has been returned.
    """
    h = await _owner_headers(app, owner_user)

    # Create + line + post: 200.00
    res = await app.post(
        "/api/v1/purchases",
        headers={**h, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]
    await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "10.000", "unit_price": "20.00"},
    )
    await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )

    # Seed SREC obligation (simulating return-created credit): amount=200, received=0
    await _seed_srec_row(pid, amount=Decimal("200.00"), received_amount=Decimal("0.00"))

    # Repay 100.00
    res_rep = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": _idem()},
        json={
            "purchase_id": pid,
            "payment_method_id": 2,
            "amount": "100.00",
        },
    )
    assert res_rep.status_code == 201, res_rep.text
    repayment = res_rep.json()
    rid = repayment["id"]
    # Per Gap #4 the API response describes the updated obligation row:
    # amount stays at the original SREC obligation; received_amount grows
    # by the repayment amount. The seeded row had amount=200, received=0;
    # after repaying 100, amount is unchanged and received_amount == 100.
    assert Decimal(str(repayment["amount"])) == Decimal("200.00")
    assert Decimal(str(repayment["received_amount"])) == Decimal("100.00")

    # List (MetaEnvelope with data + pagination)
    res_list = await app.get("/api/v1/supplier-repayments", headers=h)
    assert res_list.status_code == 200
    body = res_list.json()
    items = body.get("data") if isinstance(body, dict) else body
    assert any(x["id"] == rid for x in items)

    # Get
    res_g = await app.get(f"/api/v1/supplier-repayments/{rid}", headers=h)
    assert res_g.status_code == 200
    assert res_g.json()["id"] == rid


# ---------------------------------------------------------------------------
# repayment_exceeds_srec
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supplier_repayment_exceeds_srec(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Repaying more than the SREC obligation → 409.

    SREC = amount=100, received=0 → SREC=100. Repay 150 → 409.
    """
    h = await _owner_headers(app, owner_user)

    res = await app.post(
        "/api/v1/purchases",
        headers={**h, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]
    await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "5.000", "unit_price": "20.00"},
    )
    await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )

    # Seed SREC = 100
    await _seed_srec_row(pid, amount=Decimal("100.00"), received_amount=Decimal("0.00"))

    # Repay 150 (only 100 SREC exists)
    res_over = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"purchase_id": pid, "payment_method_id": 2, "amount": "150.00"},
    )
    assert res_over.status_code == 409, res_over.text
    assert res_over.json()["error"]["code"] == "repayment_exceeds_srec"


# ---------------------------------------------------------------------------
# Multiple repayments must not over-allocate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supplier_repayment_cumulative_bound(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """First repayment 60 ok; second 60 fails (total 120 > SREC 100).

    SREC = amount=100, received=0 → SREC=100.
    """
    h = await _owner_headers(app, owner_user)

    res = await app.post(
        "/api/v1/purchases",
        headers={**h, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]
    await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "5.000", "unit_price": "20.00"},
    )
    await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )

    # Seed SREC = 100
    await _seed_srec_row(pid, amount=Decimal("100.00"), received_amount=Decimal("0.00"))

    # First repayment 60 ok
    r1 = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"purchase_id": pid, "payment_method_id": 2, "amount": "60.00"},
    )
    assert r1.status_code == 201, r1.text
    assert Decimal(str(r1.json()["amount"])) == Decimal("100.00")
    assert Decimal(str(r1.json()["received_amount"])) == Decimal("60.00")

    # Second 60 → cumulative 120 > 100 SREC → 409.
    r2 = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"purchase_id": pid, "payment_method_id": 2, "amount": "60.00"},
    )
    assert r2.status_code == 409, r2.text
    assert r2.json()["error"]["code"] == "repayment_exceeds_srec"


# ---------------------------------------------------------------------------
# Gap #4 regression: obligation row semantics (Σ amount must NOT grow)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repayment_over_bound_after_partial(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Test C — Over-repayment is rejected.

    obligation=40, received=30 → remaining SREC=10.
    A repayment of 11 must be rejected (409 repayment_exceeds_srec).
    """
    h = await _owner_headers(app, owner_user)

    pid = await _create_and_pay_purchase(app, h, "100.00", paid="100.00")
    await _seed_srec_row(pid, amount=Decimal("40.00"), received_amount=Decimal("30.00"))

    # Repay 11 → only 10 remaining → 409.
    res = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"purchase_id": pid, "payment_method_id": 2, "amount": "11.00"},
    )
    assert res.status_code == 409, res.text
    assert res.json()["error"]["code"] == "repayment_exceeds_srec"

    # Obligation row unchanged.
    res_get = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    assert res_get.json()["supplier_receivable"] == 10.0


@pytest.mark.asyncio
async def test_repayment_exact_bound(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Test D — Exact-bound repayment succeeds and zeroes SREC.

    obligation=40, received=30 → remaining SREC=10.
    A repayment of 10 must succeed and leave SREC=0.
    """
    h = await _owner_headers(app, owner_user)

    pid = await _create_and_pay_purchase(app, h, "100.00", paid="100.00")
    await _seed_srec_row(pid, amount=Decimal("40.00"), received_amount=Decimal("30.00"))

    res = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"purchase_id": pid, "payment_method_id": 2, "amount": "10.00"},
    )
    assert res.status_code == 201, res.text
    assert Decimal(str(res.json()["amount"])) == Decimal("40.00")
    assert Decimal(str(res.json()["received_amount"])) == Decimal("40.00")

    res_get = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    assert res_get.json()["supplier_receivable"] == 0.0


@pytest.mark.asyncio
async def test_repayment_no_obligation_rejected(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Test G — No SREC obligation → repayment rejected.

    Fully-paid purchase with no return credit → no supplier_repayments
    row → a repayment attempt must be rejected (409,
    repayment_exceeds_srec) rather than silently creating a new
    obligation row.
    """
    h = await _owner_headers(app, owner_user)

    pid = await _create_and_pay_purchase(app, h, "100.00", paid="100.00")

    res = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"purchase_id": pid, "payment_method_id": 2, "amount": "10.00"},
    )
    assert res.status_code == 409, res.text
    assert res.json()["error"]["code"] == "repayment_exceeds_srec"

    # supplier_receivable must be 0 — no obligation row was created.
    res_get = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    assert res_get.json()["supplier_receivable"] == 0.0


@pytest.mark.asyncio
async def test_concurrent_repayments_do_not_over_allocate(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Concurrency: two simultaneous repayments must not exceed SREC.

    obligation=40, received=0 → SREC=40.
    Two concurrent repayments of 25 each (total 50 > 40).
    At most one may succeed; the other must fail the SREC ceiling.
    The DB CHECK ``received_amount <= amount`` guarantees at most 40
    total is received regardless of race.
    """
    import asyncio

    h = await _owner_headers(app, owner_user)

    pid = await _create_and_pay_purchase(app, h, "100.00", paid="100.00")
    await _seed_srec_row(pid, amount=Decimal("40.00"), received_amount=Decimal("0.00"))

    async def repay(label: str) -> tuple[str, int]:
        res = await app.post(
            "/api/v1/supplier-repayments",
            headers={**h, "Idempotency-Key": _idem()},
            json={"purchase_id": pid, "payment_method_id": 2, "amount": "25.00"},
        )
        return label, res.status_code

    results = await asyncio.gather(repay("a"), repay("b"))
    statuses = [s for _, s in results]
    # Exactly one should succeed; the other must be rejected.
    assert statuses.count(201) == 1, f"expected one 201, got {statuses}"
    assert statuses.count(409) == 1, f"expected one 409, got {statuses}"

    # Σ received must never exceed 40 (the obligation).
    res_get = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    srec = Decimal(str(res_get.json()["supplier_receivable"]))
    assert srec >= Decimal("0"), "SREC must never go negative"



@pytest.mark.asyncio
async def test_supplier_repayment_idempotency(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    h = await _owner_headers(app, owner_user)

    res = await app.post(
        "/api/v1/purchases",
        headers={**h, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]
    await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "5.000", "unit_price": "20.00"},
    )
    await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )

    # Seed SREC = 50
    await _seed_srec_row(pid, amount=Decimal("50.00"), received_amount=Decimal("0.00"))

    body = {"purchase_id": pid, "payment_method_id": 2, "amount": "50.00"}
    idk = _idem()
    r1 = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": idk},
        json=body,
    )
    assert r1.status_code == 201

    r2 = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": idk},
        json=body,
    )
    assert r2.status_code == 200
    assert r2.headers.get("Idempotent-Replay") == "true"
    assert r2.text != ""


# ---------------------------------------------------------------------------
# Auth / capability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_supplier_repayment_auth(
    app: AsyncClient,
    owner_user: dict[str, Any],
    staff_user: dict[str, Any],
) -> None:
    h_owner = await _owner_headers(app, owner_user)
    h_staff = await _staff_headers(app, staff_user)

    # 401
    res = await app.get("/api/v1/supplier-repayments")
    assert res.status_code == 401

    # Staff can list (purchase.view)
    res = await app.get("/api/v1/supplier-repayments", headers=h_staff)
    assert res.status_code == 200

    # Setup purchase
    res = await app.post(
        "/api/v1/purchases",
        headers={**h_owner, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]
    await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**h_owner, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "5.000", "unit_price": "10.00"},
    )
    await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**h_owner, "Idempotency-Key": _idem()},
        json={},
    )

    # Seed SREC so a repayment could theoretically be created
    await _seed_srec_row(pid, amount=Decimal("50.00"), received_amount=Decimal("0.00"))

    # Staff cannot create supplier repayment (purchase.refund is owner-only)
    res_staff = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h_staff, "Idempotency-Key": _idem()},
        json={"purchase_id": pid, "payment_method_id": 2, "amount": "50.00"},
    )
    assert res_staff.status_code == 403, res_staff.text


# ---------------------------------------------------------------------------
# Phase E §14 prerequisite #1: create_return must create the SREC obligation row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_return_creates_srec_obligation_fully_paid(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Scenario E (contract §3): fully-paid purchase, return = SREC_created.

    Purchase total=200, paid=200 → remaining_AP=0.
    Return value=50 (5 units @ 10) → SREC_created = max(0, 50 − 0) = 50.
    create_return must insert supplier_repayments row:
      reason='purchase_return_credit', amount=50, received_amount=0.
    Then a follow-up create_repayment of 10 must find the row and succeed.
    """
    h = await _owner_headers(app, owner_user)

    # Purchase 20 units @ 10.00 = 200.00
    res = await app.post(
        "/api/v1/purchases",
        headers={**h, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]
    res_line = await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "20.000", "unit_price": "10.00"},
    )
    line_id = res_line.json()["id"]
    await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )
    # Pay full amount
    res_pay = await app.post(
        f"/api/v1/purchases/{pid}/payments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"payment_method_id": 2, "amount": "200.00"},
    )
    assert res_pay.status_code in (200, 201), res_pay.text

    # Return 5 units @ 10.00 = 50.00
    res_p = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    res_ret = await app.post(
        f"/api/v1/purchases/{pid}/returns",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": _etag(res_p)},
        json={"reason": "damaged", "lines": [{"purchase_line_id": line_id, "quantity": "5.000"}]},
    )
    assert res_ret.status_code == 201, res_ret.text

    # Verify the obligation row was created by create_return (NO _seed_srec_row).
    from app.db import get_session_factory
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT amount, received_amount, reason FROM supplier_repayments "
                "WHERE purchase_id = :pid AND reason = 'purchase_return_credit'"
            ),
            {"pid": pid},
        )
        row = result.first()
    assert row is not None, "create_return did NOT create the SREC obligation row"
    assert Decimal(str(row[0])) == Decimal("50.00"), f"amount={row[0]}, expected 50.00"
    assert Decimal(str(row[1])) == Decimal("0.00"), f"received_amount={row[1]}, expected 0.00"
    assert row[2] == "purchase_return_credit"

    # End-to-end: create_repayment of 10 must find this row and upsert.
    res_repay = await app.post(
        "/api/v1/supplier-repayments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"purchase_id": pid, "payment_method_id": 2, "amount": "10.00"},
    )
    assert res_repay.status_code in (200, 201), res_repay.text
    body = res_repay.json()
    assert Decimal(str(body["amount"])) == Decimal("50.00")
    assert Decimal(str(body["received_amount"])) == Decimal("10.00")
    # SREC remaining = 50 − 10 = 40.
    res_get = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    assert res_get.json()["supplier_receivable"] == 40.0


@pytest.mark.asyncio
async def test_create_return_creates_srec_obligation_partial_payment(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Scenario D (contract §3): partial payment → SREC = return − remaining_AP.

    Purchase total=200, paid=150 → remaining_AP=50.
    Return value=70 (7 units @ 10) → SREC_created = max(0, 70 − 50) = 20.
    create_return must insert obligation row with amount=20.
    """
    h = await _owner_headers(app, owner_user)

    res = await app.post(
        "/api/v1/purchases",
        headers={**h, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]
    res_line = await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "20.000", "unit_price": "10.00"},
    )
    line_id = res_line.json()["id"]
    await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )
    # Pay 150 of 200 → remaining AP = 50
    res_pay = await app.post(
        f"/api/v1/purchases/{pid}/payments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"payment_method_id": 2, "amount": "150.00"},
    )
    assert res_pay.status_code in (200, 201), res_pay.text

    # Return 7 units @ 10.00 = 70.00 → AP_reduction=50, SREC_created=20
    res_p = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    res_ret = await app.post(
        f"/api/v1/purchases/{pid}/returns",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": _etag(res_p)},
        json={"reason": "damaged", "lines": [{"purchase_line_id": line_id, "quantity": "7.000"}]},
    )
    assert res_ret.status_code == 201, res_ret.text

    from app.db import get_session_factory
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT amount, received_amount FROM supplier_repayments "
                "WHERE purchase_id = :pid AND reason = 'purchase_return_credit'"
            ),
            {"pid": pid},
        )
        row = result.first()
    assert row is not None, "create_return did NOT create the SREC obligation row"
    assert Decimal(str(row[0])) == Decimal("20.00"), f"amount={row[0]}, expected 20.00"
    assert Decimal(str(row[1])) == Decimal("0.00")


@pytest.mark.asyncio
async def test_create_return_no_srec_when_return_le_remaining_ap(
    app: AsyncClient,
    owner_user: dict[str, Any],
) -> None:
    """Scenario: return ≤ remaining AP → SREC_created = 0 → no obligation row.

    Purchase total=200, paid=50 → remaining_AP=150.
    Return value=50 → SREC_created = max(0, 50 − 150) = 0.
    create_return must NOT insert any obligation row.
    """
    h = await _owner_headers(app, owner_user)

    res = await app.post(
        "/api/v1/purchases",
        headers={**h, "Idempotency-Key": _idem()},
        json={"supplier_id": 1},
    )
    pid = res.json()["id"]
    res_line = await app.post(
        f"/api/v1/purchases/{pid}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "20.000", "unit_price": "10.00"},
    )
    line_id = res_line.json()["id"]
    await app.post(
        f"/api/v1/purchases/{pid}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )
    # Pay only 50 → remaining AP = 150
    res_pay = await app.post(
        f"/api/v1/purchases/{pid}/payments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"payment_method_id": 2, "amount": "50.00"},
    )
    assert res_pay.status_code in (200, 201), res_pay.text

    # Return 5 units @ 10.00 = 50.00 → 50 ≤ 150 → SREC_created = 0
    res_p = await app.get(f"/api/v1/purchases/{pid}", headers=h)
    res_ret = await app.post(
        f"/api/v1/purchases/{pid}/returns",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": _etag(res_p)},
        json={"reason": "damaged", "lines": [{"purchase_line_id": line_id, "quantity": "5.000"}]},
    )
    assert res_ret.status_code == 201, res_ret.text

    from app.db import get_session_factory
    factory = get_session_factory()
    async with factory() as session:
        result = await session.execute(
            text(
                "SELECT COUNT(*) FROM supplier_repayments "
                "WHERE purchase_id = :pid AND reason = 'purchase_return_credit'"
            ),
            {"pid": pid},
        )
        count = result.scalar()
    assert count == 0, f"Expected no obligation row, found {count}"

