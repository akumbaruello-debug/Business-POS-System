"""Service layer for the ``contacts`` table.

Business rules per DB spec §2.5.3 + API §Contacts + Backend-Architecture §12:

* ``type`` is a closed enum: ``customer`` / ``supplier`` / ``both``.
* ``email`` validation enforced at the DB CHECK level + Pydantic schema.
* Name + type uniqueness via ``ux_contacts_name`` → 409 Conflict.
* ``is_active`` soft-delete (deactivate) is reversible and always allowed;
  no ``referenced_by_history`` check (contacts are referenced by sales/
  purchases but the FK is ON DELETE RESTRICT — hard-delete is blocked
  by the DB, surfaced as 409).
* ``version``/ETag derived from ``updated_at`` via
  ``etag_and_version_from_updated_at`` (no DB ``version`` column).
* PATCH ``If-Match`` is REQUIRED per OpenAPI — stale ETag → 412.
* Idempotency-Key required on POST / DELETE / deactivate (per schema.sql
  idempotency_keys table + OpenAPI).
* All mutations write audit_log.
"""

from __future__ import annotations

import builtins
from typing import Any

from app.audit.service import ENTITY_CONTACTS, AuditContext, write_audit
from app.concurrency.etag import check_if_match
from app.concurrency.master_etag import etag_and_version_from_updated_at
from app.db import UnitOfWork
from app.errors import Conflict, NotFound, ReferencedByHistory
from app.repositories.contacts import ContactRepository
from app.services.idempotency import IdempotencyStore, compute_request_fingerprint
from app.validation.enums import AuditAction

# PostgreSQL unique_violation and foreign_key_violation error codes.
_PG_UNIQUE_VIOLATION = "23505"
_PG_FK_VIOLATION = "23503"


def _is_unique_violation(exc: Exception) -> bool:
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate is not None:
        return str(sqlstate) == _PG_UNIQUE_VIOLATION
    return "duplicate key value" in str(exc).lower()


def _is_fk_violation(exc: Exception) -> bool:
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate is not None:
        return str(sqlstate) == _PG_FK_VIOLATION
    msg = str(exc).lower()
    return "violates foreign key constraint" in msg


def _version_from_row(row: dict[str, Any]) -> tuple[str, int]:
    """Derive (etag, version) from the row's updated_at."""
    if row is None:
        raise NotFound("Contact not found.")
    return etag_and_version_from_updated_at(row["updated_at"])


