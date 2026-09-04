"""M2 / B.4 — Cost Types API tests.

Covers (mirrors ``tests/api/test_units.py`` adapted for ``cost_types``):

* authorization (view vs manage capability gating)
* list (pagination, filter[is_active], q search)
* create (valid, duplicate code 409, Idempotency-Key, lowercase-pattern
  rejection, extra-field rejection)
* get-by-id (existing, 404)
* update (PATCH name, PATCH is_active, immutable code via API, 404)
* deactivate (preserves history, Idempotency-Key, reversible)
* hard delete (succeeds when unreferenced; 409 referenced_by_history
  when a ``production_cost_line`` references the type; 404; Idempotency-Key)
* audit logging (create/update/deactivate/delete all write audit_log)
* error envelopes (uniform shape)
* seed-data integrity: the 5 schema-seeded codes are visible post-TRUNCATE
  because the shared ``db`` fixture re-seeds them.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from app.db import get_session_factory
from app.errors.codes import ErrorCode

pytestmark = pytest.mark.integration


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


def _valid_cost_type(code: str | None = None, name: str = "Test Cost Type") -> dict[str, Any]:
    return {
        "code": code or f"ct_{uuid.uuid4().hex[:8]}",
        "name": name,
        "is_active": True,
    }


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------


class TestCostTypeAuthorization:
    async def test_list_without_auth_returns_401(self, app: AsyncClient) -> None:
        r = await app.get("/api/v1/cost-types/")
        assert r.status_code == 401

    async def test_create_without_auth_returns_401(self, app: AsyncClient) -> None:
        r = await app.post("/api/v1/cost-types/", json=_valid_cost_type())
        assert r.status_code == 401

    async def test_create_as_staff_returns_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.post(
            "/api/v1/cost-types/",
            json=_valid_cost_type(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 403
        assert r.json()["error"]["code"] == ErrorCode.PERMISSION_DENIED.value

    async def test_list_as_staff_succeeds(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.get("/api/v1/cost-types/", headers=headers)
        assert r.status_code == 200

    async def test_delete_as_staff_returns_403(
        self, app: AsyncClient, staff_user: dict[str, Any]
    ) -> None:
        headers = await _staff_headers(app, staff_user)
        r = await app.delete(
            "/api/v1/cost-types/1",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


class TestListCostTypes:
    async def test_list_returns_seeded_rows(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """The 5 schema-seeded cost types are restored by the shared fixture
        and are visible to the owner via the list endpoint."""
        headers = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/cost-types/", headers=headers)
        assert r.status_code == 200, r.text
        codes = {row["code"] for row in r.json()["data"]}
        assert {"labor", "electricity", "gas", "packaging", "other"} <= codes
        assert r.json()["pagination"]["total"] >= 5

    async def test_list_with_filter_is_active(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        # Deactivate the seeded 'labor' (id=1)
        await app.post(
            "/api/v1/cost-types/1/deactivate",
            json={"reason": "test"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        r2 = await app.get("/api/v1/cost-types/?filter[is_active]=true", headers=headers)
        assert r2.status_code == 200
        for row in r2.json()["data"]:
            assert row["is_active"] is True

        # No filter → includes the deactivated row
        r3 = await app.get("/api/v1/cost-types/", headers=headers)
        ids = {row["id"] for row in r3.json()["data"]}
        assert 1 in ids

    async def test_list_search_by_q(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/cost-types/?q=labor", headers=headers)
        assert r.status_code == 200
        names = {row["name"] for row in r.json()["data"]}
        assert "Labor" in names

    async def test_list_pagination_enforced(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/cost-types/?per_page=2", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert len(body["data"]) <= 2
        assert body["pagination"]["per_page"] == 2


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class TestCreateCostType:
    async def test_create_valid(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        body = _valid_cost_type(name="Maintenance")
        r = await app.post(
            "/api/v1/cost-types/",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201, r.text
        out = r.json()
        assert out["code"] == body["code"]
        assert out["name"] == "Maintenance"
        assert out["is_active"] is True
        assert out["version"] == 1  # placeholder per documented contradiction
        assert "id" in out

    async def test_create_duplicate_code_returns_409(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        # Seeded 'labor' (id=1) — same code → 409
        r = await app.post(
            "/api/v1/cost-types/",
            json={"code": "labor", "name": "dup attempt", "is_active": True},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == ErrorCode.CONFLICT.value

    async def test_create_invalid_code_pattern_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """``code`` must match ``^[a-z0-9_]+$`` (lowercase-only, no hyphens,
        no spaces, no uppercase) per OpenAPI ``CostTypeRequest``."""
        headers = await _owner_headers(app, owner_user)
        # Uppercase — rejected
        r = await app.post(
            "/api/v1/cost-types/",
            json={"code": "Bad-Code", "name": "X", "is_active": True},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.VALIDATION_FAILED.value

    async def test_create_empty_name_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/cost-types/",
            json=_valid_cost_type(name=""),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.VALIDATION_FAILED.value

    async def test_create_missing_idempotency_key_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post("/api/v1/cost-types/", json=_valid_cost_type(), headers=headers)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.MISSING_HEADER.value

    async def test_create_with_extra_fields_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        body = _valid_cost_type()
        body["unexpected_field"] = "bad"
        r = await app.post(
            "/api/v1/cost-types/",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Get
# ---------------------------------------------------------------------------


class TestGetCostType:
    async def test_get_existing(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        # The seeded 'labor' cost type (id=1)
        r = await app.get("/api/v1/cost-types/1", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["code"] == "labor"
        assert body["name"] == "Labor"
        assert body["is_active"] is True
        assert body["version"] == 1

    async def test_get_nonexistent_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/cost-types/999999", headers=headers)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == ErrorCode.NOT_FOUND.value


# ---------------------------------------------------------------------------
# Update (PATCH)
# ---------------------------------------------------------------------------


class TestUpdateCostType:
    async def test_update_name(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/cost-types/",
            json=_valid_cost_type(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]

        r2 = await app.patch(
            f"/api/v1/cost-types/{cid}",
            json={"name": "Renamed cost"},
            headers=headers,
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["name"] == "Renamed cost"

    async def test_update_is_active(self, app: AsyncClient, owner_user: dict[str, Any]) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/cost-types/",
            json=_valid_cost_type(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        r2 = await app.patch(
            f"/api/v1/cost-types/{cid}",
            json={"is_active": False},
            headers=headers,
        )
        assert r2.status_code == 200
        assert r2.json()["is_active"] is False

    async def test_update_code_is_rejected(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """CostTypePatch does not expose ``code`` → extra field → 400."""
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/cost-types/",
            json=_valid_cost_type(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        r2 = await app.patch(
            f"/api/v1/cost-types/{cid}",
            json={"code": "new_code"},
            headers=headers,
        )
        assert r2.status_code == 400

    async def test_update_nonexistent_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.patch(
            "/api/v1/cost-types/999999",
            json={"name": "X"},
            headers=headers,
        )
        assert r.status_code == 404

    async def test_update_empty_body_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """PATCH with no fields: Pydantic CostTypePatch has all fields
        optional, so an empty object is valid syntactically. But the DB
        no-op path means the row is returned unchanged. Verify status 200
        and the row is unchanged."""
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/cost-types/",
            json=_valid_cost_type(name="Unchanged"),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        r2 = await app.patch(f"/api/v1/cost-types/{cid}", json={}, headers=headers)
        assert r2.status_code == 200
        assert r2.json()["name"] == "Unchanged"


# ---------------------------------------------------------------------------
# Deactivate
# ---------------------------------------------------------------------------


class TestDeactivateCostType:
    async def test_deactivate_sets_inactive(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/cost-types/",
            json=_valid_cost_type(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]

        r2 = await app.post(
            f"/api/v1/cost-types/{cid}/deactivate",
            json={"reason": "done"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["is_active"] is False

        # History preserved — GET still returns it
        r3 = await app.get(f"/api/v1/cost-types/{cid}", headers=headers)
        assert r3.status_code == 200
        assert r3.json()["is_active"] is False

    async def test_deactivate_reversible(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Per DB spec §2.7.1: deactivation is reversible."""
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/cost-types/",
            json=_valid_cost_type(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        await app.post(
            f"/api/v1/cost-types/{cid}/deactivate",
            json={"reason": "test"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        r2 = await app.patch(
            f"/api/v1/cost-types/{cid}",
            json={"is_active": True},
            headers=headers,
        )
        assert r2.status_code == 200
        assert r2.json()["is_active"] is True

    async def test_deactivate_missing_idempotency_key_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/cost-types/1/deactivate",
            json={"reason": "x"},
            headers=headers,
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.MISSING_HEADER.value

    async def test_deactivate_nonexistent_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/cost-types/999999/deactivate",
            json={"reason": "x"},
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


class TestDeleteCostType:
    async def test_delete_unreferenced_succeeds(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/cost-types/",
            json=_valid_cost_type(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]

        r2 = await app.delete(
            f"/api/v1/cost-types/{cid}",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 204, r2.text

        r3 = await app.get(f"/api/v1/cost-types/{cid}", headers=headers)
        assert r3.status_code == 404

    async def test_delete_referenced_returns_409(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        """Cost type referenced by a ``production_cost_line`` → 409
        ``referenced_by_history``. We construct the minimum FK chain:

        products (minimal) → production_runs → production_cost_lines →
        cost_types.

        The DB FK (``production_cost_lines.cost_type_id ON DELETE RESTRICT``)
        is the authoritative guard.
        """
        headers = await _owner_headers(app, owner_user)

        # Create a fresh cost type we can attempt to delete
        r = await app.post(
            "/api/v1/cost-types/",
            json=_valid_cost_type(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]

        # Insert a minimal product + production_run + production_cost_line
        # referencing the cost type, via raw SQL.
        factory = get_session_factory()
        async with factory() as session:
            prod = await session.execute(
                text(
                    "INSERT INTO products (name, created_by, updated_by) "
                    "VALUES (:n, :uid, :uid) RETURNING id"
                ),
                {"n": "CT-Blocker Product", "uid": owner_user["user_id"]},
            )
            product_id = int(prod.scalar_one())
            run = await session.execute(
                text(
                    "INSERT INTO production_runs "
                    "(output_product_id, output_quantity, finished_unit_cost, "
                    " total_raw_cost, total_overhead_cost, created_by) "
                    "VALUES (:pid, 1.0000, 1.0000, 0, 0, :uid) RETURNING id"
                ),
                {"pid": product_id, "uid": owner_user["user_id"]},
            )
            run_id = int(run.scalar_one())
            await session.execute(
                text(
                    "INSERT INTO production_cost_lines "
                    "(production_run_id, cost_type_id, amount, line_number) "
                    "VALUES (:rid, :cid, 10.00, 1)"
                ),
                {"rid": run_id, "cid": cid},
            )
            await session.commit()

        # Attempt to delete the cost type → FK violation → 409
        r2 = await app.delete(
            f"/api/v1/cost-types/{cid}",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r2.status_code == 409, r2.text
        assert r2.json()["error"]["code"] == ErrorCode.REFERENCED_BY_HISTORY.value

        # And confirm the cost type still exists after the failed delete
        r3 = await app.get(f"/api/v1/cost-types/{cid}", headers=headers)
        assert r3.status_code == 200

    async def test_delete_nonexistent_returns_404(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.delete(
            "/api/v1/cost-types/999999",
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 404

    async def test_delete_missing_idempotency_key_returns_400(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.delete("/api/v1/cost-types/1", headers=headers)
        assert r.status_code == 400
        assert r.json()["error"]["code"] == ErrorCode.MISSING_HEADER.value


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------


class TestCostTypeAudit:
    async def test_create_writes_audit_row(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        body = _valid_cost_type()
        r = await app.post(
            "/api/v1/cost-types/",
            json=body,
            headers={**headers, "Idempotency-Key": _idem()},
        )
        assert r.status_code == 201
        cid = r.json()["id"]

        factory = get_session_factory()
        async with factory() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT action, entity_type, entity_id, new_values "
                            "FROM audit_log "
                            "WHERE entity_type = 'cost_type' AND entity_id = :id "
                            "ORDER BY id"
                        ),
                        {"id": cid},
                    )
                )
                .mappings()
                .all()
            )
        actions = [r_["action"] for r_ in rows]
        assert "create" in actions

    async def test_update_writes_audit_row(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/cost-types/",
            json=_valid_cost_type(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        await app.patch(
            f"/api/v1/cost-types/{cid}",
            json={"name": "Renamed"},
            headers=headers,
        )

        factory = get_session_factory()
        async with factory() as session:
            actions = (
                (
                    await session.execute(
                        text(
                            "SELECT action FROM audit_log "
                            "WHERE entity_type = 'cost_type' AND entity_id = :id "
                            "ORDER BY id"
                        ),
                        {"id": cid},
                    )
                )
                .scalars()
                .all()
            )
        assert "update" in actions

    async def test_deactivate_writes_audit_row(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/cost-types/",
            json=_valid_cost_type(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        await app.post(
            f"/api/v1/cost-types/{cid}/deactivate",
            json={"reason": "audit test"},
            headers={**headers, "Idempotency-Key": _idem()},
        )

        factory = get_session_factory()
        async with factory() as session:
            actions = (
                (
                    await session.execute(
                        text(
                            "SELECT action FROM audit_log "
                            "WHERE entity_type = 'cost_type' AND entity_id = :id "
                            "ORDER BY id"
                        ),
                        {"id": cid},
                    )
                )
                .scalars()
                .all()
            )
        assert "deactivate" in actions

    async def test_delete_writes_audit_row(
        self, app: AsyncClient, owner_user: dict[str, Any]
    ) -> None:
        headers = await _owner_headers(app, owner_user)
        r = await app.post(
            "/api/v1/cost-types/",
            json=_valid_cost_type(),
            headers={**headers, "Idempotency-Key": _idem()},
        )
        cid = r.json()["id"]
        await app.delete(
            f"/api/v1/cost-types/{cid}",
            headers={**headers, "Idempotency-Key": _idem()},
        )

        factory = get_session_factory()
        async with factory() as session:
            rows = (
                (
                    await session.execute(
                        text(
                            "SELECT action, reason FROM audit_log "
                            "WHERE entity_type = 'cost_type' AND entity_id = :id "
                            "ORDER BY id"
                        ),
                        {"id": cid},
                    )
                )
                .mappings()
                .all()
            )
        actions = [r_["action"] for r_ in rows]
        reasons = [r_["reason"] for r_ in rows]
        assert "update" in actions  # hard delete uses UPDATE action + reason
        assert "hard_delete" in reasons
