"""Inventory module (stubs re-exporting the valuation helpers).

Implements ``Backend-Architecture-V1.0.md`` §13.6 / §13.5 — server-side
derived stock metrics (on-hand, moving-average cost, low-stock flag,
negative-stock fallback). The full service layer for stock movements
is in scope for M4+; M2 only needs the read-time valuation + system
settings parsers exposed here.

E.5 (Phase E) adds the three read-only endpoints:

* :meth:`InventoryService.list_inventory`      — ``GET /inventory``
* :meth:`InventoryService.get_product_stock`   — ``GET /inventory/products/{id}``
* :meth:`InventoryService.list_low_stock`      — ``GET /inventory/low-stock``

E.6 (Phase E) adds the lifecycle write endpoint:

* :meth:`InventoryService.create_adjustment`   — ``POST /inventory/adjustments``
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.audit.service import ENTITY_PRODUCTS, AuditContext, write_audit
from app.concurrency.etag import check_if_match
from app.concurrency.master_etag import etag_and_version_from_updated_at
from app.db import UnitOfWork
from app.errors import BusinessRuleViolation, NegativeStockDisallowed, NotFound
from app.inventory.repo import (
    count_inventory,
    count_low_stock,
    count_stock_movements,
    get_inventory_summary_stats,
    get_product_for_update,
    get_product_stock_row,
    get_setting_bool,
    get_stock_movement_by_id,
    insert_stock_movement,
    list_inventory_rows,
    list_low_stock_rows,
    list_stock_movement_rows,
)
from app.inventory.valuation import (
    compute_moving_average_unit_cost,
    get_product_valuation,
    get_settings_bool,
    get_settings_number,
)
from app.logging import get_logger
from app.services.idempotency import (
    IdempotencyStore,
    compute_request_fingerprint,
)
from app.validation.enums import AuditAction
from app.validation.pagination import make_pagination

logger = get_logger(__name__)

__all__ = [
    "InventoryService",
    # M2 re-exports
    "compute_moving_average_unit_cost",
    "get_product_valuation",
    "get_settings_bool",
    "get_settings_number",
]


def _enrich_summary(
    row: dict[str, Any],
    *,
    as_of: datetime,
) -> dict[str, Any]:
    """Build an ``InventorySummary`` contract-shaped dict from a repo row."""
    on_hand = row.get("on_hand_quantity")
    inv_value = row.get("inventory_value")
    threshold = row.get("low_stock_threshold")
    is_active = bool(row.get("is_active"))
    low_stock = bool(
        is_active
        and threshold is not None
        and on_hand is not None
        and float(on_hand) <= float(threshold)
    )
    updated_at = row.get("updated_at")
    if isinstance(updated_at, datetime):
        # Use the same ISO-8601 form Pydantic emits in JSON
        # (``Z`` for UTC, microsecond precision) so the value can be
        # round-tripped as an ETag for ``If-Match`` on POST
        # /inventory/adjustments.
        from pydantic import TypeAdapter

        updated_at_iso = TypeAdapter(datetime).dump_python(
            updated_at, mode="json"
        )
    else:
        updated_at_iso = str(updated_at) if updated_at is not None else None
    return {
        "product_id": int(row["product_id"]),
        # Phase 3A: product identity (name, code) sourced from the joined
        # products row in product_valuation. The view's ``name`` /
        # ``code`` columns are the canonical product identity.
        "product_name": row.get("name"),
        "product_code": row.get("code"),
        "on_hand_quantity": on_hand,
        "moving_average_unit_cost": compute_moving_average_unit_cost(
            on_hand_quantity=on_hand,
            inventory_value=inv_value,
        ),
        "inventory_value": inv_value,
        "low_stock": low_stock,
        # Phase 3A: expose the same ``updated_at`` that the adjustment
        # endpoint uses for If-Match so the frontend can construct the
        # canonical ETag without an extra round-trip.
        "updated_at": updated_at_iso,
        "as_of": as_of,
    }


def _enrich_stock(
    row: dict[str, Any],
    *,
    as_of: datetime,
) -> dict[str, Any]:
    """Build a ``ProductStock`` contract-shaped dict from a repo row."""
    on_hand = row.get("on_hand_quantity")
    inv_value = row.get("inventory_value")
    threshold = row.get("low_stock_threshold")
    is_active = bool(row.get("is_active"))
    low_stock = bool(
        is_active
        and threshold is not None
        and on_hand is not None
        and float(on_hand) <= float(threshold)
    )
    return {
        "product_id": int(row["product_id"]),
        "on_hand_quantity": on_hand,
        "moving_average_unit_cost": compute_moving_average_unit_cost(
            on_hand_quantity=on_hand,
            inventory_value=inv_value,
        ),
        "inventory_value": inv_value,
        "low_stock": low_stock,
        "low_stock_threshold": threshold,
        "as_of": as_of,
    }


class InventoryService:
    """Read-side service for the E.5 inventory endpoints.

    The route layer handles auth, pagination parameter parsing and
    capability enforcement; this service owns the SQL + derivation.
    """

    __slots__ = ("_uow",)

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    # -- GET /inventory -------------------------------------------------

    async def list_inventory(
        self,
        *,
        page: int,
        per_page: int,
        sort: str,
        q: str | None,
        category_id: int | None,
        is_active: bool | None,
        from_iso: str | None,
        to_iso: str | None,
        stock_status: str | None = None,
    ) -> dict[str, Any]:
        total = await count_inventory(
            self._uow,
            q=q,
            category_id=category_id,
            is_active=is_active,
            from_iso=from_iso,
            to_iso=to_iso,
            stock_status=stock_status,
        )
        rows = await list_inventory_rows(
            self._uow,
            page=page,
            per_page=per_page,
            sort=sort,
            q=q,
            category_id=category_id,
            is_active=is_active,
            from_iso=from_iso,
            to_iso=to_iso,
            stock_status=stock_status,
        )
        as_of = datetime.now(tz=UTC)
        data = [_enrich_summary(r, as_of=as_of) for r in rows]
        pag = make_pagination(page=page, per_page=per_page, total=total)
        # Phase 3A: authoritative backend summary covering the *same*
        # filtered dataset (not the current page). Always computed with
        # identical WHERE clause parameters so the numbers match the
        # paginated result set exactly.
        summary = await get_inventory_summary_stats(
            self._uow,
            q=q,
            category_id=category_id,
            is_active=is_active,
            from_iso=from_iso,
            to_iso=to_iso,
            stock_status=stock_status,
        )
        return {"data": data, "pagination": pag, "summary": summary}

    # -- GET /inventory/low-stock --------------------------------------

    async def list_low_stock(
        self,
        *,
        page: int,
        per_page: int,
        sort: str,
        q: str | None,
        category_id: int | None,
        from_iso: str | None,
        to_iso: str | None,
    ) -> dict[str, Any]:
        total = await count_low_stock(
            self._uow,
            q=q,
            category_id=category_id,
            from_iso=from_iso,
            to_iso=to_iso,
        )
        rows = await list_low_stock_rows(
            self._uow,
            page=page,
            per_page=per_page,
            sort=sort,
            q=q,
            category_id=category_id,
            from_iso=from_iso,
            to_iso=to_iso,
        )
        as_of = datetime.now(tz=UTC)
        data = [_enrich_summary(r, as_of=as_of) for r in rows]
        pag = make_pagination(page=page, per_page=per_page, total=total)
        return {"data": data, "pagination": pag}

    # -- GET /inventory/products/{product_id} ----------------------------

    async def get_product_stock(self, *, product_id: int) -> dict[str, Any] | None:
        row = await get_product_stock_row(self._uow, product_id=product_id)
        if row is None:
            return None
        as_of = datetime.now(tz=UTC)
        return _enrich_stock(row, as_of=as_of)

    # ==================================================================
    # E.7 — GET /stock-movements, GET /stock-movements/{id}
    # ==================================================================
    #
    # Read-only views on the append-only ``stock_movements`` ledger
    # (Backend-Architecture §12 + schema.sql §18.2 immutability
    # triggers). No state mutation, no audit, no ETag, no
    # Idempotency-Key. Capability: ``inventory.view`` (enforced at the
    # route layer; see ``app.api.v1.stock_movements``).
    #
    # The per-product sub-resource ``GET /products/{id}/stock-movements``
    # (operationId ``getProductStockMovements``) is implemented in
    # ``app.services.products`` for B.7; this module owns the top-level
    # E.7 endpoints that walk the whole ledger.

    async def list_stock_movements(
        self,
        *,
        page: int,
        per_page: int,
        sort: str,
        product_id: int | None,
        trigger: str | None,
        reference_type: str | None,
        reference_id: int | None,
        from_iso: str | None = None,
        to_iso: str | None = None,
        q: str | None = None,
    ) -> dict[str, Any]:
        """Paginated stock-movement list with optional filters.

        Mirrors ``openapi.yaml`` §15.2 ``listStockMovements`` (lines
        4103-4162): the only filters accepted are ``product_id``,
        ``trigger``, ``reference_type`` and ``reference_id`` (no
        ``q``/``from``/``to`` here — those exist on the
        ``/products/{id}/stock-movements`` B.7 sub-resource). Returns
        the contract envelope ``{ data, pagination }`` with each row
        shaped exactly like the OpenAPI ``StockMovement`` schema.

        ``from_iso``/``to_iso``/``q`` were added in F.1 (inventory-movements
        report) and default to ``None`` so the E.7 callers (which do
        not declare these kwargs) are unaffected.
        """
        total = await count_stock_movements(
            self._uow,
            product_id=product_id,
            trigger=trigger,
            reference_type=reference_type,
            reference_id=reference_id,
            from_iso=from_iso,
            to_iso=to_iso,
            q=q,
        )
        rows = await list_stock_movement_rows(
            self._uow,
            page=page,
            per_page=per_page,
            sort=sort,
            product_id=product_id,
            trigger=trigger,
            reference_type=reference_type,
            reference_id=reference_id,
            from_iso=from_iso,
            to_iso=to_iso,
            q=q,
        )
        data = [_enrich_stock_movement(r) for r in rows]
        pag = make_pagination(page=page, per_page=per_page, total=total)
        return {"data": data, "pagination": pag}

    async def get_stock_movement(
        self, *, movement_id: int
    ) -> dict[str, Any] | None:
        """Single ``stock_movements`` row, or ``None`` if missing.

        404 is raised by the route layer (``NotFound`` envelope) when
        this returns ``None`` — service stays repo-shaped.
        """
        row = await get_stock_movement_by_id(self._uow, movement_id=movement_id)
        if row is None:
            return None
        return _enrich_stock_movement(row)

    # ==================================================================
    # E.6 — POST /inventory/adjustments (stock-adjustment lifecycle)
    # ==================================================================

    async def create_adjustment(
        self,
        *,
        principal_user_id: int,
        product_id: int,
        quantity: Decimal,
        reason: str,
        if_match: str | None,
        idempotency_key: str | None,
        ctx: AuditContext | None,
        request_body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        """createStockAdjustment — POST /inventory/adjustments.

        Implements ``Backend-Architecture-V1.0.md`` §15.6 + Event 18/19
        of the accounting event matrix. All business mutations + audit +
        the idempotency entry share one ``COMMIT``; any failure rolls
        back the entire transaction.

        Pre-conditions (raised as 4xx with the canonical error code):

        * product exists (else 404 ``not_found``)
        * product is active (else 422 ``business_rule_violation``)
        * ETag matches the product's ``updated_at`` (else 412
          ``version_mismatch``)
        * ``quantity`` ≠ 0 (enforced by Pydantic ``exclusiveMinimum`` /
          ``maximum`` on the schema; Pydantic 422 already covers the
          case)
        * ``reason`` is non-empty (enforced by Pydantic minLength=1)

        Negative-stock rule (BR-STOCK-010/011):

        * ``quantity < 0`` and ``on_hand + quantity < 0`` is forbidden
          when the product's ``allow_negative_stock`` is FALSE *and*
          the system default ``default_negative_stock_allowed`` is
          FALSE. Either override enables the write (per-product
          override wins when non-NULL; NULL falls back to the global).
        * When the rule blocks, raise :class:`NegativeStockDisallowed`
          (422 ``negative_stock_disallowed``).
        * When allowed (override=TRUE on the product), the decrement
          proceeds and the on-hand can go negative — no extra
          ``insufficient_stock`` error in that branch.

        Effects:

        * Insert one ``stock_movements`` row with
          ``trigger='stock_adjustment'``, signed quantity, and
          ``unit_cost_at_movement`` = current MAUC snapshot
          (computed app-side via
          :func:`app.inventory.valuation.compute_moving_average_unit_cost`
          applied to the ``product_valuation`` view columns).
        * ``reason`` is persisted on both the movement row *and* the
          audit row (``audit_log.new_values.reason``).
        * ``reference_type`` / ``reference_id`` are NULL — per
          Backend-Architecture §15.6 + Reconciliation Report C-07 the
          adjustment is an orphan movement (no parent header table).
        * NO P&L entry is auto-created in V1 (Backend-Architecture
          §12.5: the inventory value is correct, the P&L effect is a
          manual reconciliation step the Owner may record via
          ``POST /manual-entries`` if desired).

        Idempotency mirrors E.3 / E.4: replay returns the cached body
        with ``Idempotent-Replay: true``; key+body mismatch → 409
        ``idempotency_violation``.

        Returns the canonical ``StockMovement`` contract response.
        """
        # ----- idempotency replay (M4 pattern) -------------------------
        idem_rec = None
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="POST",
                path="/inventory/adjustments",
                body=request_body,
            )
            idem_rec = await store.start(
                key=idempotency_key,
                user_id=principal_user_id or 0,
                endpoint="POST /inventory/adjustments",
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
                return cached, True  # type: ignore[return-value]

        # ----- row-level lock on the product --------------------------
        product = await get_product_for_update(self._uow, product_id=int(product_id))
        if product is None:
            raise NotFound(f"Product {product_id} not found.")
        if not bool(product.get("is_active")):
            # Deactivated products cannot have their stock touched.
            # The OpenAPI contract lists 422 here as
            # ``business_rule_violation`` (a generic 422, not
            # ``negative_stock_disallowed``) — keeps the contract
            # envelope narrow per Backend-Architecture §19.
            raise _inactive_product_error(int(product_id))

        # ----- ETag / If-Match -----------------------------------------
        # ``products.updated_at`` drives the master etag (no version
        # column on this table — Backend-Architecture §12 + the
        # existing ``updateProducts`` route use the same approach).
        ts = product["updated_at"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        current_etag, _v = etag_and_version_from_updated_at(ts)
        check_if_match(provided=if_match, current_etag=current_etag)

        # ----- resolve negative-stock policy (BR-STOCK-011) -----------
        # product.allow_negative_stock NULL → fall back to the global
        # default ``system_settings.default_negative_stock_allowed``.
        product_override = product.get("allow_negative_stock")
        if product_override is None:
            global_flag = await get_setting_bool(
                self._uow,
                key="default_negative_stock_allowed",
                default=False,
            )
            product_neg_ok = bool(global_flag)
        else:
            product_neg_ok = bool(product_override)

        # ----- read on-hand snapshot for the negative-stock pre-check -
        stock_row = await get_product_stock_row(self._uow, product_id=int(product_id))
        # ``product_valuation`` view exposes NULL on_hand_quantity only
        # for products with zero movements; coerce to 0 for the math.
        on_hand_raw = stock_row.get("on_hand_quantity") if stock_row else None
        try:
            on_hand = Decimal("0") if on_hand_raw is None else Decimal(str(on_hand_raw))
        except (TypeError, ValueError, ArithmeticError):
            on_hand = Decimal("0")

        qty = Decimal(str(quantity))

        # ----- negative-stock guard (BR-STOCK-010/011) -----------------
        if qty < 0 and not product_neg_ok:
            projected = on_hand + qty
            if projected < 0:
                raise NegativeStockDisallowed(
                    "Stock adjustment would drive on-hand below zero and "
                    "negative stock is not allowed for this product.",
                    details={
                        "code": "negative_stock_disallowed",
                        "product_id": int(product_id),
                        "on_hand_quantity": float(on_hand),
                        "requested_quantity": float(qty),
                        "projected_on_hand": float(projected),
                    },
                )

        # ----- unit-cost snapshot: current MAUC (NUMERIC(15,4)) --------
        inv_value_raw = stock_row.get("inventory_value") if stock_row else None
        mauc = compute_moving_average_unit_cost(
            on_hand_quantity=on_hand_raw,
            inventory_value=inv_value_raw,
        )
        if mauc is None:
            # No cost basis yet (zero stock). The DB constraint
            # ``ck_sm_total_cost_nonzero`` requires total_cost ≠ 0 for
            # the stock_adjustment trigger, so we MUST snapshot a
            # non-zero unit cost. Per BR-COST-006 the last purchase
            # price (``products.purchase_price``) is the canonical
            # COGS fallback — we honour it here for opening-balance
            # adjustments. For an item that has never had a purchase
            # AND has purchase_price = 0 (the schema default) we fall
            # back to 0.0001 (the smallest positive NUMERIC(15,4)) so
            # the CHECK passes without inventing a meaningful cost
            # basis. The P&L effect is NOT auto-posted in V1
            # (Backend-Architecture §12.5), so the tiny epsilon does
            # not contaminate the financial statements.
            purchase_price_raw = product.get("purchase_price")
            try:
                fallback_uc = (
                    Decimal(str(purchase_price_raw))
                    if purchase_price_raw is not None
                    else None
                )
            except (TypeError, ValueError, ArithmeticError):
                fallback_uc = None
            if fallback_uc is not None and fallback_uc > 0:
                unit_cost_snapshot = fallback_uc.quantize(Decimal("0.0001"))
            else:
                # Epsilon: keep at 4-decimal precision (NUMERIC(15,4))
                # so that even a zero-on-hand product gets a non-zero
                # total_cost that satisfies ``ck_sm_total_cost_nonzero``.
                unit_cost_snapshot = Decimal("0.0001")
            # total_cost must be non-zero with the same sign as quantity.
            # Use the raw product (no rounding to 0.01) so the epsilon
            # 0.0001 * 10 → 0.0010 stays non-zero after the column's
            # NUMERIC(20,4) coercion.
            total_cost_snapshot = (qty * unit_cost_snapshot).quantize(
                Decimal("0.0001")
            )
        else:
            unit_cost_snapshot = Decimal(str(mauc)).quantize(Decimal("0.0001"))
            total_cost_snapshot = (qty * unit_cost_snapshot).quantize(
                Decimal("0.0001")
            )

        # ----- INSERT stock_movements ----------------------------------
        movement = await insert_stock_movement(
            self._uow,
            product_id=int(product_id),
            trigger="stock_adjustment",
            quantity=qty,
            unit_cost_at_movement=unit_cost_snapshot,
            total_cost=total_cost_snapshot,
            reference_type=None,
            reference_id=None,
            reference_line_id=None,
            reason=reason,
            created_by=int(principal_user_id),
        )
        if movement is None:  # pragma: no cover - defensive
            raise NotFound("stock_movements insert returned no row.")

        # ----- audit (AuditAction.ADJUST) ------------------------------
        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.ADJUST,
                entity_type=ENTITY_PRODUCTS,
                entity_id=int(product_id),
                old_values={
                    "updated_at": ts.isoformat() if isinstance(ts, datetime) else ts,
                },
                new_values={
                    "stock_movement_id": int(movement["id"]),
                    "trigger": str(movement["trigger"]),
                    "quantity": float(qty),
                    "unit_cost_at_movement": (
                        float(unit_cost_snapshot)
                        if unit_cost_snapshot is not None
                        else None
                    ),
                    "total_cost": (
                        float(total_cost_snapshot)
                        if total_cost_snapshot is not None
                        else None
                    ),
                    "reason": str(reason),
                },
                reason=str(reason),
                ctx=ctx,
            )

        # ----- shape response ------------------------------------------
        result = _enrich_stock_movement(movement)

        if idempotency_key is not None and idem_rec is not None:
            store = IdempotencyStore(self._uow)
            await store.complete(
                record_id=idem_rec.id,
                status=201,
                body=_to_jsonb(result) or "{}",
                user_id=principal_user_id,
            )

        return result, False


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _to_jsonb(values: dict[str, Any] | None) -> str | None:
    if values is None:
        return None
    return json.dumps(values, default=str, sort_keys=True)


def _enrich_stock_movement(row: dict[str, Any]) -> dict[str, Any]:
    """Shape a stock_movements row into the OpenAPI ``StockMovement`` response.

    The contract lists every column as optional except the required
    ``id``, ``product_id``, ``movement_date``, ``trigger``,
    ``quantity``, ``created_at``, ``created_by``. We pass through every
    column the DB returned and let Pydantic/JSON drop the None
    decorations on the optional ones.
    """
    enriched: dict[str, Any] = dict(row)
    # JSON-friendly Decimal / datetime conversion happens via FastAPI's
    # jsonable_encoder in the route layer. Keep types as-is here.
    return enriched


def _inactive_product_error(product_id: int) -> BusinessRuleViolation:
    """Construct the 422 raised when the target product is deactivated."""
    return BusinessRuleViolation(
        "Product is deactivated; stock adjustments are not allowed.",
        details={
            "code": "business_rule_violation",
            "field": "product_id",
            "product_id": int(product_id),
            "reason": "product_deactivated",
        },
    )
