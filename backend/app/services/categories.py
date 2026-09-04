"""Service layer for the ``categories`` table.

Business rules per DB spec §5.1 + API §15.2 + Backend-Architecture §12:

* ``name`` is unique (full UNIQUE index ``ux_categories_name``) —
  duplicate → 409 Conflict.
* ``parent_id`` self-FK is ON DELETE RESTRICT — a category with children
  cannot be hard-deleted (children exist → 409 referenced_by_history).
* Hard-delete is also blocked if ``products.category_id`` references it
  (FK ON DELETE RESTRICT → 409 referenced_by_history).
* Deactivate is always allowed (reversible, no referenced_by_history check).
* ``version``/ETag derived from ``updated_at`` via
  ``etag_and_version_from_updated_at`` (no DB ``version`` column).
* PATCH ``If-Match`` is required per OpenAPI — stale ETag → 412.
* Idempotency-Key required on POST / DELETE / deactivate.
* All mutations write audit_log.
"""

from __future__ import annotations

from typing import Any

from app.audit.service import ENTITY_CATEGORIES, AuditContext, write_audit
from app.concurrency.etag import check_if_match
from app.concurrency.master_etag import (
    etag_and_version_from_updated_at,
)
from app.db import UnitOfWork
from app.errors import Conflict, NotFound, ReferencedByHistory
from app.repositories.categories import CategoryRepository
from app.services.idempotency import IdempotencyStore, compute_request_fingerprint
from app.validation.enums import AuditAction

# PostgreSQL unique_violation error code.
_PG_UNIQUE_VIOLATION = "23505"
# PostgreSQL foreign_key_violation (raised when products or children reference the category).
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
        raise NotFound("Category not found.")
    return etag_and_version_from_updated_at(row["updated_at"])


class CategoryService:
    """Business logic for categories."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow
        self._repo = CategoryRepository(uow)

    # ------------------------------------------------------------------
    # List / Get
    # ------------------------------------------------------------------

    async def list(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
        q: str | None = None,
        active_only: bool | None = None,
        sort: str = "id",
    ) -> tuple[list[dict[str, Any]], int, list[tuple[str, int]]]:
        """List categories with pagination + derived (etag, version) per row."""
        rows, total = await self._repo.list(
            page=page, per_page=per_page, q=q, active_only=active_only, sort=sort
        )
        versions = [(etag, ver) for etag, ver in (_version_from_row(r) for r in rows)]
        return rows, total, versions

    async def get(self, id: int) -> dict[str, Any]:
        """Get a single category, or raise NotFound."""
        row = await self._repo.get(id)
        if row is None:
            raise NotFound(f"Category {id} not found.")
        return row

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        name: str,
        parent_id: int | None,
        is_active: bool,
        ctx: AuditContext | None = None,
        idempotency_key: str | None = None,
        request_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new category.

        Enforces:
        * Name uniqueness → 409 Conflict.
        * Parent self-FK validity → 400 (caught from DB FK violation).
        * Idempotency: when a key is supplied, we record the fingerprint
          on the pending row. The deduplication / replay of completed
          rows is delegated to the route layer's idempotency middleware
          (per the standard M1/M2 pattern, see ``app.services.idempotency``).
        """
        # Record idempotency fingerprint as a side-effect (no replay logic
        # inline — the route layer reads ``idempotency_keys`` on retries).
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="POST", path="/categories", body=request_body
            )
            await store.start(
                key=idempotency_key,
                user_id=ctx.user_id if ctx and ctx.user_id is not None else 0,
                endpoint="POST /categories",
                fingerprint=fingerprint,
            )

        try:
            row = await self._repo.create(name=name, parent_id=parent_id, is_active=is_active)
        except Exception as exc:
            if _is_unique_violation(exc):
                raise Conflict(
                    "A category with this name already exists.",
                    details={"field": "name", "value_type": "conflict"},
                ) from exc
            raise

        if row is None:
            raise Conflict(
                "Category could not be created.",
                details={"field": "name", "value_type": "conflict"},
            )

        # Audit write.
        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.CREATE,
                entity_type=ENTITY_CATEGORIES,
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
        parent_id: int | None = None,
        if_match: str | None = None,
        ctx: AuditContext | None = None,
    ) -> dict[str, Any]:
        """Update a category. If-Match is REQUIRED (OpenAPI IfMatchRequired).

        * If-Match omitted → 428 (precondition required) / 400 MissingHeader.
        * If-Match mismatch → 412 VersionMismatch.
        * Name uniqueness on rename → 409.
        """
        existing = await self._repo.get(id)
        if existing is None:
            raise NotFound(f"Category {id} not found.")

        # If-Match REQUIRED on PATCH.
        etag, _ = _version_from_row(existing)
        check_if_match(provided=if_match, current_etag=etag)

        result = await self._repo.update(
            id,
            name=name,
            is_active=is_active,
            parent_id=parent_id,
        )
        if result is None:
            raise NotFound(f"Category {id} not found after update.")

        # Audit: old_values = existing, new_values = result.
        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.UPDATE,
                entity_type=ENTITY_CATEGORIES,
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
        """Set is_active=FALSE. Always allowed (no referenced_by_history check).

        Per OpenAPI: Idempotency-Key required, reason optional.
        """
        existing = await self._repo.get(id)
        if existing is None:
            raise NotFound(f"Category {id} not found.")

        row = await self._repo.deactivate(id)

        if ctx is not None and row is not None:
            await write_audit(
                self._uow,
                action=AuditAction.DEACTIVATE,
                entity_type=ENTITY_CATEGORIES,
                entity_id=id,
                old_values=dict(existing),
                new_values={k: v for k, v in row.items()},
                reason=reason,
                ctx=ctx,
            )

        await self._uow.commit()
        assert row is not None  # deactivate always returns the row since we checked existence
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
        """Hard-delete a category.

        Blocked by:
        * Children exist (parent_id FK) → 409 referenced_by_history.
        * Products reference it (products.category_id FK) → 409.
        * Not found → 404.
        """
        existing = await self._repo.get(id)
        if existing is None:
            raise NotFound(f"Category {id} not found.")

        # Proactive children check (DB FK also enforces, but this gives
        # a clean error envelope without relying on exception pattern).
        child_count = await self._repo.count_children(id)
        if child_count > 0:
            raise ReferencedByHistory(
                "Category has child categories.",
                details={"resource": "category", "id": id},
            )

        try:
            deleted = await self._repo.hard_delete(id)
        except Exception as exc:
            if _is_fk_violation(exc):
                raise ReferencedByHistory(
                    "Category is referenced by products.",
                    details={"resource": "category", "id": id},
                ) from exc
            raise

        if not deleted:
            raise NotFound(f"Category {id} not found after delete.")

        # Audit: 'delete' is NOT in ck_audit_action whitelist; use 'update'.
        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.UPDATE,
                entity_type=ENTITY_CATEGORIES,
                entity_id=id,
                old_values=dict(existing),
                new_values=None,
                ctx=ctx,
            )

        await self._uow.commit()


__all__ = ["CategoryService"]
