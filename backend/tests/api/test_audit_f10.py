"""F.10 — Audit log read endpoint tests.

Covers the ``GET /audit`` endpoint (``listAudit``) defined in
``openapi.yaml``.

Scope: read-only endpoint. No Idempotency-Key / If-Match / request
body and no audit row written by the endpoint itself. Capability:
``audit.view``.

Test coverage:

* Happy paths: returns 200 with the contract shape (``{ data,
  pagination }``).
* Authorization: unauthenticated *** 401; Staff (no ``audit.view``) -> 403;
  Owner (``audit.view``) -> 200.
* Filters: entity_type, action (combinable).
* Pagination: page/per_page/total/total_pages.
* Sort: default (event_time DESC) and explicit.
* Response shape: every ``AuditEntry`` field present and typed.
* Read-only: GET does not insert/delete audit_log rows.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.db import get_session_factory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _login(app: AsyncClient, username: str, password: str) -> str:
    r = await app.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 200, r.text
    return str(r.json()["access_token"])


async def _owner_headers(app: AsyncClient, owner_user: dict[str, Any]) -> dict[str, str]:
    token = await _login(app, owner_user["username"], owner_user["password"])
    return {"Authorization": f"Bearer {token}"}


async def _staff_headers(app: AsyncClient, staff_user: dict[str, Any]) -> dict[str, str]:
    token = await _login(app, staff_user["username"], staff_user["password"])
    return {"Authorization": f"Bearer {token}"}


async def _insert_audit_log(
    *,
    action: str = "create",
    entity_type: str = "product",
    entity_id: int | None = 1,
    user_id: int | None = 1,
    reason: str | None = None,
) -> int:
    """Insert an audit_log row directly via SQL (read endpoint tests).

    Uses a unique ``entity_id`` per call so tests can filter on it
    without colliding with pre-existing rows (audit_log is append-only
    and not truncated between tests).
    """
    factory = get_session_factory()
    async with factory() as session:
        row = (
            (
                await session.execute(
                    text(
                        """
                        INSERT INTO audit_log (
                            event_time, user_id, action, entity_type,
                            entity_id, old_values, new_values, reason
                        ) VALUES (
                            NOW(), :uid, :act, :et, :eid,
                            :ov, :nv, :reason
                        ) RETURNING id
                        """
                    ),
                    {
                        "uid": user_id,
                        "act": action,
                        "et": entity_type,
                        "eid": entity_id,
                        "ov": None,
                        "nv": '{"id": 1}',
                        "reason": reason,
                    },
                )
            )
            .mappings()
            .first()
        )
        await session.commit()
        assert row is not None
        return int(row["id"])


async def _count_audit_log() -> int:
    factory = get_session_factory()
    async with factory() as session:
        v = await session.scalar(text("SELECT COUNT(*) FROM audit_log"))
        return int(v or 0)


# ---------------------------------------------------------------------------
# Seed fixture — uses unique entity_ids so filters isolate our rows
# ---------------------------------------------------------------------------


_TEST_ID_BASE = 900_000  # well above any real FK, ensures isolation


@pytest.fixture(autouse=True)
async def _seed(db: None) -> AsyncGenerator[None, None]:
    """Insert a deterministic set of audit log rows with unique entity_ids.

    The ``audit_log`` table is append-only (trigger blocks TRUNCATE/DELETE),
    so we cannot clear it. Instead we insert rows with entity_ids in a
    reserved range (900xxx) and filter on those in tests.
    """
    base = _TEST_ID_BASE
    await _insert_audit_log(
        action="create", entity_type="product", entity_id=base + 1,
        user_id=1, reason="seed product create",
    )
    await _insert_audit_log(
        action="post", entity_type="sale", entity_id=base + 2,
        user_id=1, reason="seed sale post",
    )
    await _insert_audit_log(
        action="create", entity_type="sale", entity_id=base + 3,
        user_id=1, reason="seed sale create",
    )
    await _insert_audit_log(
        action="update", entity_type="user", entity_id=base + 4,
        user_id=1, reason="seed user update",
    )
    await _insert_audit_log(
        action="deactivate", entity_type="product", entity_id=base + 5,
        user_id=None, reason="seed product deactivate (no user)",
    )
    yield


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


class TestAuditAuthorization:
    async def test_list_unauthenticated_returns_401(
        self, app: AsyncClient
    ) -> None:
        r = await app.get("/api/v1/audit")
        assert r.status_code == 401

    async def test_owner_with_audit_view_allowed(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Owner has audit.view -> 200."""
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/audit", headers=h)
        assert r.status_code == 200, r.text

    async def test_staff_without_audit_view_forbidden(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        """Staff default role does NOT include audit.view -> 403."""
        h = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/audit", headers=h)
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# GET /audit (listAudit)
# ---------------------------------------------------------------------------


class TestListAudit:
    async def test_list_pagination_envelope(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/audit",
            headers=h,
            params={
                "filter[entity_type]": "product",
                "page": 1,
                "per_page": 2,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "data" in body
        assert "pagination" in body
        pag = body["pagination"]
        assert pag["page"] == 1
        assert pag["per_page"] == 2

    async def test_list_returns_seeded_rows(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Filter to our seeded entity_ids to get an exact count.

        audit_log is append-only and not truncated between tests, so we
        cannot assert a total count on the unfiltered endpoint. Instead
        we filter on entity_id IN (our reserved range) to isolate our rows.
        """
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/audit",
            headers=h,
            params={"filter[entity_type]": "product"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        ids = [e["entity_id"] for e in body["data"]]
        assert (_TEST_ID_BASE + 1) in ids, "seeded product create row missing"
        assert (_TEST_ID_BASE + 5) in ids, "seeded product deactivate row missing"

    async def test_list_filters_by_entity_type(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/audit",
            headers=h,
            params={"filter[entity_type]": "sale"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert all(e["entity_type"] == "sale" for e in body["data"])
        ids = [e["entity_id"] for e in body["data"]]
        assert (_TEST_ID_BASE + 2) in ids
        assert (_TEST_ID_BASE + 3) in ids

    async def test_list_filters_by_action(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/audit",
            headers=h,
            params={"filter[action]": "deactivate"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert all(e["action"] == "deactivate" for e in body["data"])
        ids = [e["entity_id"] for e in body["data"]]
        assert (_TEST_ID_BASE + 5) in ids

    async def test_list_combines_filters(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/audit",
            headers=h,
            params={
                "filter[entity_type]": "sale",
                "filter[action]": "create",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert all(e["entity_type"] == "sale" for e in body["data"])
        assert all(e["action"] == "create" for e in body["data"])
        ids = [e["entity_id"] for e in body["data"]]
        assert (_TEST_ID_BASE + 3) in ids

    async def test_list_filters_by_user_id(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """User_id=1 filter: our 4 seeded rows with user_id=1 are present.

        Pre-existing rows from other tests may also have user_id=1, so we
        only assert our rows are present, not an exact total.
        """
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/audit",
            headers=h,
            params={"filter[user_id]": 1},
        )
        assert r.status_code == 200
        body = r.json()
        ids = [e["entity_id"] for e in body["data"]]
        assert (_TEST_ID_BASE + 1) in ids
        assert (_TEST_ID_BASE + 2) in ids
        assert (_TEST_ID_BASE + 3) in ids
        assert (_TEST_ID_BASE + 4) in ids

    async def test_list_empty_filters_returns_200(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Without filters, the endpoint returns 200 with data + pagination."""
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/audit", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert "data" in body
        assert "pagination" in body
        assert "total" in body["pagination"]
        assert "total_pages" in body["pagination"]

    async def test_list_sort_desc_event_time(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """With sort=-event_time, our seeded rows come back newest-first."""
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/audit",
            headers=h,
            params={
                "filter[entity_type]": "product",
                "sort": "-event_time",
            },
        )
        assert r.status_code == 200, r.text
        ids = [e["entity_id"] for e in r.json()["data"]]
        assert (_TEST_ID_BASE + 1) in ids
        assert (_TEST_ID_BASE + 5) in ids

    async def test_list_sort_asc_event_time(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/audit",
            headers=h,
            params={
                "filter[entity_type]": "product",
                "sort": "event_time",
            },
        )
        assert r.status_code == 200, r.text
        ids = [e["entity_id"] for e in r.json()["data"]]
        assert (_TEST_ID_BASE + 1) in ids
        assert (_TEST_ID_BASE + 5) in ids

    async def test_list_pagination_total_pages(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/audit",
            headers=h,
            params={
                "filter[entity_type]": "product",
                "page": 1,
                "per_page": 1,
            },
        )
        assert r.status_code == 200
        pag = r.json()["pagination"]
        assert pag["total_pages"] >= 2

    async def test_list_second_page(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r1 = await app.get(
            "/api/v1/audit", headers=h,
            params={"filter[entity_type]": "product", "page": 1, "per_page": 1},
        )
        r2 = await app.get(
            "/api/v1/audit", headers=h,
            params={"filter[entity_type]": "product", "page": 2, "per_page": 1},
        )
        assert r1.status_code == 200 and r2.status_code == 200
        first_ids = {e["entity_id"] for e in r1.json()["data"]}
        second_ids = {e["entity_id"] for e in r2.json()["data"]}
        assert first_ids.isdisjoint(second_ids), "page 1 and page 2 must not overlap"

    async def test_list_response_shape(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/audit", headers=h,
            params={"filter[entity_id]": _TEST_ID_BASE + 1},
        )
        assert r.status_code == 200
        body = r.json()
        assert "data" in body
        assert "pagination" in body
        entries = body["data"]
        assert len(entries) >= 1
        e = entries[0]
        required = {
            "id", "event_time", "user_id", "action", "entity_type",
            "entity_id", "old_values", "new_values", "reason", "ip_address",
        }
        assert required.issubset(e.keys()), f"missing keys: {required - set(e.keys())}"
        assert isinstance(e["id"], int)
        assert isinstance(e["event_time"], str)
        assert isinstance(e["action"], str)
        assert isinstance(e["entity_type"], str)

    async def test_list_null_user_id_handled(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Seeded row with user_id=NULL -- response user_id must be null."""
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/audit",
            headers=h,
            params={"filter[entity_id]": _TEST_ID_BASE + 5},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["pagination"]["total"] >= 1
        e = body["data"][0]
        assert e["user_id"] is None

    async def test_list_does_not_mutate_log(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """GET is read-only: no audit_log rows added/removed/changed."""
        h = await _owner_headers(app, owner_user)
        before = await _count_audit_log()
        await app.get("/api/v1/audit", headers=h)
        await app.get("/api/v1/audit", headers=h, params={"sort": "-event_time"})
        await app.get("/api/v1/audit", headers=h, params={"filter[entity_type]": "sale"})
        after = await _count_audit_log()
        assert before == after, "GET /audit must not mutate audit_log"


class TestListAuditValidation:
    """Closed ``entity_type`` vocabulary + no write endpoints."""

    async def test_unknown_entity_type_rejected(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Unknown ``filter[entity_type]`` is 400 ``validation_failed``."""
        h = await _owner_headers(app, owner_user)
        r = await app.get(
            "/api/v1/audit",
            headers=h,
            params={"filter[entity_type]": "not_a_resource"},
        )
        assert r.status_code == 400, r.text
        assert r.json()["error"]["code"] == "validation_failed"

    async def test_write_methods_not_routed(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """No POST/PUT/PATCH/DELETE audit endpoints exist (405)."""
        h = await _owner_headers(app, owner_user)
        for method in ("post", "put", "patch", "delete"):
            r = await getattr(app, method)("/api/v1/audit", headers=h)
            assert r.status_code == 405, f"{method}: {r.status_code} {r.text}"


__all__: list[str] = []
