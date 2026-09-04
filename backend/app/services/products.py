"""Service layer for the ``products`` table.

Business rules per DB spec §2.4 + API §15.7 + Backend-Architecture §12:

* ``code`` is optional, unique-when-present (``ux_products_code`` partial
  unique). Create with a duplicate code -> 409 Conflict.
* ``code`` is immutable via the public API (PATCH does not accept ``code``).
  Attempting to change it server-side is a 400 / ``validation_failed``.
* Price change (``purchase_price``/``selling_price`` modified on PATCH) writes
  a ``product_price_history`` row. Historical transactions keep their snapshot
  price (BR-PRODUCT-002). The DB has no trigger for this — the service layer
  owns it.
* Deactivate is always allowed (soft-delete, preserves history). Blocks
  new sales at the transaction layer (BR-PRODUCT-003).
* Hard delete blocked when referenced by any transaction/movement/payment/
  AR-AP (BR-PRODUCT-004) -> 409 ``referenced_by_history``. The DB FK
  constraints + runtime FK violation translation provide this.
* ``on_hand_quantity`` / ``moving_average_unit_cost`` / ``inventory_value``
  are derived from the ``product_valuation`` view + stock movements (not
  stored on the product row). The DB has no ``moving_average_unit_cost``
  column — we read it from the view.
* ``low_stock`` is a computed boolean:
  ``on_hand_quantity <= low_stock_threshold`` when threshold is not NULL,
  else FALSE.
* ``negative_stock_fallback_supported`` is derived from:
  ``product.allow_negative_stock`` OR ``system_settings.default_negative_stock_allowed``.
* ``If-Match`` is REQUIRED on PATCH (OpenAPI IfMatchRequired).
* ``Idempotency-Key`` is REQUIRED on POST / DELETE / deactivate.
* Version/ETag derived from ``updated_at`` via
  ``etag_and_version_from_updated_at`` (no DB ``version`` column on products).
* All mutations write ``audit_log``.

NOTE on idempotency: per the established M2 pattern (categories), the service
records an idempotency fingerprint on the pending key. Full replay/dedup of
completed responses is handled by the route layer's idempotency middleware.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.audit.service import ENTITY_PRODUCTS, AuditContext, write_audit
from app.concurrency.etag import check_if_match
from app.concurrency.master_etag import etag_and_version_from_updated_at
from app.db import UnitOfWork
from app.errors import Conflict, NotFound, ReferencedByHistory
from app.inventory.valuation import compute_moving_average_unit_cost, get_settings_bool
from app.repositories.products import ProductRepository
from app.services.idempotency import IdempotencyStore, compute_request_fingerprint
from app.validation.enums import AuditAction

# PostgreSQL unique_violation error code.
_PG_UNIQUE_VIOLATION = "23505"
# PostgreSQL foreign_key_violation (raised when sales/movements/etc reference the product).
_PG_FK_VIOLATION = "23503"

# Default low-stock threshold from system settings (fallback when product
# doesn't set its own threshold).
_DEFAULT_LOW_STOCK_THRESHOLD = 0


def _is_unique_violation(exc: Exception) -> bool:
    """True if the underlying asyncpg/psycopg exception is a unique violation."""
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate is not None:
        return str(sqlstate) == _PG_UNIQUE_VIOLATION
    return "duplicate key value" in str(exc).lower()


def _is_fk_violation(exc: Exception) -> bool:
    """True if the underlying exception is a FK violation."""
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate is not None:
        return str(sqlstate) == _PG_FK_VIOLATION
    msg = str(exc).lower()
    return "violates foreign key constraint" in msg


def _version_from_row(row: dict[str, Any]) -> tuple[str, int]:
    """Derive (etag, version) from a product row's updated_at."""
    if row is None:
        raise NotFound("Product not found.")
    return etag_and_version_from_updated_at(row["updated_at"])


def _parse_dt(value: str) -> datetime:
    """Parse an ISO 8601 date-time string into a ``datetime``.

    The B.7 ``from`` / ``to`` query params arrive as ISO 8601 strings;
    asyncpg expects a ``datetime`` instance for ``TIMESTAMPTZ`` comparison.
    Accepts the ``Z`` (Zulu) suffix as UTC.
    """
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    return datetime.fromisoformat(v)


