"""M5 / Phase E.6 — Stock Adjustment tests.

Covers the single E.6 OpenAPI operation:

* ``createStockAdjustment`` — POST /inventory/adjustments

Endpoint contract (openapi.yaml §15.2, lines 4311-4347):

* Auth: bearer + capability ``inventory.adjust``
* Headers: ``Idempotency-Key`` (required, UUID), ``If-Match`` (required)
* Body: ``StockAdjustmentRequest`` — product_id (int ≥1), quantity
  (signed ≠0), reason (1-1000 chars)
* Response: 201 → ``StockMovement`` contract shape
* Errors: 400 missing/invalid header, validation; 401 unauth; 403
  permission_denied; 404 not_found; 409 idempotency_violation /
  version_mismatch; 422 negative_stock_disallowed /
  business_rule_violation (inactive product)
* DB effect: one ``stock_movements`` row with
  ``trigger='stock_adjustment'``, signed quantity, MAUC snapshot on
  ``unit_cost_at_movement``, ``reason`` populated; one ``audit_log``
  row with ``action='adjust'`` and ``new_values.reason``

Test coverage:

* Happy paths: up adjustment (positive qty) increases on-hand; down
  adjustment (negative qty) decreases on-hand.
* Reason required: missing reason → 400 validation_failed.
* Header guards: missing Idempotency-Key → 400 missing_header;
  missing If-Match → 400 missing_header.
* ETag mismatch → 412 version_mismatch.
* Idempotency: replay returns cached body + Idempotent-Replay header;
  key+body mismatch → 409 idempotency_violation.
* Authorization: 401 unauth; 403 for staff lacking inventory.adjust.
* Product missing → 404 not_found.
* Product deactivated → 422 business_rule_violation.
* Negative stock not allowed → 422 negative_stock_disallowed.
* Negative stock allowed (product override) → success, on-hand goes
  negative.
* Audit row written with reason.
* Stock movement row: trigger, quantity, reason.
* Inventory value (via product_valuation view) reflects the change.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

# ---------------------------------------------------------------------------
# Helpers
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


async def _grant_inventory_adjust_to_staff() -> None:
    """Make sure the staff user has ``inventory.adjust`` when individual
    tests need it; we explicitly remove it for the 403 case."""
    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO user_capability_overrides ("
                " user_id, capability_id, is_granted, granted_by)"
                " SELECT u.id, c.id, TRUE, 1"
                " FROM users u, capabilities c"
                " WHERE u.username = 'staff' AND c.code = 'inventory.adjust'"
                " ON CONFLICT (user_id, capability_id) DO UPDATE"
                " SET is_granted = TRUE, granted_by = 1"
            )
        )
        await session.commit()


async def _revoke_inventory_adjust_from_staff() -> None:
    """Remove the per-user override so the staff user falls back to the
    Staff role default capability set (no ``inventory.adjust``)."""
    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "DELETE FROM user_capability_overrides uo"
                " USING capabilities c, users u"
                " WHERE uo.capability_id = c.id"
                " AND c.code = 'inventory.adjust'"
                " AND uo.user_id = u.id AND u.username = 'staff'"
            )
        )
        await session.commit()


async def _seed_adjustment_products() -> list[dict[str, Any]]:
    """Seed the product set used by E.6 tests.

    Products:
      201 — 'Adjustable Active'   — allow_negative_stock FALSE, stock 50
      202 — 'Adjustable Negative' — allow_negative_stock TRUE,  stock 30
      203 — 'Inactive Product'    — is_active FALSE,            stock 10
      204 — 'Brand New'           — allow_negative_stock NULL, stock 0 (default FALSE)
      205 — 'Missing'             — NOT seeded (404 test)
    """
    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO products (id, name, selling_price, purchase_price,"
                " allow_negative_stock,"
                " is_sellable, is_purchasable, is_producible, is_active,"
                " created_by, updated_by)"
                " VALUES"
                " (201, 'Adjustable Active',   100.00,  4.00, FALSE, TRUE, TRUE, FALSE, TRUE,  1, 1),"
                " (202, 'Adjustable Negative', 100.00,  4.00, TRUE,  TRUE, TRUE, FALSE, TRUE,  1, 1),"
                " (203, 'Inactive Product',     50.00,  4.00, FALSE, TRUE, TRUE, FALSE, FALSE, 1, 1),"
                # 204 — Brand New, no prior stock, purchase_price=20.00 so
                # the BR-COST-006 fallback in service.create_adjustment
                # has a non-zero cost basis to snapshot. Without it, the
                # DB constraint ``ck_sm_total_cost_nonzero`` would reject
                # the first opening-balance adjustment (NUMERIC(15,2) on
                # stock_movements.total_cost cannot store a sub-cent
                # epsilon).
                " (204, 'Brand New',            20.00, 20.00, NULL,  TRUE, TRUE, FALSE, TRUE,  1, 1)"
                " ON CONFLICT (id) DO UPDATE SET"
                " is_active = EXCLUDED.is_active,"
                " allow_negative_stock = EXCLUDED.allow_negative_stock,"
                " selling_price = EXCLUDED.selling_price,"
                " purchase_price = EXCLUDED.purchase_price"
            )
        )
        await session.execute(
            text(
                "INSERT INTO stock_movements (product_id, trigger, quantity,"
                " unit_cost_at_movement, total_cost,"
                " reference_type, reference_id, created_by)"
                " VALUES"
                " (201, 'opening_balance', 50.0000, 4.00, 200.00, 'seed', 0, 1),"
                " (202, 'opening_balance', 30.0000, 6.00, 180.00, 'seed', 0, 1),"
                " (203, 'opening_balance', 10.0000, 3.00,  30.00, 'seed', 0, 1)"
                " ON CONFLICT DO NOTHING"
            )
        )
        await session.commit()
    return [
        {"id": 201, "stock": 50, "mauc": 4.00, "allow_negative_stock": False, "is_active": True},
        {"id": 202, "stock": 30, "mauc": 6.00, "allow_negative_stock": True, "is_active": True},
        {"id": 203, "stock": 10, "mauc": 3.00, "allow_negative_stock": False, "is_active": False},
        {"id": 204, "stock": 0, "mauc": None, "allow_negative_stock": None, "is_active": True},
    ]


async def _read_updated_at(product_id: int) -> str:
    """Read the product's ``updated_at`` so tests can build the If-Match value.

    The canonical ETag form (per ``app.util.iso_utc``) is the timezone-aware
    ``datetime.isoformat()`` form, which emits ``+00:00`` for UTC — NOT the
    ``Z`` shorthand. We return that form unchanged.

    Phase 3A: Use Pydantic's JSON form (Z suffix) so test ETags match the
    production ETag computation in ``etag_and_version_from_updated_at()``.
    """
    from app.db import get_session_factory
    from pydantic import TypeAdapter
    from datetime import datetime

    DATETIME_ADAPTER = TypeAdapter(datetime)

    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                text("SELECT updated_at FROM products WHERE id = :id"),
                {"id": int(product_id)},
            )
        ).mappings().first()
        assert row is not None, f"product {product_id} missing"
        ts = row["updated_at"]
    if isinstance(ts, datetime):
        # Use Pydantic's JSON form (Z suffix, microseconds preserved) to match
        # the production ETag computation.
        return DATETIME_ADAPTER.dump_python(ts, mode="json")
    return str(ts)


async def _on_hand(product_id: int) -> float:
    """Read the on-hand quantity from the ``product_valuation`` view."""
    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT on_hand_quantity FROM product_valuation"
                    " WHERE product_id = :id"
                ),
                {"id": int(product_id)},
            )
        ).mappings().first()
        if row is None or row["on_hand_quantity"] is None:
            return 0.0
        return float(row["on_hand_quantity"])


async def _stock_movement_count(product_id: int, trigger: str) -> int:
    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        v = await session.scalar(
            text(
                "SELECT COUNT(*) FROM stock_movements"
                " WHERE product_id = :id AND trigger = :t"
            ),
            {"id": int(product_id), "t": trigger},
        )
        return int(v or 0)


async def _audit_count(product_id: int) -> int:
    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        v = await session.scalar(
            text(
                "SELECT COUNT(*) FROM audit_log"
                " WHERE entity_type = 'product' AND entity_id = :id"
                " AND action = 'adjust'"
            ),
            {"id": int(product_id)},
        )
        return int(v or 0)


@pytest.fixture(autouse=True)
async def _seed(db: None) -> AsyncGenerator[None, None]:
    """Seed adjustment products + reset staff capability override.

    The staff user has the default role-level capability set which does
    NOT include ``inventory.adjust``. Individual tests that exercise
    the staff-as-authorized case opt in via ``_grant_inventory_adjust_to_staff``.
    """
    await _seed_adjustment_products()
    await _revoke_inventory_adjust_from_staff()
    yield


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


async def test_create_adjustment_unauthenticated_rejected(app: AsyncClient) -> None:
    r = await app.post(
        "/api/v1/inventory/adjustments",
        headers={"Idempotency-Key": _idem(), "If-Match": '"x"'},
        json={"product_id": 201, "quantity": 1.0, "reason": "test"},
    )
    assert r.status_code == 401


async def test_create_adjustment_staff_forbidden(app: AsyncClient, staff_user: dict[str, Any]) -> None:
    """Staff has no ``inventory.adjust`` capability by default → 403."""
    h = await _staff_headers(app, staff_user)
    etag = await _read_updated_at(201)
    r = await app.post(
        "/api/v1/inventory/adjustments",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": f'"{etag}"'},
        json={"product_id": 201, "quantity": 1.0, "reason": "test"},
    )
    assert r.status_code == 403, r.text


# ---------------------------------------------------------------------------
# Header guards
# ---------------------------------------------------------------------------


async def test_create_adjustment_missing_idempotency_key(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    h = await _owner_headers(app, owner_user)
    etag = await _read_updated_at(201)
    r = await app.post(
        "/api/v1/inventory/adjustments",
        headers={**h, "If-Match": f'"{etag}"'},
        json={"product_id": 201, "quantity": 1.0, "reason": "test"},
    )
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["error"]["code"] == "missing_header"


async def test_create_adjustment_missing_if_match(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    h = await _owner_headers(app, owner_user)
    r = await app.post(
        "/api/v1/inventory/adjustments",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 201, "quantity": 1.0, "reason": "test"},
    )
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["error"]["code"] == "missing_header"


async def test_create_adjustment_invalid_if_match(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    h = await _owner_headers(app, owner_user)
    r = await app.post(
        "/api/v1/inventory/adjustments",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": "not-a-quoted-etag"},
        json={"product_id": 201, "quantity": 1.0, "reason": "test"},
    )
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["error"]["code"] == "invalid_header"


async def test_create_adjustment_stale_if_match_returns_412(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """Stale ETag (mismatching the current ``updated_at``) → 412 version_mismatch."""
    h = await _owner_headers(app, owner_user)
    r = await app.post(
        "/api/v1/inventory/adjustments",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": '"1999-01-01T00:00:00Z"'},
        json={"product_id": 201, "quantity": 1.0, "reason": "test"},
    )
    assert r.status_code == 412, r.text
    body = r.json()
    assert body["error"]["code"] == "version_mismatch"


async def test_create_adjustment_wildcard_if_match_succeeds(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """``If-Match: *`` matches any existing representation (RFC 7232)."""
    h = await _owner_headers(app, owner_user)
    r = await app.post(
        "/api/v1/inventory/adjustments",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": "*"},
        json={"product_id": 201, "quantity": 1.0, "reason": "wildcard"},
    )
    assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# Body validation
# ---------------------------------------------------------------------------


async def test_create_adjustment_reason_required(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    h = await _owner_headers(app, owner_user)
    etag = await _read_updated_at(201)
    r = await app.post(
        "/api/v1/inventory/adjustments",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": f'"{etag}"'},
        json={"product_id": 201, "quantity": 1.0},  # reason missing
    )
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["error"]["code"] == "validation_failed"


async def test_create_adjustment_negative_stock_zero_quantity_allowed_by_spec(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """The OpenAPI spec declares ``exclusiveMinimum`` on quantity but
    leaves ``maximum`` inclusive, so ``quantity=0`` is allowed by the
    schema. The DB constraint ``ck_sm_quantity`` also allows qty=0
    only for ``value_adjustment``, NOT for ``stock_adjustment`` —
    this case is therefore rejected at the DB layer with a
    check_violation. Verify the user-facing envelope surfaces a clean
    409 / 422 instead of an opaque 500."""
    h = await _owner_headers(app, owner_user)
    etag = await _read_updated_at(201)
    r = await app.post(
        "/api/v1/inventory/adjustments",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": f'"{etag}"'},
        json={"product_id": 201, "quantity": 0.0, "reason": "zero"},
    )
    # We accept 400 (schema validation rejection by Pydantic if the
    # exclusiveMinimum is enforced strictly), 409, or 422 — anything
    # other than 500. The key contract is that the client gets a
    # proper envelope, not an unhandled exception.
    assert r.status_code in (400, 409, 422), r.text
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] in (
        "validation_failed",
        "conflict",
        "business_rule_violation",
        "ck_sm_quantity",
    ) or body["error"]["code"] != "internal_error"


async def test_create_adjustment_product_id_required(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    h = await _owner_headers(app, owner_user)
    etag = await _read_updated_at(201)
    r = await app.post(
        "/api/v1/inventory/adjustments",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": f'"{etag}"'},
        json={"quantity": 1.0, "reason": "no product_id"},
    )
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["error"]["code"] == "validation_failed"


# ---------------------------------------------------------------------------
# Resource guards
# ---------------------------------------------------------------------------


async def test_create_adjustment_product_missing_returns_404(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    h = await _owner_headers(app, owner_user)
    # No row id=205 exists (per _seed_adjustment_products). Use a fresh
    # If-Match value — the route must look up the product *before*
    # ETag validation, so a bogus ETag on a non-existent product
    # should still surface 404, not 412.
    r = await app.post(
        "/api/v1/inventory/adjustments",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": "*"},
        json={"product_id": 205, "quantity": 1.0, "reason": "missing"},
    )
    assert r.status_code == 404, r.text
    body = r.json()
    assert body["error"]["code"] == "not_found"


async def test_create_adjustment_inactive_product_rejected(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    h = await _owner_headers(app, owner_user)
    etag = await _read_updated_at(203)
    r = await app.post(
        "/api/v1/inventory/adjustments",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": f'"{etag}"'},
        json={"product_id": 203, "quantity": 1.0, "reason": "inactive"},
    )
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["error"]["code"] == "business_rule_violation"


# ---------------------------------------------------------------------------
# Negative-stock guard
# ---------------------------------------------------------------------------


async def test_create_adjustment_negative_stock_disallowed(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """Product 201 has stock 50 and allow_negative_stock=FALSE. A -100
    adjustment drives on_hand to -50 → blocked."""
    h = await _owner_headers(app, owner_user)
    etag = await _read_updated_at(201)
    r = await app.post(
        "/api/v1/inventory/adjustments",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": f'"{etag}"'},
        json={"product_id": 201, "quantity": -100.0, "reason": "shrinkage"},
    )
    assert r.status_code == 422, r.text
    body = r.json()
    assert body["error"]["code"] == "negative_stock_disallowed"
    assert body["error"]["details"]["product_id"] == 201


async def test_create_adjustment_within_bounds_succeeds(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """-10 against stock 50 is within bounds → 201."""
    h = await _owner_headers(app, owner_user)
    etag = await _read_updated_at(201)
    r = await app.post(
        "/api/v1/inventory/adjustments",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": f'"{etag}"'},
        json={"product_id": 201, "quantity": -10.0, "reason": "shrinkage"},
    )
    assert r.status_code == 201, r.text
    assert await _on_hand(201) == 40.0


async def test_create_adjustment_negative_allowed_product(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """Product 202 has allow_negative_stock=TRUE → a -100 decrement
    drives on_hand to -70 but is allowed."""
    h = await _owner_headers(app, owner_user)
    etag = await _read_updated_at(202)
    r = await app.post(
        "/api/v1/inventory/adjustments",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": f'"{etag}"'},
        json={"product_id": 202, "quantity": -100.0, "reason": "shrinkage"},
    )
    assert r.status_code == 201, r.text
    assert await _on_hand(202) == -70.0


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


async def test_create_adjustment_up_increases_on_hand(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """+25 against product 201 → on_hand goes 50 → 75."""
    h = await _owner_headers(app, owner_user)
    etag = await _read_updated_at(201)
    r = await app.post(
        "/api/v1/inventory/adjustments",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": f'"{etag}"'},
        json={"product_id": 201, "quantity": 25.0, "reason": "found stock"},
    )
    assert r.status_code == 201, r.text
    assert await _on_hand(201) == 75.0
    # Stock_movements count for trigger='stock_adjustment' on product 201 incremented by 1.
    assert await _stock_movement_count(201, "stock_adjustment") == 1


async def test_create_adjustment_response_shape(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """201 response carries the canonical ``StockMovement`` contract shape."""
    h = await _owner_headers(app, owner_user)
    etag = await _read_updated_at(201)
    r = await app.post(
        "/api/v1/inventory/adjustments",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": f'"{etag}"'},
        json={"product_id": 201, "quantity": 5.0, "reason": "opening"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    for required in (
        "id", "product_id", "movement_date", "trigger",
        "quantity", "created_at", "created_by",
    ):
        assert required in body, f"missing required field {required}"
    assert body["trigger"] == "stock_adjustment"
    assert body["product_id"] == 201
    assert float(body["quantity"]) == 5.0
    # unit_cost_at_movement snapshots the MAUC at write time (50 stock @ 4.00 MAUC).
    assert body.get("unit_cost_at_movement") == 4.0
    # total_cost = quantity * unit_cost, with sign (5 * 4.00 = 20.00).
    assert float(body["total_cost"]) == 20.0
    # reason is persisted on the movement row.
    assert body.get("reason") == "opening"
    # No parent header table → reference_type/id are None.
    assert body.get("reference_type") is None
    assert body.get("reference_id") is None


async def test_create_adjustment_writes_audit_row(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """The action writes one ``audit_log`` row with action='adjust'."""
    h = await _owner_headers(app, owner_user)
    etag = await _read_updated_at(201)
    before = await _audit_count(201)
    r = await app.post(
        "/api/v1/inventory/adjustments",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": f'"{etag}"'},
        json={"product_id": 201, "quantity": 1.0, "reason": "audit test"},
    )
    assert r.status_code == 201, r.text
    assert await _audit_count(201) == before + 1


async def test_create_adjustment_inventory_value_unchanged_for_zero_mauc(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """Per Arch §12.5: the P&L effect is NOT auto-posted. Verify that
    adjusting a brand-new product with no prior MAUC succeeds (we fall
    back to ``products.purchase_price`` per BR-COST-006) and leaves
    on-hand at the requested quantity. The P&L effect is left for the
    Owner to record via ``POST /manual-entries``."""
    h = await _owner_headers(app, owner_user)
    etag = await _read_updated_at(204)
    r = await app.post(
        "/api/v1/inventory/adjustments",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": f'"{etag}"'},
        json={"product_id": 204, "quantity": 10.0, "reason": "first stock"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    # Brand-new product had zero cost basis — we fall back to
    # products.purchase_price (20.00 in the seed) as the unit-cost
    # snapshot. total_cost = 10 * 20 = 200.
    assert float(body["quantity"]) == 10.0
    assert float(body["unit_cost_at_movement"]) == 20.0
    assert float(body["total_cost"]) == 200.0
    assert await _on_hand(204) == 10.0


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


async def test_create_adjustment_idempotency_replay(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """Replaying the same Idempotency-Key + body returns the cached body
    with ``Idempotent-Replay: true`` and DOES NOT create a second movement."""
    h = await _owner_headers(app, owner_user)
    etag = await _read_updated_at(201)
    body = {"product_id": 201, "quantity": 3.0, "reason": "replay me"}
    headers = {**h, "Idempotency-Key": _idem(), "If-Match": f'"{etag}"'}

    r1 = await app.post("/api/v1/inventory/adjustments", headers=headers, json=body)
    assert r1.status_code == 201, r1.text
    assert "Idempotent-Replay" not in r1.headers
    assert await _on_hand(201) == 53.0

    r2 = await app.post("/api/v1/inventory/adjustments", headers=headers, json=body)
    assert r2.status_code == 200, r2.text
    assert r2.headers.get("Idempotent-Replay") == "true"
    # On-hand did NOT advance further — the cached body was returned,
    # the side effects were rolled back / not re-executed.
    assert await _on_hand(201) == 53.0
    # Stock-movement count is still exactly 1 for product 201.
    assert await _stock_movement_count(201, "stock_adjustment") == 1
    # Audit count did not increment on the replay (the cached body was
    # returned; the audit row was written only on the first call).
    audit_after_replay = await _audit_count(201)
    # Run a third replay to confirm audit count stays put.
    r3 = await app.post("/api/v1/inventory/adjustments", headers=headers, json=body)
    assert r3.status_code == 200
    assert r3.headers.get("Idempotent-Replay") == "true"
    assert await _audit_count(201) == audit_after_replay


async def test_create_adjustment_idempotency_key_body_mismatch(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """Same key + different body → 409 idempotency_violation."""
    h = await _owner_headers(app, owner_user)
    etag = await _read_updated_at(201)
    idem = _idem()
    body_a = {"product_id": 201, "quantity": 1.0, "reason": "A"}
    body_b = {"product_id": 201, "quantity": 2.0, "reason": "B"}
    headers_a = {**h, "Idempotency-Key": idem, "If-Match": f'"{etag}"'}
    r1 = await app.post("/api/v1/inventory/adjustments", headers=headers_a, json=body_a)
    assert r1.status_code == 201, r1.text

    # ETag changed because we mutated the product row via the adjustment.
    etag2 = await _read_updated_at(201)
    headers_b = {**h, "Idempotency-Key": idem, "If-Match": f'"{etag2}"'}
    r2 = await app.post("/api/v1/inventory/adjustments", headers=headers_b, json=body_b)
    assert r2.status_code == 409, r2.text
    body = r2.json()
    assert body["error"]["code"] == "idempotency_violation"


# ---------------------------------------------------------------------------
# Capability boundary (positive — staff with capability)
# ---------------------------------------------------------------------------


async def test_create_adjustment_staff_with_capability_allowed(
    app: AsyncClient, staff_user: dict[str, Any]
) -> None:
    """When ``inventory.adjust`` is granted to staff (via per-user
    override), the staff user can perform the adjustment."""
    await _grant_inventory_adjust_to_staff()
    h = await _staff_headers(app, staff_user)
    etag = await _read_updated_at(201)
    r = await app.post(
        "/api/v1/inventory/adjustments",
        headers={**h, "Idempotency-Key": _idem(), "If-Match": f'"{etag}"'},
        json={"product_id": 201, "quantity": 1.0, "reason": "by staff"},
    )
    assert r.status_code == 201, r.text


# ---------------------------------------------------------------------------
# Phase 3A — ETag compatibility between InventorySummary and adjustment
# ---------------------------------------------------------------------------


async def test_etag_from_inventory_summary_matches_adjustment(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """Phase 3A regression: the ETag exposed in InventorySummary must be
    the exact same value the adjustment endpoint accepts as If-Match.

    This proves the contract path:
        InventorySummary.updated_at
          → frontend ETag construction
          → If-Match
          → POST /inventory/adjustments
    """
    from app.concurrency.master_etag import etag_and_version_from_updated_at

    h = await _owner_headers(app, owner_user)
    # 1. Read the inventory list (which now carries updated_at).
    r = await app.get("/api/v1/inventory", headers=h)
    assert r.status_code == 200, r.text
    rows = r.json()["data"]
    row201 = next(r for r in rows if r["product_id"] == 201)
    summary_updated_at = row201["updated_at"]

    # 2. Independently compute the canonical ETag from the same
    #    timestamp. Pydantic JSON serialization already produced the
    #    Z-suffixed form, so we re-derive via the canonical helper.
    from datetime import datetime

    ts = datetime.fromisoformat(summary_updated_at.replace("Z", "+00:00"))
    canonical_etag, _version = etag_and_version_from_updated_at(ts)

    # 3. Use that ETag as If-Match on the adjustment endpoint. Should
    #    be accepted (201).
    r2 = await app.post(
        "/api/v1/inventory/adjustments",
        headers={
            **h,
            "Idempotency-Key": _idem(),
            "If-Match": canonical_etag,
        },
        json={"product_id": 201, "quantity": 1.0, "reason": "etag from inventory summary"},
    )
    assert r2.status_code == 201, r2.text


__all__: list[str] = []
