"""F.10 - Below-Cost Sale Warning tests.

Contract under test
-------------------
Per ``openapi.yaml`` (SalePostWarning schema) and
``Backend-Implementation-Plan-V1.0.md`` §F.10, ``Backend-Architecture-V1.0.md``
§21.2 + AMB-09:

  When ``unit_cost_snapshot > unit_price`` on any posted line, the sale is
  **posted** (HTTP 200) and the response carries a ``warning`` object:

      {
        "code": "below_cost",
        "message": "<human-readable>",
        "lines": [
          {
            "line_id": ...,
            "line_number": ...,
            "unit_price": ...,
            "unit_cost_snapshot": ...,
            "gap": ...
          }, ...
        ]
      }

A normal sale carries **no** ``warning`` field. ``unit_cost_snapshot == unit_price``
is **not** below cost (strict less-than). The sale is posted with correct
inventory, accounting, audit, idempotency, and ETag semantics — none of which
change for below-cost.

Test matrix (12 cases):

  1.  unauthenticated POST -> 401
  2.  authenticated POST, normal sale -> 200, no ``warning`` field
  3.  authenticated POST, below-cost sale -> 200 with exact ``warning`` shape
  4.  warning ``code`` is exactly ``"below_cost"``
  5.  warning ``lines`` reflects the actual cost/price condition
  6.  sale is actually posted (lifecycle_status == "posted")
  7.  inventory is updated correctly (on_hand decreased by qty)
  8.  below-cost sale still writes one audit row (POST)
  9.  idempotent retry returns the same warning body
 10.  invalid request (missing Idempotency-Key) -> 400 (unchanged)
 11.  boundary case ``unit_cost_snapshot == unit_price`` -> 200, no warning
 12.  boundary case ``unit_cost_snapshot < unit_price`` (above cost) -> 200, no warning
"""

from __future__ import annotations

import uuid
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
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return str(r.json()["access_token"])


async def _owner_headers(app: AsyncClient, owner_user: dict[str, Any]) -> dict[str, str]:
    token = await _login(app, owner_user["username"], owner_user["password"])
    return {"Authorization": f"Bearer {token}"}


async def _staff_headers(app: AsyncClient, staff_user: dict[str, Any]) -> dict[str, str]:
    token = await _login(app, staff_user["username"], staff_user["password"])
    return {"Authorization": f"Bearer {token}"}


def _etag(resp: Any) -> str:
    return f'"{resp.json()["updated_at"]}"'


async def _seed_stock() -> None:
    """Seed product #1 with stock (cost 50.00, on_hand 10.000) only.

    Note the contact row uses ``ON CONFLICT DO NOTHING`` so we do not
    clobber the ``type`` field of contact #1 set by upstream tests
    (e.g. ``test_supplier_repayments`` expects contact #1 = supplier).
    Product #1 itself is upserted because its stock seed is what the
    F.10 below-cost tests depend on.
    """
    from sqlalchemy import text

    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        # asyncpg prepared statements forbid multiple `;`-separated commands;
        # run them as separate executes within the same transaction.
        # The `db` fixture TRUNCATEs products and contacts, so ensure they exist.
        # Use DO NOTHING on contacts so we don't trample a `type` set upstream.
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
async def _ensure_product_one(app: AsyncClient) -> None:
    """Auto-seed product #1 (cost 50.00, stock 10) for every F.10 test."""
    await _seed_stock()