class ProductService:
    """Business logic for products."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow
        self._repo = ProductRepository(uow)

    # ------------------------------------------------------------------
    # List / Get
    # ------------------------------------------------------------------

    async def list_products(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
        q: str | None = None,
        active_only: bool | None = None,
        sort: str = "id",
        category_id: int | None = None,
        unit_id: int | None = None,
    ) -> tuple[list[dict[str, Any]], int, list[tuple[str, int]]]:
        """List products with pagination + derived (etag, version) per row."""
        rows, total = await self._repo.list(
            page=page,
            per_page=per_page,
            q=q,
            active_only=active_only,
            sort=sort,
            category_id=category_id,
            unit_id=unit_id,
        )
        versions = [(etag, ver) for etag, ver in (_version_from_row(r) for r in rows)]
        return rows, total, versions

    async def get(self, id: int) -> dict[str, Any]:
        """Get a single product, or raise NotFound."""
        row = await self._repo.get(id)
        if row is None:
            raise NotFound(f"Product {id} not found.")
        return row

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        name: str,
        code: str | None,
        category_id: int | None = None,
        unit_id: int | None = None,
        purchase_price: float = 0,
        selling_price: float = 0,
        low_stock_threshold: float | None = None,
        allow_negative_stock: bool | None = None,
        notes: str | None = None,
        is_sellable: bool = True,
        is_purchasable: bool = True,
        is_producible: bool = False,
        is_active: bool = True,
        ctx: AuditContext | None = None,
        idempotency_key: str | None = None,
        request_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new product.

        Enforces:
        * Code uniqueness (when present) -> 409 Conflict.
        * Idempotency: when a key is supplied, records fingerprint.
        * ``created_by``/``updated_by`` are NOT NULL on the products table
          (FK to users). The route layer always provides an authenticated
          principal — we pass `ctx.user_id` through.
        """

        # If-Match is not required on create, but idempotency is.
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="POST", path="/products", body=request_body
            )
            await store.start(
                key=idempotency_key,
                user_id=ctx.user_id if ctx and ctx.user_id is not None else 0,
                endpoint="POST /products",
                fingerprint=fingerprint,
            )

        # Check uniqueness of code (if provided).
        if code is not None:
            existing = await self._repo.get_by_code(code)
            if existing is not None:
                raise Conflict(
                    f"Product with code '{code}' already exists.",
                    details={"field": "code", "value": code},
                )

        # Validate FK references exist (DB FK is ON DELETE RESTRICT — an
        # invalid (non-existent) category/unit would raise a FK violation
        # that we translate to 409, but the semantically correct code for
        # a missing parent is 404, so we check app-side first).
        if category_id is not None:
            parent = await self._uow.first_row(
                "SELECT id FROM categories WHERE id = :id",
                {"id": category_id},
            )
            if parent is None:
                raise NotFound(f"Category {category_id} not found.")
        if unit_id is not None:
            parent_u = await self._uow.first_row(
                "SELECT id FROM units WHERE id = :id",
                {"id": unit_id},
            )
            if parent_u is None:
                raise NotFound(f"Unit {unit_id} not found.")

        # All product routes require an authenticated principal (capability-gated),
        # so ctx.user_id is always set. Defensive: fall back to 0 if missing
        # (the DB FK will catch a 0-id lookup; tests that hit this branch
        # indicate a route-layer change).
        actor = ctx.user_id if ctx and ctx.user_id is not None else 0

        try:
            row = await self._repo.create(
                name=name,
                code=code,
                category_id=category_id,
                unit_id=unit_id,
                purchase_price=purchase_price,
                selling_price=selling_price,
                low_stock_threshold=low_stock_threshold,
                allow_negative_stock=allow_negative_stock,
                notes=notes,
                is_sellable=is_sellable,
                is_purchasable=is_purchasable,
                is_producible=is_producible,
                is_active=is_active,
                created_by=actor,
                updated_by=actor,
            )
        except Exception as exc:
            if _is_unique_violation(exc):
                raise Conflict(
                    f"Product with code '{code}' already exists.",
                    details={"field": "code", "value": code},
                ) from exc
            raise

        # Audit write.
        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.CREATE,
                entity_type=ENTITY_PRODUCTS,
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
        category_id: int | None = None,
        unit_id: int | None = None,
        purchase_price: float | None = None,
        selling_price: float | None = None,
        low_stock_threshold: float | None = None,
        allow_negative_stock: bool | None = None,
        notes: str | None = None,
        is_sellable: bool | None = None,
        is_purchasable: bool | None = None,
        is_producible: bool | None = None,
        is_active: bool | None = None,
        if_match: str | None = None,
        ctx: AuditContext | None = None,
    ) -> dict[str, Any]:
        """Update a product. If-Match is REQUIRED (OpenAPI IfMatchRequired).

        * If-Match omitted -> 428/400 MissingHeader.
        * If-Match mismatch -> 412 VersionMismatch.
        * Price change (purchase_price/selling_price) writes a
          product_price_history row.
        * ``updated_by`` is set to the authenticated principal's user_id
          (NOT NULL on the products table).
        """

        existing = await self._repo.get(id)
        if existing is None:
            raise NotFound(f"Product {id} not found.")

        # If-Match REQUIRED on PATCH.
        etag, _ = _version_from_row(existing)
        check_if_match(provided=if_match, current_etag=etag)

        # Detect price changes for price-history.
        price_changed = (
            purchase_price is not None
            and float(purchase_price) != float(existing["purchase_price"])
        ) or (
            selling_price is not None and float(selling_price) != float(existing["selling_price"])
        )

        actor = ctx.user_id if ctx and ctx.user_id is not None else 0

        row = await self._repo.update(
            id,
            name=name,
            category_id=category_id,
            unit_id=unit_id,
            purchase_price=purchase_price,
            selling_price=selling_price,
            low_stock_threshold=low_stock_threshold,
            allow_negative_stock=allow_negative_stock,
            notes=notes,
            is_sellable=is_sellable,
            is_purchasable=is_purchasable,
            is_producible=is_producible,
            is_active=is_active,
            updated_by=actor,
        )

        if row is None:
            raise NotFound(f"Product {id} not found after update.")

        # Write price-history row if prices changed.
        if price_changed:
            await self._write_price_history(
                id,
                purchase_price=float(row["purchase_price"]),
                selling_price=float(row["selling_price"]),
                changed_by=actor,
            )

        # Audit: old_values = existing, new_values = row.
        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.UPDATE,
                entity_type=ENTITY_PRODUCTS,
                entity_id=id,
                old_values=dict(existing),
                new_values={k: v for k, v in row.items()},
                ctx=ctx,
            )

        await self._uow.commit()
        return row

    async def _write_price_history(
        self,
        product_id: int,
        *,
        purchase_price: float,
        selling_price: float,
        changed_by: int | None,
    ) -> None:
        """Insert a product_price_history row for a price change.

        Per BR-PRODUCT-002: historical transactions keep their snapshot price;
        only new transactions use the new price. The DB has no trigger for this.
        """
        await self._uow.execute(
            """
            INSERT INTO product_price_history (
                product_id, purchase_price, selling_price,
                effective_at, changed_by
            ) VALUES (
                :product_id, :purchase_price, :selling_price,
                NOW(), :changed_by
            )
            """,
            {
                "product_id": product_id,
                "purchase_price": purchase_price,
                "selling_price": selling_price,
                "changed_by": changed_by,
            },
        )

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
            raise NotFound(f"Product {id} not found.")

        actor = ctx.user_id if ctx and ctx.user_id is not None else 0
        row = await self._repo.deactivate(
            id,
            updated_by=actor,
        )

        if ctx is not None and row is not None:
            await write_audit(
                self._uow,
                action=AuditAction.DEACTIVATE,
                entity_type=ENTITY_PRODUCTS,
                entity_id=id,
                old_values=dict(existing),
                new_values={k: v for k, v in row.items()},
                reason=reason,
                ctx=ctx,
            )

        await self._uow.commit()
        assert row is not None  # deactivate always returns row since we checked existence
        return row

    # ------------------------------------------------------------------
    # Delete (hard)
    # ------------------------------------------------------------------

    async def delete(
        self,
        id: int,
        *,
        idempotency_key: str | None = None,
        ctx: AuditContext | None = None,
    ) -> None:
        """Hard-delete a product.

        Blocked by:
        * Any transaction/movement/payment/AR-AP reference -> 409 referenced_by_history.
        * Not found -> 404.
        """

        existing = await self._repo.get(id)
        if existing is None:
            raise NotFound(f"Product {id} not found.")

        try:
            deleted = await self._repo.hard_delete(id)
        except Exception as exc:
            if _is_fk_violation(exc):
                raise ReferencedByHistory(
                    "Product is referenced by history.",
                    details={"resource": "product", "id": id},
                ) from exc
            raise

        if not deleted:
            raise NotFound(f"Product {id} not found after delete.")

        # 'delete' is NOT in ck_audit_action whitelist; use 'update'.
        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.UPDATE,
                entity_type=ENTITY_PRODUCTS,
                entity_id=id,
                old_values=dict(existing),
                new_values=None,
                reason="hard_delete",
                ctx=ctx,
            )

        await self._uow.commit()

    # ------------------------------------------------------------------
    # Valuation helpers (for response composition)
    # ------------------------------------------------------------------

    async def get_system_setting_bool(self, key: str, *, default: bool = False) -> bool:
        """Read a boolean ``system_settings`` row.

        Wraps :func:`app.inventory.valuation.get_settings_bool` so callers
        don't reach into the inventory module directly.
        """
        val = await get_settings_bool(self._uow, key, default=default)
        return bool(val) if val is not None else False

    async def get_valuation(self, product_id: int) -> dict[str, Any] | None:
        """Read from the ``product_valuation`` view.

        Returns: ``product_id, on_hand_quantity, inventory_value``.
        ``moving_average_unit_cost`` is computed app-side per the locked
        schema (the view does not carry it — see ``app.inventory.valuation``).
        """
        return await self._uow.first_row(
            """
            SELECT product_id, on_hand_quantity, inventory_value
            FROM product_valuation
            WHERE product_id = :pid
            """,
            {"pid": product_id},
        )

    async def get_moving_average_unit_cost(self, product_id: int) -> float | None:
        """Compute moving average unit cost from positive stock movements.

        ``moving_average_unit_cost = SUM(total_cost) / SUM(quantity)`` over
        positive-quantity movements. Returns None if no inbound movements.
        """
        row = await self._uow.first_row(
            """
            SELECT SUM(total_cost) AS total_cost, SUM(quantity) AS total_qty
            FROM stock_movements
            WHERE product_id = :pid AND quantity > 0 AND total_cost IS NOT NULL
            """,
            {"pid": product_id},
        )
        if row is None or row.get("total_qty") is None or int(row["total_qty"]) == 0:
            return None
        return int(row["total_cost"]) // int(row["total_qty"])

    # ------------------------------------------------------------------
    # B.7 — Read-only sub-resources (price-history, stock-movements, valuation)
    # ------------------------------------------------------------------

    async def list_price_history(
        self,
        product_id: int,
        *,
        page: int = 1,
        per_page: int = 50,
        q: str | None = None,
        from_: str | None = None,
        to: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """List ``product_price_history`` rows for a product, newest first.

        Per OpenAPI §15.7 line 2079 ("Price history (newest first)").
        Reads only — no audit, no write. Raises ``NotFound`` if the
        product does not exist (OpenAPI §15.7 ``NotFound``).
        """
        # 404 if product does not exist (BR-PRODUCT scope: product must exist).
        if await self._repo.get(product_id) is None:
            raise NotFound(f"Product {product_id} not found.")

        params: dict[str, Any] = {"pid": product_id}
        clauses: list[str] = []
        if q:
            clauses.append("(effective_at::text ILIKE :q)")
            params["q"] = f"%{q}%"
        if from_:
            clauses.append("effective_at >= :from_")
            params["from_"] = _parse_dt(from_)
        if to:
            clauses.append("effective_at <= :to")
            params["to"] = _parse_dt(to)

        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        limit = max(1, min(per_page, 500))
        offset = (max(1, page) - 1) * limit
        params["limit"] = limit
        params["offset"] = offset

        total = await self._uow.first_scalar(
            f"SELECT COUNT(*) FROM product_price_history {where}",  # noqa: S608
            params,
        )
        rows = await self._uow.fetch_all(
            f"""
            SELECT id, product_id, purchase_price, selling_price,
                   effective_at, changed_by
            FROM product_price_history {where}
            ORDER BY effective_at DESC, id DESC
            LIMIT :limit OFFSET :offset
            """,  # noqa: S608 — closed whitelist literals, user data parameterized
            params,
        )
        return rows, int(total or 0)

    async def list_stock_movements(
        self,
        product_id: int,
        *,
        page: int = 1,
        per_page: int = 50,
        q: str | None = None,
        from_: str | None = None,
        to: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """List ``stock_movements`` rows for a product.

        Per OpenAPI §15.7 line 2127. The table is append-only (immutable
        via DB trigger); this endpoint is READ-ONLY and never writes.
        Raises ``NotFound`` if the product does not exist.
        """
        if await self._repo.get(product_id) is None:
            raise NotFound(f"Product {product_id} not found.")

        params: dict[str, Any] = {"pid": product_id}
        clauses: list[str] = []
        if q:
            clauses.append("(trigger ILIKE :q OR reference_type ILIKE :q OR reason ILIKE :q)")
            params["q"] = f"%{q}%"
        if from_:
            clauses.append("movement_date >= :from_")
            params["from_"] = _parse_dt(from_)
        if to:
            clauses.append("movement_date <= :to")
            params["to"] = _parse_dt(to)

        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        limit = max(1, min(per_page, 500))
        offset = (max(1, page) - 1) * limit
        params["limit"] = limit
        params["offset"] = offset

        total = await self._uow.first_scalar(
            f"SELECT COUNT(*) FROM stock_movements {where}",  # noqa: S608
            params,
        )
        rows = await self._uow.fetch_all(
            f"""
            SELECT id, product_id, movement_date, trigger, quantity,
                   unit_cost_at_movement, total_cost, reference_type,
                   reference_id, reference_line_id, reversal_of_movement_id,
                   reversed_by_movement_id, reason, created_at, created_by
            FROM stock_movements {where}
            ORDER BY movement_date DESC, id DESC
            LIMIT :limit OFFSET :offset
            """,  # noqa: S608 — closed whitelist literals, user data parameterized
            params,
        )
        return rows, int(total or 0)

    async def get_valuation_snapshot(self, product_id: int) -> dict[str, Any]:
        """Read the per-product valuation snapshot.

        Returns the OpenAPI ``ProductValuation`` shape:
        ``{ product_id, on_hand_quantity, moving_average_unit_cost,
        inventory_value, as_of }``.

        The locked ``product_valuation`` view exposes only
        ``on_hand_quantity`` and ``inventory_value``; the moving average
        is derived app-side via
        :func:`app.inventory.valuation.compute_moving_average_unit_cost`.
        Raises ``NotFound`` if the product does not exist.

        NOTE: if the product has zero stock and no movements at all the
        view row is absent — we synthesize a zero-row so the contract
        shape (``on_hand_quantity``, ``inventory_value`` required) is
        always satisfied.
        """
        if await self._repo.get(product_id) is None:
            raise NotFound(f"Product {product_id} not found.")

        row = await self._uow.first_row(
            """
            SELECT product_id, on_hand_quantity, inventory_value
            FROM product_valuation
            WHERE product_id = :pid
            """,
            {"pid": product_id},
        )
        on_hand = row["on_hand_quantity"] if row else 0
        inv_value = row["inventory_value"] if row else 0
        mauc = compute_moving_average_unit_cost(
            on_hand_quantity=on_hand,
            inventory_value=inv_value,
        )
        return {
            "product_id": int(product_id),
            "on_hand_quantity": on_hand,
            "moving_average_unit_cost": mauc,
            "inventory_value": inv_value,
            "as_of": datetime.now(tz=UTC),
        }


__all__ = ["ProductService"]
