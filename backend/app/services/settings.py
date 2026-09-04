r"""F.9 - Settings API service.

Backs the two system settings endpoints in ``openapi.yaml`` (lines
5877-5928):

* ``GET  /settings``  - returns the full ``SettingsMap`` + the canonical
  ETag (the quoted ISO-8601 ``MAX(updated_at)`` across all rows).
* ``PATCH /settings`` - applies a partial update; requires
  ``Idempotency-Key`` + ``If-Match``.

Architecture
------------
    route  ->  SettingsService  ->  UnitOfWork  ->  system_settings
                                   \->  IdempotencyStore
                                   \->  audit_log

The service is intentionally thin: the locked schema has no
``settings_view`` / ``settings_manage`` columns; the only business
rules are the read-only ``costing_method`` key and the DB trigger
``fn_system_settings_readonly_keys()`` (which enforces the same plus
the ``is_initialized`` one-way gate).

ETag strategy
-------------
``system_settings`` is treated as a **single logical resource** for
optimistic concurrency. The canonical ETag is the quoted ISO-8601
``MAX(updated_at)`` across all rows. Any PATCH refreshes every
touched row's ``updated_at`` (via the
``trg_system_settings_touch_updated`` BEFORE UPDATE trigger,
``schema.sql:2227``) and the next read returns the new MAX, so a
follow-up PATCH that doesn't read between writes will correctly 412.
This matches the project's per-resource ETag convention used by
categories / financial_categories / products (single-row ETag = that
row's ``updated_at``); here the "resource" is the whole settings map.

Idempotency
-----------
``IdempotencyStore.start`` records the request fingerprint; the
``complete`` call writes the response cache atomically with the audit
row. A second PATCH with the same key + same body returns the cached
body (200 with ``Idempotent-Replay: true``); same key + different
body returns 409 ``idempotency_violation`` (the route catches the raw
``IdempotencyViolationConflict`` and re-raises as
:class:`IdempotencyViolation` per the project convention).

Audit
-----
Each PATCH writes ONE ``audit_log`` row with ``action=settings_change``
(the only legal action for this entity type per the
``ck_audit_action`` CHECK at ``schema.sql:1488-1492``). ``new_values``
carries the merged update (the post-image of just the touched keys);
``old_values`` carries the pre-image of just the touched keys.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from app.audit.service import AuditContext, write_audit
from app.concurrency.etag import check_if_match, make_etag_from_updated_at
from app.db import UnitOfWork
from app.errors import IdempotencyViolation, ValidationFailed
from app.services.idempotency import (
    IdempotencyStore,
    compute_request_fingerprint,
)
from app.validation.enums import AuditAction
from app.validation.settings_schemas import READONLY_SETTING_KEYS

ENTITY_SYSTEM_SETTINGS = "system_settings"

# Pre-computed JSON encoder for non-JSON value rows. ``value_type='json'``
# rows store the value as a JSON-encoded string; all other value types
# store the raw scalar as a string (``'true'``/``'false'``/``'42'``/text).
_JSON_SENTINEL = "json"


class SettingsService:
    """Service for F.9 GET / PATCH /settings.

    State is held in the ``system_settings`` table; the service
    never caches reads (the F.9 plan prose says the boot-time cache
    is wired in M5; the route layer reads on every request).
    """

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    # ------------------------------------------------------------------ #
    # Read
    # ------------------------------------------------------------------ #

    async def get_settings(self) -> tuple[dict[str, Any], str]:
        """Read all settings + the canonical ETag.

        Returns ``(rows, etag)`` where ``rows`` is a
        ``key -> deserialised value`` dict (booleans are real bools,
        numbers are real int/float, JSON values are real dict/list,
        string values are real str) and ``etag`` is the quoted
        ISO-8601 ``MAX(updated_at)``.
        """
        raw = await self._uow.fetch_all(
            """
            SELECT key, value, value_type, updated_at
            FROM system_settings
            """,
            None,
        )
        rows = {str(r["key"]): self._decode_value(r) for r in raw}
        max_updated = self._max_updated(raw)
        etag = make_etag_from_updated_at(max_updated)
        return rows, etag

    # ------------------------------------------------------------------ #
    # Write
    # ------------------------------------------------------------------ #

    async def patch_settings(
        self,
        *,
        patch: dict[str, Any],
        if_match: str | None,
        idempotency_key: str,
        principal_user_id: int,
        request_body: Any,
        ctx: AuditContext,
    ) -> tuple[dict[str, Any], str, bool]:
        """Apply a partial settings update.

        Args:
          patch: validated PATCH body (``key -> value``).
          if_match: the parsed ``If-Match`` header inner value, or
            ``None`` (the route must require it; this method does
            not enforce required-ness - the OpenAPI does).
          idempotency_key: the raw UUID from the ``Idempotency-Key``
            header. Required.
          principal_user_id: for the audit row + the idempotency
            record.
          request_body: the original request body (used for the
            idempotency fingerprint).
          ctx: the audit context.

        Returns:
          ``(rows, etag, is_replay)``: the full post-image ``rows``
          (all known settings), the new canonical ETag, and a bool
          indicating whether the response was served from the
          idempotency cache.

        Raises:
          VersionMismatch: 412 on ETag mismatch.
          InvalidPayload: 400 on read-only key, unknown key
            (shouldn't happen - Pydantic catches it), or any other
            contract violation.
        """
        # ----- idempotency replay (M4 pattern) -------------------------
        store = IdempotencyStore(self._uow)
        fingerprint = compute_request_fingerprint(
            method="PATCH",
            path="/settings",
            body=request_body,
        )
        try:
            idem_rec = await store.start(
                key=idempotency_key,
                user_id=int(principal_user_id),
                endpoint="PATCH /settings",
                fingerprint=fingerprint,
            )
        except Exception as exc:
            # ``IdempotencyStore.start`` raises
            # ``IdempotencyViolationConflict`` (a raw ``Exception``,
            # not an ``AppError``). Re-wrap as the project's 409
            # envelope per the convention documented in
            # ``app/services/idempotency.py`` line 130.
            from app.services.idempotency import IdempotencyViolationConflict

            if isinstance(exc, IdempotencyViolationConflict):
                raise IdempotencyViolation(
                    "Idempotency-Key has been used with a different request body.",
                ) from exc
            raise

        if idem_rec.is_completed:
            await self._uow.commit()
            cached = idem_rec.response_body
            if isinstance(cached, str):
                try:
                    cached = json.loads(cached)
                except (ValueError, TypeError):
                    cached = {}
            if not isinstance(cached, dict):
                cached = {}
            rows = cached.get("rows", {})
            etag = cached.get("etag", "")
            return rows, etag, True

        # ----- business-rule pre-validations ---------------------------
        if not patch:
            raise ValidationFailed("PATCH body must contain at least one setting key.")
        for key in patch:
            if key in READONLY_SETTING_KEYS:
                raise ValidationFailed(
                    f"Setting '{key}' is read-only and cannot be modified.",
                    errors=[{"field": key, "code": "setting_read_only"}],
                )
        # ``is_initialized`` one-way gate (DB trigger
        # ``fn_system_settings_readonly_keys``). We pre-check so the
        # envelope stays a clean 400 instead of a 23514 / 500.
        new_init = patch.get("is_initialized")
        if new_init is False:
            raise ValidationFailed(
                "Setting 'is_initialized' is one-way; " "it cannot be transitioned back to false.",
                errors=[{"field": "is_initialized", "code": "setting_read_only"}],
            )

        # ----- read current ETag (single logical resource) -------------
        current_rows, current_etag = await self.get_settings()
        # The route should have already validated the header, but the
        # service defends in depth: skip the check when ``if_match``
        # is None and let the route's MissingHeader carry the 400.
        if if_match is not None:
            check_if_match(provided=if_match, current_etag=current_etag)

        # ----- apply the update ----------------------------------------
        # We update the touched keys one at a time (the table is
        # keyed by ``key``; there is no other unique constraint we
        # could exploit). Each UPDATE re-runs
        # ``trg_system_settings_touch_updated`` and the readonly
        # trigger; a malformed value would fail here. The trigger
        # rejects ``costing_method`` updates with a 23514
        # ``check_violation``; we pre-validated against the read-only
        # set above so that case cannot reach the DB.
        pre_image: dict[str, Any] = {}
        for key, value in patch.items():
            pre_image[key] = current_rows.get(key)
            value_type, encoded = self._encode_value(value)
            await self._uow.execute(
                """
                UPDATE system_settings
                SET value = :value,
                    value_type = :value_type,
                    updated_by = :uid
                WHERE key = :key
                """,
                {
                    "value": encoded,
                    "value_type": value_type,
                    "uid": int(principal_user_id),
                    "key": key,
                },
            )

        # Re-read the post-image + new ETag.
        new_rows, new_etag = await self.get_settings()

        # ----- audit (AuditAction.SETTINGS_CHANGE) ---------------------
        await write_audit(
            self._uow,
            action=AuditAction.SETTINGS_CHANGE,
            entity_type=ENTITY_SYSTEM_SETTINGS,
            entity_id=None,  # system_settings has no single-row id
            old_values={"settings": pre_image},
            new_values={"settings": {k: new_rows.get(k) for k in patch}},
            reason="PATCH /settings",
            ctx=ctx,
        )

        # ----- finalise idempotency ------------------------------------
        await store.complete(
            record_id=idem_rec.id,
            status=200,
            body=json.dumps({"rows": new_rows, "etag": new_etag}),
            user_id=int(principal_user_id),
        )

        return new_rows, new_etag, False

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _decode_value(row: dict[str, Any]) -> Any:
        """Convert one ``system_settings`` row to its native Python value.

        The DB ``value`` column is ``TEXT``; ``value_type`` tells us
        how to interpret it (``string``/``number``/``boolean``/``json``).
        """
        raw = row["value"]
        vtype = row["value_type"]
        if vtype == "boolean":
            return raw == "true"
        if vtype == "number":
            try:
                if "." in str(raw):
                    return float(raw)
                return int(raw)
            except (TypeError, ValueError):
                return str(raw)
        if vtype == _JSON_SENTINEL:
            try:
                return json.loads(str(raw))
            except (ValueError, TypeError):
                return raw
        # ``string`` and unknown: pass through.
        return raw

    @staticmethod
    def _encode_value(value: Any) -> tuple[str, str]:
        """Convert a Pydantic-validated value to ``(value_type, encoded)``.

        The DB ``ck_system_settings_value_type`` CHECK allows only the
        four canonical tags. We pick the tag that round-trips the
        input losslessly:

        * ``bool`` -> ``boolean`` / ``"true"``|``"false"``
        * ``int``|``float`` -> ``number`` / ``str(value)``
        * ``dict``|``list`` -> ``json`` / ``json.dumps(value)``
        * ``str`` -> ``string`` / ``value``
        * ``None`` -> rejected by the Pydantic schema; this is
          defensive.
        """
        if isinstance(value, bool):
            return "boolean", "true" if value else "false"
        if isinstance(value, int | float):
            return "number", str(value)
        if isinstance(value, dict | list):
            return _JSON_SENTINEL, json.dumps(value, default=str)
        if value is None:
            return "string", ""
        return "string", str(value)

    @staticmethod
    def _max_updated(rows: list[dict[str, Any]]) -> datetime:
        """Return the max ``updated_at`` from a list of row dicts.

        ``asyncpg`` returns ``TIMESTAMPTZ`` as naive ``datetime``;
        normalise to UTC for the ETag builder. Falls back to
        ``NOW()`` if the table is empty (defensive; the schema
        seeds 10 rows so this is unreachable in practice).
        """
        if not rows:
            return datetime.now(tz=UTC)
        best: datetime | None = None
        for r in rows:
            ts = r["updated_at"]
            if isinstance(ts, datetime) and ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)
            if best is None or ts > best:
                best = ts
        return best if best is not None else datetime.now(tz=UTC)


__all__ = [
    "SettingsService",
    "ENTITY_SYSTEM_SETTINGS",
    "READONLY_SETTING_KEYS",
]
