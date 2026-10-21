"""Service layer for the ``product_attributes`` table.

V3 Phase 2 — managed master values for Jenis / Size / Kualitas.

Business rules (mirrors the categories/units master-data convention):

* ``(kind, name)`` is unique (``ux_product_attributes_kind_name``) —
  duplicate within the same dimension → 409 Conflict. The same name is
  allowed under different kinds (a Size named "A" and a Kualitas named
  "A" are different things — semantics stay distinct per dimension).
* ``kind`` is immutable via PATCH. Changing it would silently move an
  attribute (possibly already referenced by products) to another
  dimension; the CHECK set + patch schema both close it.
* Hard-delete is blocked if any product references the attribute via
  ``jenis_id / size_id / kualitas_id`` (FK ON DELETE RESTRICT → 23503 →
  409 referenced_by_history). ``count_products_referencing`` gives the
  same result app-side for a clean error envelope.
* Deactivate is always allowed (reversible, references preserved).
* ``version``/ETag derived from ``updated_at`` via
  ``etag_and_version_from_updated_at`` (no DB ``version`` column).
* PATCH ``If-Match`` is REQUIRED per the master-data convention — stale
  ETag → 412.
* Idempotency-Key required on POST / DELETE / deactivate.
* All mutations write audit_log (entity_type="product_attribute").
* No seeds — the Owner creates the values; nothing is invented.
"""

from __future__ import annotations

from typing import Any

from app.audit.service import ENTITY_PRODUCT_ATTRIBUTES, AuditContext, write_audit
from app.concurrency.etag import check_if_match
from app.concurrency.master_etag import (
    etag_and_version_from_updated_at,
)
from app.db import UnitOfWork
from app.errors import Conflict, NotFound, ReferencedByHistory
from app.repositories.product_attributes import ProductAttributeRepository
from app.services.idempotency import IdempotencyStore, compute_request_fingerprint
from app.validation.enums import AuditAction

# PostgreSQL unique_violation error code.
_PG_UNIQUE_VIOLATION = "23505"
# PostgreSQL foreign_key_violation (raised when products reference the attribute).
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
        raise NotFound("Product attribute not found.")
    return etag_and_version_from_updated_at(row["updated_at"])


class ProductAttributeService:
    """Business logic for product attributes."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow
        self._repo = ProductAttributeRepository(uow)
        self._endpoint = "/product-attributes"

    # ------------------------------------------------------------------
    # List / Get
    # ------------------------------------------------------------------

    async def list(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
        kind: str | None = None,
        q: str | None = None,
        active_only: bool | None = None,
        sort: str = "id",
    ) -> tuple[list[dict[str, Any]], int, list[tuple[str, int]]]:
        """List attributes with pagination + derived (etag, version) per row."""
        rows, total = await self._repo.list(
            page=page,
            per_page=per_page,
            kind=kind,
            q=q,
            active_only=active_only,
            sort=sort,
        )
        versions = [(etag, ver) for etag, ver in (_version_from_row(r) for r in rows)]
        return rows, total, versions

    async def get(self, id: int) -> dict[str, Any]:
        """Get a single attribute, or raise NotFound."""
        row = await self._repo.get(id)
        if row is None:
            raise NotFound(f"Product attribute {id} not found.")
        return row

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        kind: str,
        name: str,
        is_active: bool,
        ctx: AuditContext | None = None,
        idempotency_key: str | None = None,
        request_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new attribute value.

        Enforces:
        * ``(kind, name)`` uniqueness → 409 Conflict.
        * Idempotency: when a key is supplied, we record the fingerprint
          on the pending row (dedup/replay delegated to the route layer's
          idempotency middleware — standard M1/M2 pattern).
        """
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="POST", path=self._endpoint, body=request_body
            )
            await store.start(
                key=idempotency_key,
                user_id=ctx.user_id if ctx and ctx.user_id is not None else 0,
                endpoint=f"POST {self._endpoint}",
                fingerprint=fingerprint,
            )

        try:
            row = await self._repo.create(kind=kind, name=name, is_active=is_active)
        except Exception as exc:
            if _is_unique_violation(exc):
                raise Conflict(
                    f"A {kind} with this name already exists.",
                    details={"field": "name", "kind": kind, "value_type": "conflict"},
                ) from exc
            raise

        if row is None:
            raise Conflict(
                f"{kind} attribute could not be created.",
                details={"field": "name", "value_type": "conflict"},
            )

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.CREATE,
                entity_type=ENTITY_PRODUCT_ATTRIBUTES,
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
        name: str | None = None,
        is_active: bool | None = None,
        if_match: str | None = None,
        ctx: AuditContext | None = None,
    ) -> dict[str, Any]:
        """Update an attribute. If-Match is REQUIRED.

        * If-Match omitted → 428 (precondition required) / 400 MissingHeader.
        * If-Match mismatch → 412 VersionMismatch.
        * ``(kind, name)`` uniqueness on rename → 409.
        * ``kind`` is immutable (not accepted by PATCH).
        """
        existing = await self._repo.get(id)
        if existing is None:
            raise NotFound(f"Product attribute {id} not found.")

        etag, _ = _version_from_row(existing)
        check_if_match(provided=if_match, current_etag=etag)

        try:
            result = await self._repo.update(
                id,
                name=name,
                is_active=is_active,
            )
        except Exception as exc:
            if _is_unique_violation(exc):
                raise Conflict(
                    "A product attribute with this name already exists in this dimension.",
                    details={"field": "name", "value_type": "conflict"},
                ) from exc
            raise
        if result is None:
            raise NotFound(f"Product attribute {id} not found after update.")

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.UPDATE,
                entity_type=ENTITY_PRODUCT_ATTRIBUTES,
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
        """Set is_active=FALSE. Always allowed (no referenced_by_history check)."""
        existing = await self._repo.get(id)
        if existing is None:
            raise NotFound(f"Product attribute {id} not found.")

        row = await self._repo.deactivate(id)

        if ctx is not None and row is not None:
            await write_audit(
                self._uow,
                action=AuditAction.DEACTIVATE,
                entity_type=ENTITY_PRODUCT_ATTRIBUTES,
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
        """Hard-delete an attribute.

        Blocked by:
        * Products reference it (products.jenis_id / size_id / kualitas_id FK)
          → 409 referenced_by_history.
        * Not found → 404.
        """
        existing = await self._repo.get(id)
        if existing is None:
            raise NotFound(f"Product attribute {id} not found.")

        # Proactive check (DB FK also enforces — this gives a clean error
        # envelope without relying on exception pattern).
        referencing = await self._repo.count_products_referencing(id)
        if referencing > 0:
            raise ReferencedByHistory(
                f"Product attribute is referenced by {referencing} product(s).",
                details={"resource": "product_attribute", "id": id},
            )

        try:
            deleted = await self._repo.hard_delete(id)
        except Exception as exc:
            if _is_fk_violation(exc):
                raise ReferencedByHistory(
                    "Product attribute is referenced by products.",
                    details={"resource": "product_attribute", "id": id},
                ) from exc
            raise

        if not deleted:
            raise NotFound(f"Product attribute {id} not found after delete.")

        # Audit: 'delete' is NOT in ck_audit_action whitelist; use 'update'.
        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.UPDATE,
                entity_type=ENTITY_PRODUCT_ATTRIBUTES,
                entity_id=id,
                old_values=dict(existing),
                new_values=None,
                ctx=ctx,
            )

        await self._uow.commit()


__all__ = ["ProductAttributeService"]
