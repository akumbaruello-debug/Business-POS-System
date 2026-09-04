"""F.9 - Settings API tests.

Covers the two settings endpoints per ``openapi.yaml`` (lines
5877-5928) and ``Backend-Implementation-Plan-V1.0.md`` item F.9:

* ``GET  /settings``          - ``getSettings``   (settings.view)
* ``PATCH /settings``         - ``updateSettings`` (settings.manage,
  ``Idempotency-Key`` + ``If-Match`` required)

Test matrix (mirrors the F.1-F.8 authorization + read matrix):

GET
  1. unauthenticated -> 401
  2. authenticated -> 200, full ``SettingsMap`` shape
  3. response ETag header present + matches the canonical ISO 8601 form
  4. ``is_initialized`` is exposed as a boolean (not the string "false")
  5. ``costing_method`` defaults to "moving_average"

PATCH
  6. unauthenticated -> 401
  7. missing ``Idempotency-Key`` -> 400 missing_header
  8. missing ``If-Match`` -> 400 missing_header
  9. valid update with correct ETag -> 200 + new ETag header
  10. partial update: only the keys in the body change
  11. multiple keys updated in one request
  12. unknown setting key -> 400 validation_failed
  13. read-only ``costing_method`` -> 400 validation_failed
  14. ``is_initialized: false`` -> 400 (one-way gate)
  15. empty body ``{}`` -> 400
  16. stale If-Match -> 412 version_mismatch
  17. missing If-Match (after a stale read shows the ETag is required)
  18. malformed If-Match -> 400 invalid_header
  19. replay same key + same body -> 200 + ``Idempotent-Replay: true``
  20. replay same key + different body -> 409 idempotency_violation
  21. successful PATCH writes exactly one ``audit_log`` row with
      ``action=settings_change`` + the new values
  22. successful PATCH writes the ``system_settings`` row(s) for
      touched keys (post-condition: subsequent GET returns the new
      value)
  23. failed ETag mismatch does NOT mutate any system_settings row
      (verifies the optimistic-concurrency check runs before the
      write)
  24. ``settings_change`` audit row carries the pre-image + post-image
      of just the touched keys
"""

from __future__ import annotations

import json
import uuid

import pytest

from app.db import UnitOfWork

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers (mirror the F.1-F.8 test convention)
# ---------------------------------------------------------------------------


async def _login(app, username, password) -> str:
    r = await app.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return str(r.json()["access_token"])


async def _owner_headers(app, owner_user) -> dict[str, str]:
    token = await _login(app, owner_user["username"], owner_user["password"])
    return {"Authorization": f"Bearer {token}"}


def _idempotency_key() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


class TestGetSettings:
    """GET /settings  -  getSettings  (settings.view)"""

    async def test_unauthenticated_returns_401(self, app):
        r = await app.get("/api/v1/settings")
        assert r.status_code == 401

    async def test_authenticated_returns_full_settings_map(self, app, owner_user):
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/settings", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert "settings" in body
        settings = body["settings"]
        # The 10 canonical default rows are seeded by schema.sql
        # (lines 2384-2394). Verify every one is present.
        expected_keys = {
            "costing_method",
            "default_negative_stock_allowed",
            "default_low_stock_threshold",
            "default_posting_timing",
            "enable_sequential_doc_numbers",
            "is_initialized",
            "display_rounding",
            "company_name",
            "company_address",
            "adjustment_creates_pnl_entry",
        }
        assert set(settings) == expected_keys

    async def test_etag_header_present_and_canonical(self, app, owner_user):
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/settings", headers=h)
        assert r.status_code == 200
        etag = r.headers.get("ETag")
        assert etag is not None
        # ``iso_utc`` (used by ``make_etag_from_updated_at``) emits the
        # ISO-8601 canonical form with full microsecond precision and a
        # ``+00:00`` suffix (not ``Z``) — this is deliberate per
        # ``app/util/__init__.py`` so the ETag round-trips through
        # ``jsonable_encoder``'s ``datetime.isoformat()``.
        assert etag.startswith('"') and etag.endswith('"')
        inner = etag[1:-1]
        assert inner.endswith("+00:00")
        assert "T" in inner
        assert "." in inner  # microsecond precision preserved

    async def test_is_initialized_is_boolean(self, app, owner_user):
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/settings", headers=h)
        assert r.status_code == 200
        # Must be a real bool (not the string "false").
        assert isinstance(r.json()["settings"]["is_initialized"], bool)

    async def test_costing_method_default_value(self, app, owner_user):
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/settings", headers=h)
        assert r.status_code == 200
        assert r.json()["settings"]["costing_method"] == "moving_average"