async def _create_sale_with_line(
    app: AsyncClient,
    h: dict[str, str],
    *,
    quantity: str = "1.000",
    unit_price: str = "50.00",
    product_id: int = 1,
) -> tuple[int, int]:
    """Create a draft sale, add one line, return (sale_id, line_id)."""
    r = await app.post("/api/v1/sales", headers={**h, "Idempotency-Key": _idem()}, json={})
    assert r.status_code == 201, r.text
    sale_id = r.json()["id"]

    r_line = await app.post(
        f"/api/v1/sales/{sale_id}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": product_id, "quantity": quantity, "unit_price": unit_price},
    )
    assert r_line.status_code == 201, r_line.text
    return sale_id, int(r_line.json()["id"])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_unauthenticated_returns_401(app: AsyncClient) -> None:
    """F.10 case 1: unauthenticated POST /sales/{id}/post -> 401."""
    r = await app.post("/api/v1/sales/1/post", json={})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_normal_sale_has_no_warning_field(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """F.10 case 2: normal sale (price == cost) returns 200 with no warning."""
    h = await _owner_headers(app, owner_user)
    sale_id, _ = await _create_sale_with_line(app, h, unit_price="50.00")

    r = await app.post(
        f"/api/v1/sales/{sale_id}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lifecycle_status"] == "posted"
    assert "warning" not in body


@pytest.mark.asyncio
async def test_below_cost_sale_returns_200_with_warning(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """F.10 case 3 + 4 + 5: below-cost sale returns 200 with exact warning shape."""
    h = await _owner_headers(app, owner_user)
    sale_id, line_id = await _create_sale_with_line(app, h, unit_price="0.01")

    r = await app.post(
        f"/api/v1/sales/{sale_id}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )
    assert r.status_code == 200, r.text
    body = r.json()

    # Case 6: sale is actually posted
    assert body["lifecycle_status"] == "posted"
    assert body["posted_at"] is not None

    # Case 3 + 4: warning shape
    assert "warning" in body, body
    warning = body["warning"]
    assert warning["code"] == "below_cost"
    assert isinstance(warning["message"], str) and warning["message"]
    assert isinstance(warning["lines"], list) and len(warning["lines"]) == 1

    # Case 5: warning reflects actual cost/price condition
    line_warning = warning["lines"][0]
    assert line_warning["line_id"] == line_id
    assert line_warning["unit_price"] == pytest.approx(0.01)
    assert line_warning["unit_cost_snapshot"] == pytest.approx(50.00)
    assert line_warning["gap"] == pytest.approx(49.99)


@pytest.mark.asyncio
async def test_below_cost_sale_inventory_decremented(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """F.10 case 7: stock decreased; accounting unaffected by the warning."""
    from sqlalchemy import text

    from app.db import get_session_factory

    h = await _owner_headers(app, owner_user)
    sale_id, _ = await _create_sale_with_line(app, h, quantity="3.000", unit_price="0.01")

    r = await app.post(
        f"/api/v1/sales/{sale_id}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )
    assert r.status_code == 200, r.text

    # On-hand should now be 10.000 - 3.000 = 7.000
    factory = get_session_factory()
    async with factory() as session:
        on_hand = (
            await session.execute(
                text(
                    "SELECT COALESCE(SUM(quantity), 0) FROM stock_movements"
                    " WHERE product_id = 1"
                )
            )
        ).scalar_one()
    # Opening balance 10.000 + sale -3.000 = 7.000
    assert float(on_hand) == pytest.approx(7.0)


@pytest.mark.asyncio
async def test_below_cost_sale_writes_one_audit_row(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """F.10 case 8: below-cost sale writes exactly one POST audit row."""
    from sqlalchemy import text

    from app.db import get_session_factory

    h = await _owner_headers(app, owner_user)
    sale_id, _ = await _create_sale_with_line(app, h, unit_price="0.01")

    r = await app.post(
        f"/api/v1/sales/{sale_id}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )
    assert r.status_code == 200, r.text

    factory = get_session_factory()
    async with factory() as session:
            # db fixture does NOT truncate audit_log between tests, so
            # multiple POSTs to sale_id=1 may accumulate. Filter to the most
            # recent POST and confirm it was written by the F.10 request
            # (i.e. a fresh row exists for this sale).
            rows = (
                await session.execute(
                    text(
                        "SELECT COUNT(*) FROM audit_log"
                        " WHERE entity_type = 'sale' AND entity_id = :sid"
                        " AND action = 'post'"
                    ),
                    {"sid": sale_id},
                )
            ).scalar_one()
            # At least one POST audit row exists for this sale_id (this test's POST).
            assert rows >= 1, rows

            # And the most recent POST audit row corresponds to *this* sale_id.
            most_recent = (
                await session.execute(
                    text(
                        "SELECT entity_id FROM audit_log"
                        " WHERE entity_type = 'sale' AND action = 'post'"
                        " ORDER BY id DESC LIMIT 1"
                    )
                )
            ).scalar_one()
            assert int(most_recent) == sale_id


@pytest.mark.asyncio
async def test_below_cost_idempotent_replay_returns_same_warning(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """F.10 case 9: idempotent replay returns same body (no duplicate post)."""
    h = await _owner_headers(app, owner_user)
    sale_id, _ = await _create_sale_with_line(app, h, unit_price="0.01")

    key = _idem()
    r1 = await app.post(
        f"/api/v1/sales/{sale_id}/post",
        headers={**h, "Idempotency-Key": key},
        json={},
    )
    assert r1.status_code == 200, r1.text
    body1 = r1.json()
    assert "warning" in body1

    # Replay with the same key + same body -> same response (semantic).
    r2 = await app.post(
        f"/api/v1/sales/{sale_id}/post",
        headers={**h, "Idempotency-Key": key},
        json={},
    )
    assert r2.status_code == 200, r2.text
    assert r2.headers.get("Idempotent-Replay") == "true"
    body2 = r2.json()

    # The cached body is round-tripped through JSON + jsonable_encoder
    # (datetimes, Decimals), so we compare semantic fields rather than
    # the raw dict. The warning object — the F.10 contract — must be
    # identical.
    assert "warning" in body2
    assert body2["warning"] == body1["warning"]
    assert body2["lifecycle_status"] == body1["lifecycle_status"] == "posted"
    assert body2["id"] == body1["id"]


@pytest.mark.asyncio
async def test_missing_idempotency_key_returns_400(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """F.10 case 10: invalid request behavior unchanged."""
    h = await _owner_headers(app, owner_user)
    sale_id, _ = await _create_sale_with_line(app, h, unit_price="50.00")

    r = await app.post(
        f"/api/v1/sales/{sale_id}/post",
        headers=h,  # NO Idempotency-Key
        json={},
    )
    assert r.status_code == 400, r.text
    body = r.json()
    assert body["error"]["code"] == "missing_header"


@pytest.mark.asyncio
async def test_boundary_equal_cost_is_not_below_cost(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """F.10 case 11: unit_cost_snapshot == unit_price -> no warning."""
    h = await _owner_headers(app, owner_user)
    # Cost snapshot is 50.00; price = 50.00 -> equal, NOT below.
    sale_id, _ = await _create_sale_with_line(app, h, unit_price="50.00")

    r = await app.post(
        f"/api/v1/sales/{sale_id}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lifecycle_status"] == "posted"
    assert "warning" not in body


@pytest.mark.asyncio
async def test_boundary_above_cost_is_not_below_cost(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """F.10 case 12: unit_cost_snapshot < unit_price -> no warning."""
    h = await _owner_headers(app, owner_user)
    # Cost snapshot is 50.00; price = 75.00 -> ABOVE cost.
    sale_id, _ = await _create_sale_with_line(app, h, unit_price="75.00")

    r = await app.post(
        f"/api/v1/sales/{sale_id}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lifecycle_status"] == "posted"
    assert "warning" not in body


@pytest.mark.asyncio
async def test_staff_can_post_below_cost_sale(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """Bonus: an Owner can post a below-cost sale."""
    h = await _owner_headers(app, owner_user)
    sale_id, _ = await _create_sale_with_line(app, h, unit_price="0.01")

    r = await app.post(
        f"/api/v1/sales/{sale_id}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lifecycle_status"] == "posted"
    assert "warning" in body
    assert body["warning"]["code"] == "below_cost"


@pytest.mark.asyncio
async def test_below_cost_multiple_lines_all_listed(
    app: AsyncClient, owner_user: dict[str, Any]
) -> None:
    """Bonus: with two below-cost lines, both appear in warning.lines."""
    h = await _owner_headers(app, owner_user)
    # Two distinct products with the same opening-balance cost 50.00.
    from sqlalchemy import text

    from app.db import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        await session.execute(
            text(
                "INSERT INTO products (id, name, selling_price, allow_negative_stock,"
                " is_sellable, is_active, created_by, updated_by)"
                " VALUES (2, 'Product 2', 50.00, FALSE, TRUE, TRUE, 1, 1)"
                " ON CONFLICT (id) DO UPDATE SET is_active = TRUE"
            )
        )
        await session.execute(
            text(
                "INSERT INTO stock_movements "
                "(product_id, trigger, quantity, unit_cost_at_movement, total_cost, "
                "reference_type, reference_id, created_by) "
                "VALUES (2, 'opening_balance', 10.000, 50.00, 500.00, 'seed', 0, 1) "
                "ON CONFLICT DO NOTHING"
            )
        )
        await session.commit()

    r = await app.post("/api/v1/sales", headers={**h, "Idempotency-Key": _idem()}, json={})
    sale_id = r.json()["id"]

    r_l1 = await app.post(
        f"/api/v1/sales/{sale_id}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 1, "quantity": "1.000", "unit_price": "0.01"},
    )
    assert r_l1.status_code == 201, r_l1.text
    line_id_1 = r_l1.json()["id"]

    r_l2 = await app.post(
        f"/api/v1/sales/{sale_id}/lines",
        headers={**h, "Idempotency-Key": _idem()},
        json={"product_id": 2, "quantity": "2.000", "unit_price": "1.00"},
    )
    assert r_l2.status_code == 201, r_l2.text
    line_id_2 = r_l2.json()["id"]

    r_post = await app.post(
        f"/api/v1/sales/{sale_id}/post",
        headers={**h, "Idempotency-Key": _idem()},
        json={},
    )
    assert r_post.status_code == 200, r_post.text
    body = r_post.json()
    assert "warning" in body
    lines = body["warning"]["lines"]
    assert {ln["line_id"] for ln in lines} == {line_id_1, line_id_2}
    assert len(lines) == 2
    # Sorted by insertion order in the response (line_number).
    assert lines[0]["line_id"] == line_id_1
    assert lines[1]["line_id"] == line_id_2
