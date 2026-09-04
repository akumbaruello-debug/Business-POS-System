"""Manual finance entry service (E.8).

Business logic for the four E.8 endpoints:

* ``listManualEntries``  — paginated list with filters.
* ``getManualEntry``     — single row by id.
* ``createManualEntry``  — create (posted) row + cash_movement.
* ``cancelManualEntry``  — lifecycle flip + reversing cash_movement.

Per ``Backend-Architecture-V1.0.md`` §12.5 + §15.16:

* Manual entries are cash-based only (no stock effects).
* Each create inserts a paired ``cash_movements`` row in the same
  transaction. Cancel inserts a reversing cash_movement (opposite
  direction, same amount).
* The DB trigger ``fn_cash_movements_balance_check`` enforces INV-03
  (Cash >= 0) on every cash_movement insert; we pre-check so we can
  return a clean ``insufficient_cash`` instead of letting the trigger
  raise a raw check_violation.
* Capability: create requires ``manual_entry.create_income`` OR
  ``manual_entry.create_expense`` (oneOf). List/get require
  ``manual_entry.view``. Cancel requires ``manual_entry.cancel``.
* Idempotency-Key required on create and cancel.
* If-Match required on cancel: the canonical ETag for
  ``manual_finance_entries`` is computed via
  ``etag_and_version_from_content`` since the row has no ``updated_at``
  column (``Backend-Architecture §12`` + ``concurrency/master_etag.py``).
  The ETag is returned in the response body (``etag``) and the ``ETag``
  response header so clients can issue ``If-Match`` on cancel.
* Audit: create → action ``create``; cancel → action ``cancel``.

Schema fix: ``schema.sql`` §18.21 registered
``trg_mfe_bump_version`` against ``fn_bump_version_and_updated_at``,
which references a non-existent ``updated_at`` column on
``manual_finance_entries`` (the table only has ``version``). That made
every UPDATE fail with ``record "new" has no field "updated_at"``,
blocking cancellation. Per the E.8 directive, this is a required fix
(E.8 cancel is a lifecycle UPDATE) and is corrected to
``fn_bump_version_only``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.audit.service import AuditContext, write_audit
from app.concurrency.etag import check_if_match
from app.concurrency.master_etag import etag_and_version_from_content
from app.db import UnitOfWork
from app.errors import (
    BusinessRuleViolation,
    Conflict,
    InsufficientCash,
    NotFound,
)
from app.manual_entry.repo import (
    count_manual_entries,
    get_manual_entry,
    insert_cash_movement_for_manual,
    insert_manual_entry,
    list_manual_entry_rows,
    update_manual_entry_cancel,
)
from app.services.idempotency import (
    IdempotencyStore,
    compute_request_fingerprint,
)
from app.validation.enums import AuditAction

__all__ = ["ManualEntryService"]

ENTITY_MANUAL_ENTRIES = "manual_entry"


def _etag_from_row(row: dict[str, Any]) -> str:
    """Derive the canonical ETag for a manual_finance_entries row.

    The table has no ``updated_at`` column (only ``version``), so per
    ``Backend-Architecture §12`` + ``concurrency/master_etag.py`` we use
    ``etag_and_version_from_content`` over the row's identity + mutable
    fields + version.
    """
    etag, _v = etag_and_version_from_content(
        row["id"],
        row["entry_date"],
        row["amount"],
        row["payment_method_id"],
        row["notes"],
        row["lifecycle_status"],
        row["cancellation_date"],
        row["cancellation_reason"],
        row["cancelled_by"],
        row["version"],
    )
    return etag


class ManualEntryService:
    __slots__ = ("_uow",)

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    # ---------------------------------------------------------------------------
    # List
    # ---------------------------------------------------------------------------

    async def list_manual_entries(
        self,
        *,
        page: int,
        per_page: int,
        sort: str,
        entry_type: str | None,
        category_id: int | None,
        lifecycle_status: str | None,
        from_iso: str | None,
        to_iso: str | None,
        q: str | None,
    ) -> dict[str, Any]:
        total = await count_manual_entries(
            self._uow,
            entry_type=entry_type,
            category_id=category_id,
            lifecycle_status=lifecycle_status,
            from_iso=from_iso,
            to_iso=to_iso,
            q=q,
        )
        rows = await list_manual_entry_rows(
            self._uow,
            page=page,
            per_page=per_page,
            sort=sort,
            entry_type=entry_type,
            category_id=category_id,
            lifecycle_status=lifecycle_status,
            from_iso=from_iso,
            to_iso=to_iso,
            q=q,
        )
        return {"data": rows, "total": total}

    # ---------------------------------------------------------------------------
    # Get
    # ---------------------------------------------------------------------------

    async def get_manual_entry(self, *, entry_id: int) -> dict[str, Any] | None:
        row = await get_manual_entry(self._uow, entry_id=entry_id)
        if row is None:
            return None
        row["etag"] = _etag_from_row(row)
        return row

    # ---------------------------------------------------------------------------
    # Create
    # ---------------------------------------------------------------------------

    async def create_manual_entry(
        self,
        *,
        principal_user_id: int,
        category_id: int,
        entry_type: str,
        amount: Decimal,
        payment_method_id: int,
        entry_date: datetime | None,
        notes: str | None,
        idempotency_key: str | None,
        ctx: AuditContext | None,
        request_body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        """createManualEntry — POST /manual-entries.

        Implements Backend-Architecture §15.16 + Event 21/22 of the
        accounting event matrix. All business mutations + audit +
        idempotency share one COMMIT; any failure rolls back the entire
        transaction.

        Pre-conditions (raised as 4xx with canonical error code):

        * category exists and is active (else 404 ``not_found``)
        * category.entry_type matches the request entry_type
          (else 400 ``manual_entry_duplicate_of_derived``)
        * payment_method exists and is active (else 404 ``not_found``)
        * amount > 0 (enforced by Pydantic on the request schema)
        * Cash balance >= amount for expense (INV-03).

        Effects:

        * Insert one ``manual_finance_entries`` row with
          ``lifecycle_status='posted'``.
        * Insert one ``cash_movements`` row with
          ``trigger='manual_income'`` or ``'manual_expense'``,
          ``direction='in'`` or ``'out'``, and ``amount`` signed
          accordingly (income → positive amount, direction='in';
          expense → negative amount, direction='out').
        * ``reference_type='manual_entry'``, ``reference_id`` = the new
          entry's id.
        * Audit action ``create`` on ``manual_entry`` entity.

        Idempotency mirrors E.3/E.4: replay returns the cached body
        with ``Idempotent-Replay: true``; key+body mismatch → 409
        ``idempotency_violation``.

        Returns the canonical ``ManualEntry`` contract response,
        enriched with the derived ``etag``.
        """
        # ----- idempotency replay -------------------------------------------
        idem_rec = None
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="POST",
                path="/manual-entries",
                body=request_body,
            )
            idem_rec = await store.start(
                key=idempotency_key,
                user_id=principal_user_id or 0,
                endpoint="POST /manual-entries",
                fingerprint=fingerprint,
            )
            if idem_rec is not None and idem_rec.is_completed:
                await self._uow.commit()
                cached = idem_rec.response_body
                if isinstance(cached, str):
                    try:
                        cached = json.loads(cached)
                    except (ValueError, TypeError):
                        cached = {}
                return dict(cached) if isinstance(cached, dict) else {}, True

        # ----- validate category exists + active + entry_type matches ---
        cat = await self._uow.first_row(
            "SELECT id, entry_type, is_active FROM financial_categories WHERE id = :id",
            {"id": int(category_id)},
        )
        if cat is None:
            raise NotFound(f"Financial category {category_id} not found.")
        if not bool(cat["is_active"]):
            raise BusinessRuleViolation(
                "Financial category is deactivated.",
                details={"code": "business_rule_violation", "category_id": int(category_id)},
            )
        if str(cat["entry_type"]) != entry_type:
            from app.errors import ManualEntryDuplicateOfDerived

            raise ManualEntryDuplicateOfDerived(
                "Category entry_type does not match the requested entry_type.",
                details={
                    "category_id": int(category_id),
                    "category_type": str(cat["entry_type"]),
                    "requested_type": entry_type,
                },
            )

        # ----- validate payment_method exists + active ---------------------
        pm = await self._uow.first_row(
            "SELECT id, is_active FROM payment_methods WHERE id = :id",
            {"id": int(payment_method_id)},
        )
        if pm is None:
            raise NotFound(f"Payment method {payment_method_id} not found.")
        if not bool(pm["is_active"]):
            raise BusinessRuleViolation(
                "Payment method is deactivated.",
                details={"code": "business_rule_violation", "payment_method_id": int(payment_method_id)},
            )

        # ----- cash balance pre-check for expense --------------------------
        if entry_type == "expense":
            # INV-03: Cash >= 0. For expense we pre-check so we can return
            # a clean ``insufficient_cash`` instead of letting the DB
            # trigger raise a raw check_violation.
            row = await self._uow.first_row(
                "SELECT COALESCE(SUM(amount), 0) AS b FROM cash_movements"
            )
            current_cash = Decimal(str((row or {"b": 0})["b"]))
            if current_cash < amount:
                raise InsufficientCash(
                    "Insufficient cash balance for manual expense.",
                    details={"current_cash": float(current_cash), "required": float(amount)},
                )

        # ----- resolve entry_date -------------------------------------------
        now = datetime.now(tz=UTC)
        if entry_date is None:
            entry_date = now

        # ----- insert manual_finance_entries -------------------------------
        entry = await insert_manual_entry(
            self._uow,
            category_id=category_id,
            amount=amount,
            payment_method_id=payment_method_id,
            entry_date=entry_date,
            notes=notes,
            created_by=principal_user_id,
        )
        if entry is None:
            raise Conflict("Failed to insert manual entry.")
        entry_id = int(entry["id"])

        # ----- insert cash_movements ----------------------------------------
        if entry_type == "income":
            cash_amount = amount
            direction = "in"
            trigger = "manual_income"
        else:  # expense
            cash_amount = -amount
            direction = "out"
            trigger = "manual_expense"

        await insert_cash_movement_for_manual(
            self._uow,
            amount=cash_amount,
            direction=direction,
            trigger=trigger,
            payment_method_id=payment_method_id,
            reference_id=entry_id,
            created_by=principal_user_id,
        )

        # ----- audit (AuditAction.CREATE) --------------------------------
        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.CREATE,
                entity_type=ENTITY_MANUAL_ENTRIES,
                entity_id=entry_id,
                old_values=None,
                new_values={
                    "category_id": int(entry["category_id"]),
                    "entry_type": str(entry["entry_type"]),
                    "amount": float(amount),
                    "payment_method_id": int(entry["payment_method_id"]),
                    "entry_date": entry["entry_date"].isoformat() if isinstance(entry["entry_date"], datetime) else str(entry["entry_date"]),
                    "notes": str(entry["notes"]) if entry["notes"] else None,
                    "lifecycle_status": str(entry["lifecycle_status"]),
                },
                ctx=ctx,
            )

        # ----- shape response --------------------------------------------
        result = dict(entry)
        result["etag"] = _etag_from_row(result)

        if idempotency_key is not None and idem_rec is not None:
            store = IdempotencyStore(self._uow)
            await store.complete(
                record_id=idem_rec.id,
                status=201,
                body=json.dumps(result, default=str, sort_keys=True),
                user_id=principal_user_id,
            )

        return result, False

    # ---------------------------------------------------------------------------
    # Cancel
    # ---------------------------------------------------------------------------

    async def cancel_manual_entry(
        self,
        *,
        entry_id: int,
        principal_user_id: int,
        reason: str,
        if_match: str | None,
        idempotency_key: str | None,
        ctx: AuditContext | None,
        request_body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        """cancelManualEntry — POST /manual-entries/{id}/cancel.

        Implements Backend-Architecture §15.16: cancellation creates a
        reversing cash_movement (opposite direction, same amount). The
        original entry's lifecycle_status flips to 'cancelled'.

        Pre-conditions:

        * entry exists and is 'posted' (else 404 or 409).
        * If-Match matches the entry's current ETag.

        Effects:

        * Update ``manual_finance_entries``: lifecycle_status →
          'cancelled', cancellation_date, cancellation_reason,
          cancelled_by.
        * Insert one ``cash_movements`` row with opposite direction
          and same magnitude as the original entry's cash movement.
        * Audit action ``cancel`` on ``manual_entry`` entity.

        Idempotency mirrors E.3/E.4: replay returns the cached body
        with ``Idempotent-Replay: true``; key+body mismatch → 409
        ``idempotency_violation``.

        Returns the enriched ``ManualEntry`` contract response, with an
        updated ``etag``.
        """
        # ----- idempotency replay (before ETag check) ----------------------
        # Idempotency replay happens first (matches production_cancel
        # behaviour — idempotent retry returns cached result regardless of
        # ETag freshness, since the key+body already authenticated intent).
        idem_rec = None
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="POST",
                path=f"/manual-entries/{entry_id}/cancel",
                body=request_body,
            )
            idem_rec = await store.start(
                key=idempotency_key,
                user_id=principal_user_id or 0,
                endpoint="POST /manual-entries/{id}/cancel",
                fingerprint=fingerprint,
            )
            if idem_rec is not None and idem_rec.is_completed:
                await self._uow.commit()
                cached = idem_rec.response_body
                if isinstance(cached, str):
                    try:
                        cached = json.loads(cached)
                    except (ValueError, TypeError):
                        cached = {}
                return dict(cached) if isinstance(cached, dict) else {}, True

        # ----- fetch current entry + ETag check -----------------------------
        current = await get_manual_entry(self._uow, entry_id=entry_id)
        if current is None:
            raise NotFound(f"Manual entry {entry_id} not found.")
        if str(current["lifecycle_status"]) != "posted":
            raise Conflict(
                "Manual entry is not in a cancellable state.",
                details={
                    "code": "lifecycle_state_invalid",
                    "current_status": str(current["lifecycle_status"]),
                },
            )

        # If-Match: content-derived ETag (manual_finance_entries has no
        # updated_at column; ``etag_and_version_from_content`` is the
        # project helper for this case).
        current_etag = _etag_from_row(current)
        check_if_match(provided=if_match, current_etag=current_etag)

        # ----- read the original cash_movement to reverse --------------------
        orig_cm = await self._uow.first_row(
            """
            SELECT id, amount, direction, trigger, payment_method_id
            FROM cash_movements
            WHERE reference_type = 'manual_entry' AND reference_id = :rid
            """,
            {"rid": int(entry_id)},
        )
        if orig_cm is None:
            raise Conflict("Original cash movement for manual entry not found.")

        # ----- insert reversing cash_movement --------------------------------
        orig_amount = Decimal(str(orig_cm["amount"]))
        orig_direction = str(orig_cm["direction"])
        reverse_direction = "in" if orig_direction == "out" else "out"
        reverse_amount = -orig_amount
        reverse_trigger = (
            "manual_income" if orig_direction == "out" else "manual_expense"
        )
        await insert_cash_movement_for_manual(
            self._uow,
            amount=reverse_amount,
            direction=reverse_direction,
            trigger=reverse_trigger,
            payment_method_id=int(orig_cm["payment_method_id"]),
            reference_id=entry_id,
            created_by=principal_user_id,
        )

        # ----- lifecycle: -> cancelled ---------------------------------------
        cancellation_date = datetime.now(tz=UTC)
        cancelled = await update_manual_entry_cancel(
            self._uow,
            entry_id=entry_id,
            cancellation_date=cancellation_date,
            cancellation_reason=reason,
            cancelled_by=principal_user_id,
        )
        if cancelled is None:
            raise Conflict("Failed to cancel manual entry.")

        # ----- audit (AuditAction.CANCEL) --------------------------------
        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.CANCEL,
                entity_type=ENTITY_MANUAL_ENTRIES,
                entity_id=entry_id,
                old_values={
                    "lifecycle_status": "posted",
                    "version": int(current.get("version", 0)),
                },
                new_values={
                    "lifecycle_status": "cancelled",
                    "cancellation_date": cancellation_date.isoformat(),
                    "cancellation_reason": reason,
                    "cancelled_by": principal_user_id,
                },
                reason=reason,
                ctx=ctx,
            )

        # ----- shape response -----------------------------------------------
        result = dict(cancelled)
        result["etag"] = _etag_from_row(result)

        if idempotency_key is not None and idem_rec is not None:
            store = IdempotencyStore(self._uow)
            await store.complete(
                record_id=idem_rec.id,
                status=200,
                body=json.dumps(result, default=str, sort_keys=True),
                user_id=principal_user_id,
            )

        return result, False
