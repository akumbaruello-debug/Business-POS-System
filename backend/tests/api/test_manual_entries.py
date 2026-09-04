"""E.8 — Manual entries API tests.

Covers four endpoints from openapi.yaml §15.12:

* GET  /manual-entries             — listManualEntries
* POST /manual-entries             — createManualEntry (Idempotency-Key)
* GET  /manual-entries/{id}        — getManualEntry
* POST /manual-entries/{id}/cancel — cancelManualEntry (Idempotency-Key + If-Match)

Capabilities:
  list/get → manual_entry.view
  create   → manual_entry.create_income OR manual_entry.create_expense (oneOf)
  cancel   → manual_entry.cancel

Staff default caps do NOT include any manual_entry.* cap (per
``app/authz/caps.STAFF_DEFAULT_CAPABILITIES``), so Staff is forbidden.

Tests obtain the ETag authoritatively from the ``ETag`` response
header (the same value the real client would use) and independently
verify DB side effects (exactly one entry, one cash movement, etc.).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

from app.db import UnitOfWork

FMAN = "manual_finance_entries"
CASH = "cash_movements"


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


async def _owner(app: AsyncClient, owner_user: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {await _login(app, 'owner', 'OwnerPass123!')}"} if False else {"Authorization": f"Bearer {await _login(app, owner_user['username'], owner_user['password'])}"}


async def _staff(app: AsyncClient, staff_user: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {await _login(app, staff_user['username'], staff_user['password'])}"}


async def _seed_cash(amount: float = 1000.0) -> None:
    async with UnitOfWork() as uow:
        await uow.execute(
            "INSERT INTO cash_movements "
            "(amount, direction, trigger, payment_method_id, reference_type, reference_id, created_by) "
            "VALUES (:amt, 'in', 'manual_income', 1, 'seed', 0, 1)",
            {"amt": amount},
        )
        await uow.commit()


def _headers(extra: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in extra.items()}


@pytest.fixture
async def income_entry(app: AsyncClient, owner_user: dict) -> dict:
    h = await _owner(app, owner_user)
    r = await app.post(
        "/api/v1/manual-entries",
        json={
            "category_id": 1,
            "entry_type": "income",
            "amount": 100.0,
            "payment_method_id": 1,
            "entry_date": "2026-09-02T12:00:00Z",
            "notes": "Income",
        },
        headers={**_headers(h), "Idempotency-Key": _idem()},
    )
    assert r.status_code == 201, r.text
    return r.json()


@pytest.fixture
async def expense_entry(app: AsyncClient, owner_user: dict) -> dict:
    await _seed_cash(1000.0)
    h = await _owner(app, owner_user)
    r = await app.post(
        "/api/v1/manual-entries",
        json={
            "category_id": 4,
            "entry_type": "expense",
            "amount": 50.0,
            "payment_method_id": 1,
            "entry_date": "2026-09-02T12:00:00Z",
            "notes": "Rent",
        },
        headers={**_headers(h), "Idempotency-Key": _idem()},
    )
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------------------
# LIST
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_unauth(app: AsyncClient) -> None:
    assert (await app.get("/api/v1/manual-entries")).status_code == 401


@pytest.mark.asyncio
async def test_list_owner_ok(app: AsyncClient, owner_user: dict, income_entry: dict) -> None:
    r = await app.get("/api/v1/manual-entries", headers=await _owner(app, owner_user))
    assert r.status_code == 200
    b = r.json()
    assert "data" in b and "pagination" in b
    assert any(e["id"] == income_entry["id"] for e in b["data"])


@pytest.mark.asyncio
async def test_list_staff_forbidden(app: AsyncClient, staff_user: dict) -> None:
    r = await app.get("/api/v1/manual-entries", headers=await _staff(app, staff_user))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_list_filters(app, owner_user, income_entry, expense_entry) -> None:
    h = await _owner(app, owner_user)
    r = await app.get("/api/v1/manual-entries?filter[entry_type]=income", headers=h)
    assert r.status_code == 200
    assert all(e["entry_type"] == "income" for e in r.json()["data"])

    r = await app.get("/api/v1/manual-entries?filter[category_id]=1", headers=h)
    assert all(e["category_id"] == 1 for e in r.json()["data"])

    r = await app.get("/api/v1/manual-entries?filter[lifecycle_status]=posted", headers=h)
    assert all(e["lifecycle_status"] == "posted" for e in r.json()["data"])


# ---------------------------------------------------------------------------
# GET
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_ok(app, owner_user, income_entry) -> None:
    h = await _owner(app, owner_user)
    r = await app.get(f"/api/v1/manual-entries/{income_entry['id']}", headers=h)
    assert r.status_code == 200
    b = r.json()
    assert b["id"] == income_entry["id"]
    assert b["entry_type"] == "income"
    assert b["lifecycle_status"] == "posted"
    assert "ETag" in r.headers


@pytest.mark.asyncio
async def test_get_missing(app, owner_user) -> None:
    h = await _owner(app, owner_user)
    r = await app.get("/api/v1/manual-entries/999999", headers=h)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# CREATE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_income_creates_entry_and_cash(app, owner_user) -> None:
    h = await _owner(app, owner_user)
    r = await app.post(
        "/api/v1/manual-entries",
        json={"category_id": 1, "entry_type": "income", "amount": 200.0, "payment_method_id": 1},
        headers={**_headers(h), "Idempotency-Key": _idem()},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["entry_type"] == "income"
    assert body["amount"] == 200.0
    assert body["lifecycle_status"] == "posted"
    assert "ETag" in r.headers
    eid = body["id"]
    async with UnitOfWork() as uow:
        n = await uow.scalar(f"SELECT COUNT(*) FROM {FMAN} WHERE id=:id", {"id": eid})
        assert n == 1
        cm = await uow.first_row(
            f"SELECT amount, direction, trigger FROM {CASH} WHERE reference_type='manual_entry' AND reference_id=:id",
            {"id": eid},
        )
        assert cm["amount"] == 200.0
        assert cm["direction"] == "in"
        assert cm["trigger"] == "manual_income"
        # audit row exists for this entry (audit_log is append-only across tests)
        a = await uow.scalar(
            "SELECT COUNT(*) FROM audit_log WHERE entity_type='manual_entry' AND entity_id=:id AND action='create'",
            {"id": eid},
        )
        assert a >= 1


@pytest.mark.asyncio
async def test_create_expense(app, owner_user) -> None:
    await _seed_cash(500.0)
    h = await _owner(app, owner_user)
    r = await app.post(
        "/api/v1/manual-entries",
        json={"category_id": 4, "entry_type": "expense", "amount": 100.0, "payment_method_id": 1},
        headers={**_headers(h), "Idempotency-Key": _idem()},
    )
    assert r.status_code == 201
    assert r.json()["entry_type"] == "expense"
    eid = r.json()["id"]
    async with UnitOfWork() as uow:
        cm = await uow.first_row(
            f"SELECT amount, direction, trigger FROM {CASH} WHERE reference_type='manual_entry' AND reference_id=:id",
            {"id": eid},
        )
        assert cm["amount"] == -100.0
        assert cm["direction"] == "out"
        assert cm["trigger"] == "manual_expense"


@pytest.mark.asyncio
async def test_create_unauth(app) -> None:
    r = await app.post(
        "/api/v1/manual-entries",
        json={"category_id": 1, "entry_type": "income", "amount": 10.0, "payment_method_id": 1},
        headers={"Idempotency-Key": _idem()},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_create_staff_forbidden(app, staff_user) -> None:
    h = await _staff(app, staff_user)
    r = await app.post(
        "/api/v1/manual-entries",
        json={"category_id": 1, "entry_type": "income", "amount": 10.0, "payment_method_id": 1},
        headers={**_headers(h), "Idempotency-Key": _idem()},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_create_insufficient_cash(app, owner_user) -> None:
    h = await _owner(app, owner_user)
    r = await app.post(
        "/api/v1/manual-entries",
        json={"category_id": 4, "entry_type": "expense", "amount": 100.0, "payment_method_id": 1},
        headers={**_headers(h), "Idempotency-Key": _idem()},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "insufficient_cash"


@pytest.mark.asyncio
async def test_create_category_type_mismatch(app, owner_user) -> None:
    h = await _owner(app, owner_user)
    r = await app.post(
        "/api/v1/manual-entries",
        json={"category_id": 1, "entry_type": "expense", "amount": 10.0, "payment_method_id": 1},
        headers={**_headers(h), "Idempotency-Key": _idem()},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "manual_entry_duplicate_of_derived"


@pytest.mark.asyncio
async def test_create_category_inactive(app, owner_user) -> None:
    async with UnitOfWork() as uow:
        await uow.execute("UPDATE financial_categories SET is_active=false WHERE id=1")
        await uow.commit()
    h = await _owner(app, owner_user)
    r = await app.post(
        "/api/v1/manual-entries",
        json={"category_id": 1, "entry_type": "income", "amount": 10.0, "payment_method_id": 1},
        headers={**_headers(h), "Idempotency-Key": _idem()},
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "business_rule_violation"


@pytest.mark.asyncio
async def test_create_missing_idempotency_key(app, owner_user) -> None:
    h = await _owner(app, owner_user)
    r = await app.post(
        "/api/v1/manual-entries",
        json={"category_id": 1, "entry_type": "income", "amount": 10.0, "payment_method_id": 1},
        headers=_headers(h),
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "missing_header"


@pytest.mark.asyncio
async def test_create_idempotency_replay(app, owner_user) -> None:
    h = await _owner(app, owner_user)
    key = _idem()
    body = {"category_id": 1, "entry_type": "income", "amount": 50.0, "payment_method_id": 1}
    r1 = await app.post(
        "/api/v1/manual-entries", json=body, headers={**_headers(h), "Idempotency-Key": key}
    )
    assert r1.status_code == 201
    eid = r1.json()["id"]
    r2 = await app.post(
        "/api/v1/manual-entries", json=body, headers={**_headers(h), "Idempotency-Key": key}
    )
    assert r2.status_code == 200
    assert r2.headers.get("Idempotent-Replay") == "true"
    assert r2.json()["id"] == eid
    # exactly one entry + one cash movement
    async with UnitOfWork() as uow:
        n = await uow.scalar(f"SELECT COUNT(*) FROM {FMAN} WHERE id=:id", {"id": eid})
        assert n == 1
        c = await uow.scalar(
            f"SELECT COUNT(*) FROM {CASH} WHERE reference_type='manual_entry' AND reference_id=:id",
            {"id": eid},
        )
        assert c == 1


@pytest.mark.asyncio
async def test_create_idempotency_conflict(app, owner_user) -> None:
    h = await _owner(app, owner_user)
    key = _idem()
    body = {"category_id": 1, "entry_type": "income", "amount": 50.0, "payment_method_id": 1}
    await app.post(
        "/api/v1/manual-entries", json=body, headers={**_headers(h), "Idempotency-Key": key}
    )
    r2 = await app.post(
        "/api/v1/manual-entries",
        json={**body, "amount": 60.0},
        headers={**_headers(h), "Idempotency-Key": key},
    )
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "idempotency_violation"


@pytest.mark.asyncio
async def test_create_invalid_amount(app, owner_user) -> None:
    h = await _owner(app, owner_user)
    r = await app.post(
        "/api/v1/manual-entries",
        json={"category_id": 1, "entry_type": "income", "amount": -10.0, "payment_method_id": 1},
        headers={**_headers(h), "Idempotency-Key": _idem()},
    )
    assert r.status_code == 400  # validation_failed (project maps Pydantic errors → 400)
    assert r.json()["error"]["code"] == "validation_failed"


@pytest.mark.asyncio
async def test_create_rollback_on_bad_payment(app, owner_user) -> None:
    h = await _owner(app, owner_user)
    r = await app.post(
        "/api/v1/manual-entries",
        json={"category_id": 1, "entry_type": "income", "amount": 10.0, "payment_method_id": 999},
        headers={**_headers(h), "Idempotency-Key": _idem()},
    )
    assert r.status_code == 404
    async with UnitOfWork() as uow:
        n = await uow.scalar(f"SELECT COUNT(*) FROM {FMAN}")
        assert n == 0


# ---------------------------------------------------------------------------
# CANCEL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_ok(app, owner_user, income_entry) -> None:
    h = await _owner(app, owner_user)
    eid = income_entry["id"]
    etag = (await app.get(f"/api/v1/manual-entries/{eid}", headers=h)).headers["ETag"]
    r = await app.post(
        f"/api/v1/manual-entries/{eid}/cancel",
        json={"reason": "Test cancel"},
        headers={**_headers(h), "Idempotency-Key": _idem(), "If-Match": etag},
    )
    assert r.status_code == 200
    b = r.json()
    assert b["lifecycle_status"] == "cancelled"
    assert b["cancellation_reason"] == "Test cancel"
    assert b["cancelled_by"] is not None
    async with UnitOfWork() as uow:
        cms = await uow.fetch_all(
            f"SELECT amount, direction, trigger FROM {CASH} "
            f"WHERE reference_type='manual_entry' AND reference_id=:id ORDER BY id",
            {"id": eid},
        )
        assert len(cms) == 2
        assert cms[0]["direction"] == "in"
        assert cms[1]["direction"] == "out"
        assert cms[1]["amount"] == -cms[0]["amount"]
        assert cms[1]["trigger"] == "manual_expense"
        # audit
        a = await uow.scalar(
            "SELECT COUNT(*) FROM audit_log WHERE entity_type='manual_entry' AND entity_id=:id AND action='cancel'",
            {"id": eid},
        )
        assert a == 1


@pytest.mark.asyncio
async def test_cancel_missing_if_match(app, owner_user, income_entry) -> None:
    h = await _owner(app, owner_user)
    r = await app.post(
        f"/api/v1/manual-entries/{income_entry['id']}/cancel",
        json={"reason": "x"},
        headers={**_headers(h), "Idempotency-Key": _idem()},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "missing_header"


@pytest.mark.asyncio
async def test_cancel_stale_etag(app, owner_user, income_entry) -> None:
    h = await _owner(app, owner_user)
    r = await app.post(
        f"/api/v1/manual-entries/{income_entry['id']}/cancel",
        json={"reason": "x"},
        headers={**_headers(h), "Idempotency-Key": _idem(), "If-Match": '"stale-etag"'},
    )
    assert r.status_code == 412
    assert r.json()["error"]["code"] == "version_mismatch"


@pytest.mark.asyncio
async def test_cancel_malformed_etag(app, owner_user, income_entry) -> None:
    h = await _owner(app, owner_user)
    r = await app.post(
        f"/api/v1/manual-entries/{income_entry['id']}/cancel",
        json={"reason": "x"},
        headers={**_headers(h), "Idempotency-Key": _idem(), "If-Match": "not-quoted"},
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_header"


@pytest.mark.asyncio
async def test_cancel_already_cancelled(app, owner_user, income_entry) -> None:
    h = await _owner(app, owner_user)
    eid = income_entry["id"]
    etag = (await app.get(f"/api/v1/manual-entries/{eid}", headers=h)).headers["ETag"]
    r1 = await app.post(
        f"/api/v1/manual-entries/{eid}/cancel",
        json={"reason": "first"},
        headers={**_headers(h), "Idempotency-Key": _idem(), "If-Match": etag},
    )
    assert r1.status_code == 200
    # cancel again — new etag from first cancel response
    etag2 = r1.headers["ETag"]
    r2 = await app.post(
        f"/api/v1/manual-entries/{eid}/cancel",
        json={"reason": "second"},
        headers={**_headers(h), "Idempotency-Key": _idem(), "If-Match": etag2},
    )
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "conflict"
    async with UnitOfWork() as uow:
        c = await uow.scalar(
            f"SELECT COUNT(*) FROM {CASH} WHERE reference_type='manual_entry' AND reference_id=:id",
            {"id": eid},
        )
        assert c == 2  # no double reversal


@pytest.mark.asyncio
async def test_cancel_idempotency(app, owner_user, income_entry) -> None:
    h = await _owner(app, owner_user)
    eid = income_entry["id"]
    etag = (await app.get(f"/api/v1/manual-entries/{eid}", headers=h)).headers["ETag"]
    key = _idem()
    r1 = await app.post(
        f"/api/v1/manual-entries/{eid}/cancel",
        json={"reason": "idem"},
        headers={**_headers(h), "Idempotency-Key": key, "If-Match": etag},
    )
    assert r1.status_code == 200
    r2 = await app.post(
        f"/api/v1/manual-entries/{eid}/cancel",
        json={"reason": "idem"},
        headers={**_headers(h), "Idempotency-Key": key, "If-Match": etag},
    )
    assert r2.status_code == 200
    assert r2.headers.get("Idempotent-Replay") == "true"
    assert r2.json()["id"] == r1.json()["id"]
    async with UnitOfWork() as uow:
        c = await uow.scalar(
            f"SELECT COUNT(*) FROM {CASH} WHERE reference_type='manual_entry' AND reference_id=:id",
            {"id": eid},
        )
        assert c == 2


@pytest.mark.asyncio
async def test_cancel_staff_forbidden(app, staff_user, income_entry) -> None:
    h = await _staff(app, staff_user)
    r = await app.post(
        f"/api/v1/manual-entries/{income_entry['id']}/cancel",
        json={"reason": "x"},
        headers={**_headers(h), "Idempotency-Key": _idem(), "If-Match": '"fake"'},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_cancel_missing_entry(app, owner_user) -> None:
    h = await _owner(app, owner_user)
    r = await app.post(
        "/api/v1/manual-entries/999999/cancel",
        json={"reason": "x"},
        headers={**_headers(h), "Idempotency-Key": _idem(), "If-Match": '"any"'},
    )
    assert r.status_code == 404