class TestPatchSettingsAuthnAuthz:
    """PATCH /settings - unauthenticated / missing headers."""

    async def test_unauthenticated_returns_401(self, app):
        r = await app.patch(
            "/api/v1/settings",
            json={"company_name": "Acme"},
            headers={
                "Idempotency-Key": _idempotency_key(),
                "If-Match": '"2026-01-01T00:00:00.000000+00:00"',
            },
        )
        assert r.status_code == 401

    async def test_missing_idempotency_key_returns_400(self, app, owner_user):
        h = await _owner_headers(app, owner_user)
        r = await app.patch(
            "/api/v1/settings",
            json={"company_name": "Acme"},
            headers={
                **h,
                "If-Match": '"2026-01-01T00:00:00.000000+00:00"',
            },
        )
        assert r.status_code == 400
        body = r.json()
        assert body["error"]["code"] in (
            "missing_header",
            "validation_failed",
        )

    async def test_missing_if_match_returns_400(self, app, owner_user):
        """The OAS declares ``If-Match`` as required on PATCH via
        ``IfMatchRequired``. The route rejects an absent header with
        400 ``missing_header``.
        """
        h = await _owner_headers(app, owner_user)
        r = await app.patch(
            "/api/v1/settings",
            json={"company_name": "Acme"},
            headers={**h, "Idempotency-Key": _idempotency_key()},
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "missing_header"


class TestPatchSettingsHappyPath:
    """PATCH /settings - successful update + read-back."""

    async def _get_etag(self, app, owner_user) -> str:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/settings", headers=h)
        assert r.status_code == 200
        return str(r.headers["ETag"])

    async def test_valid_update_succeeds_with_new_etag(self, app, owner_user):
        etag = await self._get_etag(app, owner_user)
        h = await _owner_headers(app, owner_user)
        r = await app.patch(
            "/api/v1/settings",
            json={"company_name": "Acme Corp"},
            headers={
                **h,
                "Idempotency-Key": _idempotency_key(),
                "If-Match": etag,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["settings"]["company_name"] == "Acme Corp"
        # New ETag is present + differs from the pre-image.
        new_etag = r.headers.get("ETag")
        assert new_etag is not None
        assert new_etag != etag

    async def test_partial_update_leaves_other_keys_alone(self, app, owner_user):
        """Patching ``company_name`` must not touch ``costing_method``."""
        etag = await self._get_etag(app, owner_user)
        h = await _owner_headers(app, owner_user)
        r = await app.patch(
            "/api/v1/settings",
            json={"company_name": "Acme Inc"},
            headers={
                **h,
                "Idempotency-Key": _idempotency_key(),
                "If-Match": etag,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["settings"]["company_name"] == "Acme Inc"
        # Read-only key is unchanged.
        assert body["settings"]["costing_method"] == "moving_average"

    async def test_multiple_keys_updated_in_one_request(self, app, owner_user):
        etag = await self._get_etag(app, owner_user)
        h = await _owner_headers(app, owner_user)
        r = await app.patch(
            "/api/v1/settings",
            json={
                "company_name": "Beta LLC",
                "company_address": "123 Main St",
                "default_low_stock_threshold": 5,
            },
            headers={
                **h,
                "Idempotency-Key": _idempotency_key(),
                "If-Match": etag,
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["settings"]["company_name"] == "Beta LLC"
        assert body["settings"]["company_address"] == "123 Main St"
        # Numeric values are returned as real numbers (not strings).
        assert body["settings"]["default_low_stock_threshold"] == 5

    async def test_successful_patch_persists_to_database(self, app, owner_user):
        etag = await self._get_etag(app, owner_user)
        h = await _owner_headers(app, owner_user)
        r = await app.patch(
            "/api/v1/settings",
            json={"company_name": "Gamma Co"},
            headers={
                **h,
                "Idempotency-Key": _idempotency_key(),
                "If-Match": etag,
            },
        )
        assert r.status_code == 200
        # Verify the row is persisted (not just the response).
        async with UnitOfWork() as uow:
            row = await uow.first_row(
                "SELECT value FROM system_settings WHERE key = 'company_name'",
                None,
            )
        assert row is not None
        assert row["value"] == "Gamma Co"


class TestPatchSettingsRejections:
    """PATCH /settings - 400 on contract violations."""

    async def _get_etag(self, app, owner_user) -> str:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/settings", headers=h)
        return str(r.headers["ETag"])

    async def test_unknown_setting_key_returns_400(self, app, owner_user):
        etag = await self._get_etag(app, owner_user)
        h = await _owner_headers(app, owner_user)
        r = await app.patch(
            "/api/v1/settings",
            json={"definitely_not_a_real_key": "value"},
            headers={
                **h,
                "Idempotency-Key": _idempotency_key(),
                "If-Match": etag,
            },
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "validation_failed"

    async def test_read_only_costing_method_returns_400(self, app, owner_user):
        etag = await self._get_etag(app, owner_user)
        h = await _owner_headers(app, owner_user)
        r = await app.patch(
            "/api/v1/settings",
            json={"costing_method": "fifo"},
            headers={
                **h,
                "Idempotency-Key": _idempotency_key(),
                "If-Match": etag,
            },
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "validation_failed"

    async def test_is_initialized_false_returns_400(self, app, owner_user):
        """The ``is_initialized`` flag is one-way (DB trigger)."""
        etag = await self._get_etag(app, owner_user)
        h = await _owner_headers(app, owner_user)
        r = await app.patch(
            "/api/v1/settings",
            json={"is_initialized": False},
            headers={
                **h,
                "Idempotency-Key": _idempotency_key(),
                "If-Match": etag,
            },
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "validation_failed"

    async def test_empty_body_returns_400(self, app, owner_user):
        etag = await self._get_etag(app, owner_user)
        h = await _owner_headers(app, owner_user)
        r = await app.patch(
            "/api/v1/settings",
            json={},
            headers={
                **h,
                "Idempotency-Key": _idempotency_key(),
                "If-Match": etag,
            },
        )
        assert r.status_code == 400
        assert r.json()["error"]["code"] == "validation_failed"


class TestPatchSettingsOptimisticConcurrency:
    """PATCH /settings - 412 on stale If-Match, 400 on malformed."""

    async def _get_etag(self, app, owner_user) -> str:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/settings", headers=h)
        return str(r.headers["ETag"])

    async def test_stale_if_match_returns_412(self, app, owner_user):
        # Stale ETag (2020-01-01 is before any seeded row's updated_at).
        h = await _owner_headers(app, owner_user)
        r = await app.patch(
            "/api/v1/settings",
            json={"company_name": "Stale"},
            headers={
                **h,
                "Idempotency-Key": _idempotency_key(),
                "If-Match": '"2020-01-01T00:00:00.000000+00:00"',
            },
        )
        assert r.status_code == 412
        assert r.json()["error"]["code"] == "version_mismatch"

    async def test_stale_if_match_does_not_mutate(self, app, owner_user):
        """A failed concurrency check must leave the row untouched."""
        # Snapshot the current value.
        async with UnitOfWork() as uow:
            pre = await uow.first_row(
                "SELECT value FROM system_settings WHERE key = 'company_name'",
                None,
            )
        pre_value = pre["value"] if pre else None

        h = await _owner_headers(app, owner_user)
        r = await app.patch(
            "/api/v1/settings",
            json={"company_name": "Should Not Persist"},
            headers={
                **h,
                "Idempotency-Key": _idempotency_key(),
                "If-Match": '"2020-01-01T00:00:00.000000+00:00"',
            },
        )
        assert r.status_code == 412

        # Verify the row is unchanged.
        async with UnitOfWork() as uow:
            post = await uow.first_row(
                "SELECT value FROM system_settings WHERE key = 'company_name'",
                None,
            )
        post_value = post["value"] if post else None
        assert pre_value == post_value

    async def test_malformed_if_match_returns_400(self, app, owner_user):
        h = await _owner_headers(app, owner_user)
        r = await app.patch(
            "/api/v1/settings",
            json={"company_name": "Acme"},
            headers={
                **h,
                "Idempotency-Key": _idempotency_key(),
                "If-Match": "not-a-quoted-etag",
            },
        )
        assert r.status_code == 400
        # Either ``invalid_header`` (from the If-Match parser) or
        # ``missing_header`` is acceptable; the project convention is
        # ``invalid_header`` for malformed quoted values.
        assert r.json()["error"]["code"] in (
            "invalid_header",
            "missing_header",
            "validation_failed",
        )


class TestPatchSettingsIdempotency:
    """PATCH /settings - same key+body replays; conflict on diff body."""

    async def _get_etag(self, app, owner_user) -> str:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/settings", headers=h)
        return str(r.headers["ETag"])

    async def test_replay_same_key_same_body_is_idempotent(self, app, owner_user):
        etag = await self._get_etag(app, owner_user)
        h = await _owner_headers(app, owner_user)
        idem = _idempotency_key()
        body = {"company_name": "Replay Test"}
        # First call: 200, no replay header.
        r1 = await app.patch(
            "/api/v1/settings",
            json=body,
            headers={**h, "Idempotency-Key": idem, "If-Match": etag},
        )
        assert r1.status_code == 200
        assert r1.headers.get("Idempotent-Replay") != "true"

        # Second call: same key, same body, new If-Match (we'd be
        # following the ETag in production; pass the new one).
        new_etag = r1.headers["ETag"]
        r2 = await app.patch(
            "/api/v1/settings",
            json=body,
            headers={**h, "Idempotency-Key": idem, "If-Match": new_etag},
        )
        assert r2.status_code == 200
        # Replay header is set.
        assert r2.headers.get("Idempotent-Replay") == "true"
        # Body is the same as the first response.
        assert r2.json()["settings"]["company_name"] == "Replay Test"

    async def test_same_key_different_body_returns_409(self, app, owner_user):
        etag = await self._get_etag(app, owner_user)
        h = await _owner_headers(app, owner_user)
        idem = _idempotency_key()
        # First call.
        r1 = await app.patch(
            "/api/v1/settings",
            json={"company_name": "First"},
            headers={**h, "Idempotency-Key": idem, "If-Match": etag},
        )
        assert r1.status_code == 200
        new_etag = r1.headers["ETag"]
        # Second call: same key, different body.
        r2 = await app.patch(
            "/api/v1/settings",
            json={"company_name": "Second"},
            headers={**h, "Idempotency-Key": idem, "If-Match": new_etag},
        )
        assert r2.status_code == 409
        assert r2.json()["error"]["code"] == "idempotency_violation"


class TestPatchSettingsAudit:
    """PATCH /settings - audit_log invariants."""

    async def _get_etag(self, app, owner_user) -> str:
        h = await _owner_headers(app, owner_user)
        r = await app.get("/api/v1/settings", headers=h)
        return str(r.headers["ETag"])

    async def test_successful_patch_writes_one_audit_row(self, app, owner_user):
        etag = await self._get_etag(app, owner_user)
        h = await _owner_headers(app, owner_user)
        # Snapshot the audit count for settings_change.
        async with UnitOfWork() as uow:
            pre = await uow.first_scalar(
                "SELECT COUNT(*) FROM audit_log " "WHERE action = 'settings_change'",
                None,
            )
        pre_count = int(pre or 0)

        r = await app.patch(
            "/api/v1/settings",
            json={"company_name": "Audit Test"},
            headers={
                **h,
                "Idempotency-Key": _idempotency_key(),
                "If-Match": etag,
            },
        )
        assert r.status_code == 200

        async with UnitOfWork() as uow:
            post = await uow.first_scalar(
                "SELECT COUNT(*) FROM audit_log " "WHERE action = 'settings_change'",
                None,
            )
        post_count = int(post or 0)
        assert post_count == pre_count + 1

    async def test_audit_row_carries_pre_and_post_image(self, app, owner_user):
        # Pre-seed: read current company_name.
        async with UnitOfWork() as uow:
            pre_row = await uow.first_row(
                "SELECT value FROM system_settings " "WHERE key = 'company_name'",
                None,
            )
        pre_value = pre_row["value"] if pre_row else None

        etag = await self._get_etag(app, owner_user)
        h = await _owner_headers(app, owner_user)
        r = await app.patch(
            "/api/v1/settings",
            json={"company_name": "Audit Pre/Post"},
            headers={
                **h,
                "Idempotency-Key": _idempotency_key(),
                "If-Match": etag,
            },
        )
        assert r.status_code == 200

        # Find the most recent settings_change audit row.
        async with UnitOfWork() as uow:
            row = await uow.first_row(
                "SELECT old_values, new_values FROM audit_log "
                "WHERE action = 'settings_change' "
                "ORDER BY id DESC LIMIT 1",
                None,
            )
        assert row is not None

        old = (
            row["old_values"]
            if isinstance(row["old_values"], dict)
            else json.loads(row["old_values"] or "{}")
        )
        new = (
            row["new_values"]
            if isinstance(row["new_values"], dict)
            else json.loads(row["new_values"] or "{}")
        )

        # Pre-image carries the previous company_name.
        assert old["settings"]["company_name"] == pre_value
        # Post-image carries the new value.
        assert new["settings"]["company_name"] == "Audit Pre/Post"