class ContactService:
    """Business logic for contacts (customers + suppliers)."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow
        self._repo = ContactRepository(uow)

    # ------------------------------------------------------------------
    # List / Get
    # ------------------------------------------------------------------
    async def list(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
        q: str | None = None,
        contact_type: str | None = None,
        active_only: bool | None = None,
        sort: str = "id",
    ) -> builtins.tuple[list[dict[str, Any]], int, list[tuple[str, int]]]:
        """List contacts with pagination + derived (etag, version) per row."""
        rows, total = await self._repo.list(
            page=page, per_page=per_page, q=q,
            contact_type=contact_type, active_only=active_only, sort=sort,
        )
        versions = [
            etag_and_version_from_updated_at(r["updated_at"]) for r in rows
        ]
        return rows, total, versions

    async def get(self, id: int) -> dict[str, Any]:
        """Get a single contact, or raise NotFound."""
        row = await self._repo.get(id)
        if row is None:
            raise NotFound(f"Contact {id} not found.")
        return row

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------
    async def create(
        self,
        *,
        contact_type: str,
        name: str,
        phone: str | None = None,
        email: str | None = None,
        address: str | None = None,
        notes: str | None = None,
        is_active: bool = True,
        created_by: int | None,
        idempotency_key: str | None = None,
        request_body: dict[str, Any] | None = None,
        ctx: AuditContext | None = None,
    ) -> dict[str, Any]:
        """Create a new contact.

        Enforces:
        * Name+type uniqueness → 409 Conflict.
        * Idempotency: when a key is supplied, a fingerprint is recorded.
          Deduplication / replay of completed rows is the route layer's job.
        """
        # Idempotency fingerprint as a side-effect.
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="POST", path="/contacts", body=request_body
            )
            await store.start(
                key=idempotency_key,
                user_id=ctx.user_id if ctx and ctx.user_id is not None else 0,
                endpoint="POST /contacts",
                fingerprint=fingerprint,
            )

        try:
            row = await self._repo.create(
                type=contact_type,
                name=name,
                phone=phone,
                email=email,
                address=address,
                notes=notes,
                is_active=is_active,
                created_by=created_by,
            )
        except Exception as exc:
            if _is_unique_violation(exc):
                raise Conflict(
                    "A contact with this name and type already exists.",
                    details={"field": "name", "value_type": "conflict"},
                ) from exc
            raise

        if row is None:
            raise Conflict(
                "Contact could not be created.",
                details={"field": "name", "value_type": "conflict"},
            )

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.CREATE,
                entity_type=ENTITY_CONTACTS,
                entity_id=int(row["id"]),
                new_values={k: v for k, v in row.items()},
                ctx=ctx,
            )

        await self._uow.commit()
        return row

    # ------------------------------------------------------------------
    # Update (PATCH)
    # ------------------------------------------------------------------
    async def update(
        self,
        id: int,
        *,
        type: str | None = None,
        name: str | None = None,
        phone: str | None = None,
        email: str | None = None,
        address: str | None = None,
        notes: str | None = None,
        is_active: bool | None = None,
        if_match: str | None = None,
        ctx: AuditContext | None = None,
        created_by: int | None = None,
    ) -> dict[str, Any]:
        """Update a contact. If-Match is REQUIRED (OpenAPI IfMatchRequired).

        * If-Match mismatch → 412 VersionMismatch.
        * Name+type uniqueness on rename → 409.
        """
        existing = await self._repo.get(id)
        if existing is None:
            raise NotFound(f"Contact {id} not found.")

        etag, _ = _version_from_row(existing)
        check_if_match(provided=if_match, current_etag=etag)

        try:
            result = await self._repo.update(
                id,
                type=type,
                name=name,
                phone=phone,
                email=email,
                address=address,
                notes=notes,
                is_active=is_active,
            )
        except Exception as exc:
            if _is_unique_violation(exc):
                raise Conflict(
                    "A contact with this name and type already exists.",
                    details={"field": "name", "value_type": "conflict"},
                ) from exc
            raise

        if result is None:
            raise NotFound(f"Contact {id} not found after update.")

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.UPDATE,
                entity_type=ENTITY_CONTACTS,
                entity_id=id,
                old_values=dict(existing),
                new_values={k: v for k, v in result.items()},
                ctx=ctx,
            )

        await self._uow.commit()
        return result

    # ------------------------------------------------------------------
    # Deactivate
    # ------------------------------------------------------------------
    async def deactivate(
        self,
        id: int,
        *,
        reason: str | None = None,
        idempotency_key: str | None = None,
        ctx: AuditContext | None = None,
    ) -> dict[str, Any]:
        """Set is_active=FALSE. Always allowed (preserves history).

        Per OpenAPI: Idempotency-Key required, reason optional.
        """
        existing = await self._repo.get(id)
        if existing is None:
            raise NotFound(f"Contact {id} not found.")

        row = await self._repo.deactivate(id)

        if ctx is not None and row is not None:
            await write_audit(
                self._uow,
                action=AuditAction.DEACTIVATE,
                entity_type=ENTITY_CONTACTS,
                entity_id=id,
                old_values=dict(existing),
                new_values={k: v for k, v in row.items()},
                reason=reason,
                ctx=ctx,
            )

        await self._uow.commit()
        assert row is not None
        return row

    # ------------------------------------------------------------------
    # Hard Delete
    # ------------------------------------------------------------------
    async def delete(
        self,
        id: int,
        *,
        idempotency_key: str | None = None,
        ctx: AuditContext | None = None,
    ) -> None:
        """Hard-delete a contact.

        Blocked by:
        * Not found → 404.
        * Referenced by sales/purchases (FK ON DELETE RESTRICT) → 409
          referenced_by_history.
        """
        existing = await self._repo.get(id)
        if existing is None:
            raise NotFound(f"Contact {id} not found.")

        # Idempotency fingerprint.
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="DELETE", path=f"/contacts/{id}", body=b""
            )
            await store.start(
                key=idempotency_key,
                user_id=ctx.user_id if ctx and ctx.user_id is not None else 0,
                endpoint=f"DELETE /contacts/{id}",
                fingerprint=fingerprint,
            )

        try:
            deleted = await self._repo.hard_delete(id)
        except Exception as exc:
            if _is_fk_violation(exc):
                raise ReferencedByHistory(
                    "Contact is referenced by transactions.",
                    details={"resource": "contact", "id": id},
                ) from exc
            raise

        if not deleted:
            raise NotFound(f"Contact {id} not found after delete.")

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.UPDATE,  # 'delete' not in ck_audit_action; use 'update' (matches categories pattern)
                entity_type=ENTITY_CONTACTS,
                entity_id=id,
                old_values=dict(existing),
                new_values=None,
                ctx=ctx,
            )

        await self._uow.commit()


__all__ = ["ContactService"]
