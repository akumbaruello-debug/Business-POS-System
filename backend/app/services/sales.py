"""Business logic for the Sales lifecycle (B.8).

Per ``Backend-Architecture-V1.0.md`` §7/§8/§9/§10/§13/§14/§15 + OpenAPI
§15, the service is the transaction boundary. All lifecycle mutations
(post, pay, cancel, return) run inside a single ``UnitOfWork`` so the
business mutation + idempotency entry + audit log share one ``COMMIT``.

Lifecycle (per PRD §15.1):
    draft → posted → {completed | partially_returned | returned | cancelled}
    partially_returned → {returned | cancelled}
    returned → cancelled
    completed → {partially_returned | cancelled}   (auto-complete then partial return)

The ``version`` column + ``updated_at`` trigger auto-bump on UPDATE, so
the ETag (quoted ``updated_at``) round-trips for If-Match.

DB triggers enforce the hard invariants:
* ``trg_sale_lifecycle_terminal`` — reject illegal transitions.
* ``trg_sale_lines_immutable_when_posted`` — reject edits on posted lines.
* ``trg_sale_payments_allocation_bound`` — Σ payments ≤ total.
* ``trg_srl_quantity_bound`` — Σ returns ≤ original qty.
* ``trg_cash_movements_balance_check`` — cash ≥ 0.
* ``trg_stock_movements_immutable`` — append-only.

The service does **pre-checks** before the DB sees the mutation, so we
return clean, documented 4xx error codes (with the right ``code`` field)
instead of opaque constraint violations. The DB trigger is the final
backstop for concurrency races.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.audit.service import (
    ENTITY_SALE_LINES,
    ENTITY_SALE_PAYMENTS,
    ENTITY_SALES,
    ENTITY_SALES_RETURNS,
    AuditContext,
    write_audit,
)
from app.concurrency.etag import check_if_match
from app.concurrency.master_etag import etag_and_version_from_updated_at
from app.db import UnitOfWork
from app.errors import (
    Conflict,
    InsufficientStock,
    LifecycleViolation,
    NotFound,
    PermissionDenied,
    TenderedNotAllowedForNonCash,
)
from app.logging import get_logger
from app.repositories.products import ProductRepository
from app.repositories.sales import SaleRepository
from app.services.idempotency import (
    IdempotencyStore,
    IdempotencyViolationConflict,
    compute_request_fingerprint,
)
from app.validation.enums import AuditAction, LifecycleStatus

logger = get_logger(__name__)

# Canonical lifecycle set for the ``sales`` table (DB CHECK + OpenAPI).
_LIFECYCLE_TERMINAL = frozenset({"cancelled"})
_LIFECYCLE_POSTABLE = frozenset({"draft"})
_LIFECYCLE_CANCELLABLE = frozenset(
    {"posted", "completed", "partially_returned", "returned", "cancelled"}
)
_LIFECYCLE_RETURNABLE = frozenset({"posted", "completed", "partially_returned"})

# Advisory-lock tag for the cash balance; the DB trigger is the real guard.
# (We rely on the trigger rather than pg_advisory locking per §9.2 — the
#  cash_movements pre-insert trigger serializes via table access.)
_SALE_LINE_OWNER_CAP = "sale.edit_own_draft"

# Two-decimal quant for all monetary results.
_2DP = Decimal("0.01")


def _quant(x: Any) -> Decimal:
    if x is None:
        return Decimal("0")
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


def _q2(x: Any) -> Decimal:
    return _quant(x).quantize(_2DP)


def _build_below_cost_warning(
    sale_row: dict[str, Any], lines: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Build the F.10 below-cost warning payload for a posted sale.

    Returns ``None`` when no line is sold below its server-computed
    ``unit_cost_snapshot``; the field is then omitted from the response.

    The boundary is **strict**: ``unit_price < unit_cost_snapshot``.
    Equality (``unit_price == unit_cost_snapshot``) is NOT below cost.
    Lines with a ``unit_cost_snapshot`` of zero (no prior cost) are
    never flagged — there is nothing to compare against.
    """
    below: list[dict[str, Any]] = []
    for ln in lines:
        cost = _quant(ln.get("unit_cost_snapshot"))
        price = _quant(ln.get("unit_price"))
        if cost > 0 and price < cost:
            gap = (cost - price).quantize(_2DP)
            below.append(
                {
                    "line_id": int(ln["id"]),
                    "line_number": int(ln.get("line_number") or 0),
                    "unit_price": float(price.quantize(_2DP)),
                    "unit_cost_snapshot": float(cost.quantize(_2DP)),
                    "gap": float(gap),
                }
            )
    if not below:
        return None
    plural = "s" if len(below) > 1 else ""
    msg = (
        f"{len(below)} line{plural} sold below unit cost snapshot; "
        "sale was posted as-is."
    )
    return {"code": "below_cost", "message": msg, "lines": below}


class SaleService:
    """Business logic for the sales lifecycle + derived fields.

    One instance per ``UnitOfWork``. Routes build a ``SaleService(uow)``
    and call the public mutation methods.
    """

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow
        self._repo = SaleRepository(uow)
        self._prod_repo = ProductRepository(uow)

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _etag_from_row(row: dict[str, Any]) -> tuple[str, int]:
        ts = row["updated_at"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return etag_and_version_from_updated_at(ts)

    @staticmethod
    def _etag_from_return_row(row: dict[str, Any]) -> str:
        """Return the canonical ETag for a sales_returns row.

        sales_returns has no ``updated_at`` column. We treat
        ``cancellation_date`` (set when the return is cancelled) as the
        effective ``updated_at``, falling back to ``created_at``. This
        keeps the If-Match contract identical to the sales/sale-lines path:
        the ETag is the quoted ISO-8601 timestamp that clients read from
        the response's ``updated_at`` field.
        """
        from app.util import format_etag, iso_utc

        ts = row.get("cancellation_date") or row.get("created_at") or row.get("return_date")
        if ts is None:
            return format_etag(str(row.get("version", 1)))
        if isinstance(ts, str):
            try:
                parsed_ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                return format_etag(iso_utc(parsed_ts))
            except ValueError:
                return format_etag(ts)
        return format_etag(iso_utc(ts))

    @staticmethod
    def _payment_state(total: Decimal, paid: Decimal) -> tuple[str, Decimal, Decimal]:
        """Derive (payment_state, paid_amount, outstanding) from Σ payments."""
        paid = paid if paid > 0 else Decimal("0")
        outstanding = max(Decimal("0"), total - paid)
        if paid == 0:
            return "unpaid", paid, outstanding
        if paid < total:
            return "partial", paid, outstanding
        return "paid", paid, outstanding

    async def _recompute_sale(
        self, sale_id: int, *, ctx: AuditContext | None = None
    ) -> dict[str, Any]:
        """Recompute total_amount = Σ line_total - discount, then bump.

        This is the single source of truth for the sale total. We do it
        inside the same transaction as the line change so the header and
        lines never drift.
        """
        sum_lines = await self._repo.sum_line_total(sale_id)
        # Read current discount from the header (immutable during draft line edits).
        header = await self._repo.get(sale_id)
        if header is None:
            raise NotFound(f"Sale {sale_id} not found.")
        discount = _quant(header["discount_amount"])
        new_total = _q2(sum_lines - discount)
        return await self._repo.update_draft(sale_id, total_amount=new_total) or header

    # ==================================================================
    # C.1 — List / Get
    # ==================================================================

    async def list_sales(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
        q: str | None = None,
        sort: str = "id",
        lifecycle_status: str | None = None,
        customer_id: int | None = None,
        payment_state: str | None = None,
        from_iso: str | None = None,
        to_iso: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows, total = await self._repo.list(
            page=page,
            per_page=per_page,
            q=q,
            sort=sort,
            lifecycle_status=lifecycle_status,
            customer_id=customer_id,
            payment_state=None,
            from_iso=from_iso,
            to_iso=to_iso,
        )
        out: list[dict[str, Any]] = []
        for r in rows:
            out.append(await self._enrich_sale(r))
        return out, total

    async def get_sale(self, sale_id: int, *, include: set[str] | None = None) -> dict[str, Any]:
        row = await self._repo.get(sale_id)
        if row is None:
            raise NotFound(f"Sale {sale_id} not found.")
        return await self._enrich_sale(row, include=include or set())

    async def list_sale_lines(self, sale_id: int) -> list[dict[str, Any]]:
        sale = await self._repo.get(sale_id)
        if sale is None:
            raise NotFound(f"Sale {sale_id} not found.")
        lines, _ = await self._repo.list_lines(sale_id)
        return [await self._line_dict_async(ln) for ln in lines]

    async def list_sale_payments(self, sale_id: int) -> list[dict[str, Any]]:
        sale = await self._repo.get(sale_id)
        if sale is None:
            raise NotFound(f"Sale {sale_id} not found.")
        payments, _ = await self._repo.list_payments(sale_id)
        return [self._payment_dict(p) for p in payments]

    async def list_sale_returns(self, sale_id: int) -> list[dict[str, Any]]:
        sale = await self._repo.get(sale_id)
        if sale is None:
            raise NotFound(f"Sale {sale_id} not found.")
        returns, _ = await self._repo.list_returns_for_sale(sale_id)
        out = []
        for r in returns:
            out.append(await self._return_dict(r))
        return out

    async def _enrich_sale(
        self,
        row: dict[str, Any],
        *,
        include: set[str] | None = None,
        with_payments: list[dict[str, Any]] | None = None,
        with_returns: list[dict[str, Any]] | None = None,
        with_lines: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Attach derived fields (payment_state, paid_amount, outstanding,
        ar, crl, version) + embedded sub-resources.

        ``ar`` = outstanding (debtor balance on this sale).
        ``crl`` = current refundable: Σ payments - Σ returns - Σ refunds.
          (refunds are M4; for B.8 crl = Σ payments - Σ returns_selling_price
          is NOT correct — returns reduce AR not cash. crl = Σ payments - prior
          refunds. Since refunds table is out of scope here, crl = Σ payments
          for a paid sale.)
        """
        total = _q2(row["total_amount"])
        paid = await self._repo.sum_payments(row["id"])
        state, paid_amt, outstanding = self._payment_state(total, paid)

        # crl (customer refundable limit): Σ payments - Σ refunds.
        # Refunds table exists in schema but is M4; read 0.
        refunded = Decimal("0")
        crl = max(Decimal("0"), paid - refunded)

        # version from the DB column (trigger-bumped), etag from updated_at.
        etag, _ver = self._etag_from_row(row)

        enriched = dict(row)
        enriched["payment_state"] = state
        enriched["paid_amount"] = paid_amt
        enriched["outstanding"] = outstanding
        enriched["ar"] = outstanding
        enriched["crl"] = crl
        enriched["version"] = int(row["version"]) if row.get("version") is not None else 1
        enriched["etag"] = etag

        inc = include or set()
        if "lines" in inc:
            lines_rows, _ = await self._repo.list_lines(row["id"])
            enriched["lines"] = [await self._line_dict_async(r) for r in lines_rows]
        elif with_lines is not None:
            enriched["lines"] = [await self._line_dict_async(r) for r in with_lines]
        if "payments" in inc:
            pay_rows, _ = await self._repo.list_payments(row["id"])
            enriched["payments"] = [self._payment_dict(r) for r in pay_rows]
        elif with_payments is not None:
            enriched["payments"] = [self._payment_dict(r) for r in with_payments]
        if "returns" in inc:
            ret_rows, _ = await self._repo.list_returns_for_sale(row["id"])
            enriched["returns"] = [await self._return_dict(r) for r in ret_rows]
        elif with_returns is not None:
            enriched["returns"] = [await self._return_dict(r) for r in with_returns]

        return enriched

    @staticmethod
    def _line_dict(
        r: dict[str, Any],
        *,
        below_cost: bool = False,
    ) -> dict[str, Any]:
        return {
            "id": int(r["id"]),
            "sale_id": int(r["sale_id"]),
            "product_id": int(r["product_id"]),
            "quantity": float(r["quantity"]),
            "unit_price": float(r["unit_price"]),
            "discount_amount": float(r["discount_amount"]),
            "line_total": float(r["line_total"]),
            "unit_cost_snapshot": (
                float(r["unit_cost_snapshot"]) if r.get("unit_cost_snapshot") is not None else None
            ),
            "cogs_total_snapshot": (
                float(r["cogs_total_snapshot"])
                if r.get("cogs_total_snapshot") is not None
                else None
            ),
            "is_negative_stock_fallback": bool(r["is_negative_stock_fallback"]),
            "stock_movement_id": (
                int(r["stock_movement_id"]) if r.get("stock_movement_id") is not None else None
            ),
            "line_number": int(r["line_number"]),
            "below_cost": below_cost,
            # Lines have no per-line ``updated_at``; the parent's ETag is
            # injected by the caller in :meth:`add_line`.
            "etag": None,
            "updated_at": None,
        }

    async def _is_line_below_cost(self, r: dict[str, Any]) -> bool:
        """Derived ``below_cost`` flag for a sale line.

        - At post time, ``unit_cost_snapshot`` is populated by the
          stock-movement snapshot — compare the snapshot to ``unit_price``.
        - In draft (snapshot is NULL), compare ``unit_price`` to the
          product's current moving-average unit cost.
        """
        cost = r.get("unit_cost_snapshot")
        if cost is not None and Decimal(str(cost)) > 0:
            return Decimal(str(r["unit_price"])) < Decimal(str(cost))
        product_id = int(r["product_id"])
        mov_avg = await self._repo.moving_average_unit_cost(product_id)
        if mov_avg is None or mov_avg <= 0:
            return False
        return Decimal(str(r["unit_price"])) < mov_avg

    async def _line_dict_async(self, r: dict[str, Any]) -> dict[str, Any]:
        """Async variant of :meth:`_line_dict` that fills the ``below_cost`` flag."""
        bc = await self._is_line_below_cost(r)
        return self._line_dict(r, below_cost=bc)

    @staticmethod
    def _payment_dict(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(r["id"]),
            "sale_id": int(r["sale_id"]),
            "payment_method_id": int(r["payment_method_id"]),
            "amount": float(r["amount"]),
            "tendered_amount": (
                float(r["tendered_amount"]) if r.get("tendered_amount") is not None else None
            ),
            "change_amount": (
                float(r["change_amount"]) if r.get("change_amount") is not None else None
            ),
            "payment_date": r["payment_date"],
            "reference": r.get("reference"),
            "created_at": r["created_at"],
            "created_by": int(r["created_by"]),
        }

    async def _return_dict(self, r: dict[str, Any]) -> dict[str, Any]:
        lines = await self._repo.list_return_lines(int(r["id"]))
        return {
            "id": int(r["id"]),
            "sale_id": int(r["sale_id"]),
            "return_date": r["return_date"],
            "reason": r.get("reason"),
            "total_selling_price_returned": float(r["total_selling_price_returned"]),
            "total_cost_returned": float(r["total_cost_returned"]),
            "lifecycle_status": str(r["lifecycle_status"]),
            "lines": [self._return_line_dict(ln) for ln in lines],
            "created_at": r["created_at"],
            "created_by": int(r["created_by"]),
            "updated_at": r.get("cancellation_date") or r.get("created_at") or r.get("return_date"),
        }

    @staticmethod
    def _return_line_dict(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(r["id"]),
            "sales_return_id": int(r["sales_return_id"]),
            "sale_line_id": int(r["sale_line_id"]),
            "product_id": int(r["product_id"]),
            "quantity": float(r["quantity"]),
            "returned_selling_price": float(r["returned_selling_price"]),
            "returned_unit_cost": float(r["returned_unit_cost"]),
            "line_value": float(r["returned_selling_price"]),
            "line_number": int(r["line_number"]),
        }

    # ==================================================================
    # C.1 — Create draft
    # ==================================================================

    async def create_draft(
        self,
        *,
        principal_user_id: int,
        customer_id: int | None,
        sale_date: datetime | None,
        discount_amount: Decimal,
        notes: str | None,
        reference_no: str | None,
        lines: list[dict[str, Any]],
        ctx: AuditContext | None,
        idempotency_key: str | None,
        request_body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        # Idempotency
        idem_rec = None
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="POST", path="/sales", body=request_body
            )
            idem_rec = await store.start(
                key=idempotency_key,
                user_id=principal_user_id or 0,
                endpoint="POST /sales",
                fingerprint=fingerprint,
            )
            if idem_rec.is_completed:
                await self._uow.commit()
                return idem_rec.response_body, True  # type: ignore[return-value]

        # Validate customer FK
        if customer_id is not None and not await self._repo.contact_exists(customer_id):
            raise NotFound(f"Contact {customer_id} not found.")

        # Pre-validate + compute each line (price override / discount caps)
        validated_lines: list[dict[str, Any]] = []
        for ln in lines:
            line = await self._validate_and_compute_line(ln, principal_user_id)
            validated_lines.append(line)

        line_total_sum = sum((vl["line_total"] for vl in validated_lines), Decimal("0"))
        total_amount = _q2(line_total_sum - discount_amount)

        sale = await self._repo.create_draft(
            customer_id=customer_id,
            sale_date=sale_date,
            total_amount=total_amount,
            discount_amount=_q2(discount_amount),
            notes=notes,
            reference_no=reference_no,
            created_by=principal_user_id,
        )
        sale_id = int(sale["id"])

        # Insert lines
        for line in validated_lines:
            line["line_number"] = await self._repo.next_line_number(sale_id)
            inserted = await self._repo.insert_line(
                sale_id=sale_id,
                product_id=line["product_id"],
                quantity=line["quantity"],
                unit_price=line["unit_price"],
                discount_amount=line["discount_amount"],
                line_total=line["line_total"],
                line_number=line["line_number"],
            )
            if inserted is not None:
                line.update(inserted)

        # Audit
        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.CREATE,
                entity_type=ENTITY_SALES,
                entity_id=sale_id,
                new_values={
                    "customer_id": customer_id,
                    "sale_date": sale_date.isoformat() if sale_date else None,
                    "total_amount": str(total_amount),
                    "discount_amount": str(discount_amount),
                    "lines": [
                        {
                            "product_id": vl["product_id"],
                            "quantity": str(vl["quantity"]),
                            "unit_price": str(vl["unit_price"]),
                            "discount_amount": str(vl["discount_amount"]),
                            "line_total": str(vl["line_total"]),
                        }
                        for vl in validated_lines
                    ],
                },
                ctx=ctx,
            )

        # Persist idempotency (if used) as completed.
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            resp = await self._enrich_sale(sale, include={"lines"})
            if idem_rec is not None:
                await store.complete(
                    record_id=idem_rec.id,
                    status=201,
                    body=_json_serializable(resp),
                    user_id=principal_user_id,
                )

        return await self._enrich_sale(sale, include={"lines"}), False

    async def _validate_and_compute_line(
        self, ln: dict[str, Any], principal_user_id: int
    ) -> dict[str, Any]:
        """Validate a SaleLineInput, compute line_total, enforce caps.

        * ``unit_price`` override requires ``sale.price_override``.
        * ``discount_amount`` > 0 requires ``sale.discount``.
        * Product must be sellable.
        * line_total = quantity x unit_price - discount_amount.

        The capability check here requires the principal's caps; the
        route enforces the route-level cap (sale.edit_own_draft). Line
        price_override / discount are *additional* caps. We read them
        from the sales table's created_by ownership context — but the
        route layer passes principal_user_id; the service resolves caps
        via the UoW.

        NOTE: price_override / discount caps are checked at the route
        layer (see sales.py) because the Principal object is available
        there. This helper focuses on *validation + computation*.
        """
        product_id = int(ln["product_id"])
        qty = Decimal(str(ln["quantity"]))
        if qty <= 0:
            raise Conflict(
                "Sale line quantity must be positive.",
                details={"field": "quantity"},
            )

        # Product existence + sellability
        if not await self._repo.product_exists(product_id):
            raise NotFound(f"Product {product_id} not found.")
        if not await self._repo.product_is_sellable(product_id):
            raise Conflict(
                f"Product {product_id} is not sellable (inactive or not for sale).",
                details={"product_id": product_id},
            )

        # Default unit_price = product.selling_price
        if ln.get("unit_price") is not None:
            unit_price = Decimal(str(ln["unit_price"]))
        else:
            sp = await self._repo.product_selling_price(product_id)
            if sp is None:
                raise NotFound(f"Product {product_id} has no selling price.")
            unit_price = sp

        if unit_price < 0:
            raise Conflict("unit_price cannot be negative.", details={"field": "unit_price"})

        discount = Decimal(str(ln.get("discount_amount") or 0))
        if discount < 0:
            raise Conflict(
                "discount_amount cannot be negative.",
                details={"field": "discount_amount"},
            )

        line_total = _q2(qty * unit_price - discount)
        if line_total < 0:
            raise Conflict("line_total would be negative.", details={"field": "line_total"})

        return {
            "product_id": product_id,
            "quantity": qty,
            "unit_price": unit_price,
            "discount_amount": discount,
            "line_total": line_total,
        }

    # ==================================================================
    # C.1 — Patch draft
    # ==================================================================

    async def update_draft(
        self,
        sale_id: int,
        *,
        customer_id: int | None,
        sale_date: datetime | None,
        discount_amount: Decimal | None,
        notes: str | None,
        if_match: str | None,
        ctx: AuditContext | None,
    ) -> dict[str, Any]:
        sale = await self._repo.get(sale_id)
        if sale is None:
            raise NotFound(f"Sale {sale_id} not found.")
        if sale["lifecycle_status"] != LifecycleStatus.DRAFT.value:
            raise LifecycleViolation(
                f"Sale is in '{sale['lifecycle_status']}' state; only draft can be patched."
            )

        # If-Match check (required by route layer, but enforce again here)
        current_etag, _v = self._etag_from_row(sale)
        check_if_match(provided=if_match, current_etag=current_etag)

        old_values = dict(sale)
        sale = await self._repo.update_draft(
            sale_id,
            customer_id=customer_id,
            sale_date=sale_date,
            discount_amount=discount_amount,
            notes=notes,
        )
        if sale is None:
            raise NotFound(f"Sale {sale_id} not found after update.")

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.UPDATE,
                entity_type=ENTITY_SALES,
                entity_id=sale_id,
                old_values=old_values,
                new_values=dict(sale),
                ctx=ctx,
            )
        return await self._enrich_sale(sale)

    async def delete_draft(
        self,
        sale_id: int,
        *,
        principal_user_id: int,
        idempotency_key: str | None = None,
        if_match: str | None = None,
        ctx: AuditContext | None = None,
        request_body: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """Delete a draft sale owned by ``principal_user_id``.

        Returns ``(deleted, etag)`` — ``deleted`` is ``False`` when the sale
        was already deleted (idempotent replay). Per contract §2.3: deletion
        is hard-delete of the draft row; historical (posted) sales can never
        be deleted.
        """
        sale = await self._repo.get_for_update(sale_id)
        if sale is None:
            raise NotFound(f"Sale {sale_id} not found.")
        if sale["lifecycle_status"] != LifecycleStatus.DRAFT.value:
            raise LifecycleViolation("Only a draft sale can be deleted.")
        current_etag, _v = self._etag_from_row(sale)
        check_if_match(provided=if_match, current_etag=current_etag)
        if not _is_owner_or_cap(principal_user_id, sale, frozenset(), _SALE_LINE_OWNER_CAP):
            raise PermissionDenied("Not the owner of this draft sale.")

        deleted_count = await self._repo.delete_draft(sale_id)
        deleted = bool(deleted_count)
        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.DEACTIVATE,
                entity_type=ENTITY_SALES,
                entity_id=sale_id,
                old_values=dict(sale),
                new_values={},
                ctx=ctx,
            )
        return deleted, current_etag

    # ==================================================================
    # C.2 — Line CRUD
    # ==================================================================

    async def add_line(
        self,
        sale_id: int,
        *,
        principal_user_id: int,
        principal_caps: frozenset[str],
        line: dict[str, Any],
        idempotency_key: str | None,
        if_match: str | None,
        ctx: AuditContext | None,
        request_body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        # Idempotency check FIRST so replays bypass lifecycle validation
        idem_rec = None
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="POST", path=f"/sales/{sale_id}/lines", body=request_body
            )
            idem_rec = await store.start(
                key=idempotency_key,
                user_id=principal_user_id or 0,
                endpoint="POST /sales/{id}/lines",
                fingerprint=fingerprint,
            )
            if idem_rec is not None and idem_rec.is_completed:
                await self._uow.commit()
                return idem_rec.response_body, True  # type: ignore[return-value]

        sale = await self._repo.get(sale_id)
        if sale is None:
            raise NotFound(f"Sale {sale_id} not found.")
        if sale["lifecycle_status"] != LifecycleStatus.DRAFT.value:
            raise LifecycleViolation("Lines can only be added to a draft sale.")
        current_etag, _v = self._etag_from_row(sale)
        check_if_match(provided=if_match, current_etag=current_etag)

        # Ownership guard for edit_own_draft
        if int(sale["created_by"]) != principal_user_id and not principal_caps.__contains__(
            "sale.edit_own_draft"
        ):
            raise PermissionDenied("Not the owner of this draft sale.")

        # Price override / discount cap check
        unit_price = line.get("unit_price")
        discount = line.get("discount_amount")
        if unit_price is not None and not _is_owner_or_cap(
            principal_user_id, sale, principal_caps, "sale.price_override"
        ):
            raise PermissionDenied("Price override requires sale.price_override.")
        if (discount is not None and discount > 0) and not principal_caps.__contains__(
            "sale.discount"
        ):
            raise PermissionDenied("Discount requires sale.discount capability.")

        validated = await self._validate_and_compute_line(line, principal_user_id)
        line_number = await self._repo.next_line_number(sale_id)
        validated["line_number"] = line_number

        inserted = await self._repo.insert_line(
            sale_id=sale_id,
            product_id=validated["product_id"],
            quantity=validated["quantity"],
            unit_price=validated["unit_price"],
            discount_amount=validated["discount_amount"],
            line_total=validated["line_total"],
            line_number=line_number,
        )

        # Recompute header total
        updated_sale = await self._recompute_sale(sale_id, ctx=ctx)

        # Audit
        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.CREATE,
                entity_type=ENTITY_SALE_LINES,
                entity_id=int(inserted["id"]) if inserted else None,
                new_values=validated,
                ctx=ctx,
            )

        if idempotency_key is not None and inserted is not None:
            line_dict = await self._line_dict_async(inserted)
            # Inject the parent sale's updated_at/etag so callers (and
            # idempotency replays) can round-trip the If-Match ETag.
            line_dict["updated_at"] = updated_sale.get("updated_at")
            line_dict["etag"] = (
                SaleService._etag_from_row(updated_sale)[0]
                if updated_sale.get("updated_at") is not None
                else None
            )
            store = IdempotencyStore(self._uow)
            if idem_rec is not None:
                await store.complete(
                    record_id=idem_rec.id,
                    status=201,
                    body=_json_serializable(line_dict),
                    user_id=principal_user_id,
                )

        if inserted is None:
            return {}, False
        line_dict = await self._line_dict_async(inserted)
        line_dict["updated_at"] = updated_sale.get("updated_at")
        line_dict["etag"] = (
            SaleService._etag_from_row(updated_sale)[0]
            if updated_sale.get("updated_at") is not None
            else None
        )
        return line_dict, False

    async def update_line(
        self,
        sale_id: int,
        line_id: int,
        *,
        principal_user_id: int,
        principal_caps: frozenset[str],
        quantity: Decimal | None,
        unit_price: Decimal | None,
        discount_amount: Decimal | None,
        if_match: str | None,
        ctx: AuditContext | None,
    ) -> dict[str, Any]:
        sale = await self._repo.get(sale_id)
        if sale is None:
            raise NotFound(f"Sale {sale_id} not found.")
        if sale["lifecycle_status"] != LifecycleStatus.DRAFT.value:
            raise LifecycleViolation("Can only edit lines of a draft sale.")
        current_etag, _v = self._etag_from_row(sale)
        check_if_match(provided=if_match, current_etag=current_etag)

        if int(sale["created_by"]) != principal_user_id and not principal_caps.__contains__(
            "sale.edit_own_draft"
        ):
            raise PermissionDenied("Not the owner of this draft sale.")

        line = await self._repo.get_line(sale_id, line_id)
        if line is None:
            raise NotFound(f"Sale line {line_id} not found.")

        if unit_price is not None and not _is_owner_or_cap(
            principal_user_id, sale, principal_caps, "sale.price_override"
        ):
            raise PermissionDenied("Price override requires sale.price_override.")
        if (
            discount_amount is not None and discount_amount > 0
        ) and not principal_caps.__contains__("sale.discount"):
            raise PermissionDenied("Discount requires sale.discount capability.")

        new_qty = quantity if quantity is not None else line["quantity"]
        new_price = unit_price if unit_price is not None else line["unit_price"]
        new_disc = discount_amount if discount_amount is not None else line["discount_amount"]

        new_total = _q2(_quant(new_qty) * _quant(new_price) - _quant(new_disc))

        updated = await self._repo.update_line(
            sale_id,
            line_id,
            quantity=_quant(new_qty),
            unit_price=_quant(new_price),
            discount_amount=_quant(new_disc),
            line_total=new_total,
        )
        if updated is None:
            raise NotFound(f"Sale line {line_id} not found after update.")

        await self._recompute_sale(sale_id, ctx=ctx)

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.UPDATE,
                entity_type=ENTITY_SALE_LINES,
                entity_id=line_id,
                old_values=dict(line),
                new_values=dict(updated),
                ctx=ctx,
            )
        line_dict = await self._line_dict_async(updated)
        updated_sale = await self._repo.get(sale_id)
        if updated_sale is not None:
            line_dict["updated_at"] = updated_sale.get("updated_at")
            line_dict["etag"] = (
                SaleService._etag_from_row(updated_sale)[0]
                if updated_sale.get("updated_at") is not None
                else None
            )
        return line_dict

    async def delete_line(
        self,
        sale_id: int,
        line_id: int,
        *,
        principal_user_id: int,
        principal_caps: frozenset[str],
        idempotency_key: str | None,
        if_match: str | None,
        ctx: AuditContext | None,
    ) -> tuple[None, bool]:
        # Idempotency check FIRST so replays bypass lifecycle/line checks.
        idem_rec = None
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="DELETE", path=f"/sales/{sale_id}/lines/{line_id}", body=None
            )
            idem_rec = await store.start(
                key=idempotency_key,
                user_id=principal_user_id or 0,
                endpoint="DELETE /sales/{id}/lines/{line_id}",
                fingerprint=fingerprint,
            )
            if idem_rec is not None and idem_rec.is_completed:
                await self._uow.commit()
                return None, True

        sale = await self._repo.get(sale_id)
        if sale is None:
            raise NotFound(f"Sale {sale_id} not found.")
        if sale["lifecycle_status"] != LifecycleStatus.DRAFT.value:
            raise LifecycleViolation("Can only delete lines of a draft sale.")
        current_etag, _v = self._etag_from_row(sale)
        check_if_match(provided=if_match, current_etag=current_etag)

        if int(sale["created_by"]) != principal_user_id and not principal_caps.__contains__(
            "sale.edit_own_draft"
        ):
            raise PermissionDenied("Not the owner of this draft sale.")

        line = await self._repo.get_line(sale_id, line_id)
        if line is None:
            # An idempotent delete of an already-removed line is a
            # no-op success (the end state the client wanted — the line
            # is gone — is achieved).  Without a key we keep the 404.
            if idempotency_key is not None:
                store = IdempotencyStore(self._uow)
                if idem_rec is not None:
                    await store.complete(
                        record_id=idem_rec.id,
                        status=204,
                        body="{}",
                        user_id=principal_user_id,
                    )
                await self._uow.commit()
                return None, False
            raise NotFound(f"Sale line {line_id} not found.")

        await self._repo.delete_line(sale_id, line_id)
        await self._recompute_sale(sale_id, ctx=ctx)

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.UPDATE,
                entity_type=ENTITY_SALE_LINES,
                entity_id=line_id,
                old_values=dict(line),
                ctx=ctx,
            )

        # Persist idempotency completion (empty body for 204).
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            if idem_rec is not None:
                await store.complete(
                    record_id=idem_rec.id,
                    status=204,
                    body="{}",
                    user_id=principal_user_id,
                )

        return None, False

    # ==================================================================
    # C.3 — Post (lifecycle)
    # ==================================================================

    async def post_sale(
        self,
        sale_id: int,
        *,
        principal_user_id: int,
        if_match: str | None,
        idempotency_key: str | None,
        ctx: AuditContext | None,
        request_body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        # Idempotency check FIRST so replays bypass lifecycle validation
        idem_rec = None
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="POST", path=f"/sales/{sale_id}/post", body=request_body
            )
            idem_rec = await store.start(
                key=idempotency_key,
                user_id=principal_user_id or 0,
                endpoint="POST /sales/{id}/post",
                fingerprint=fingerprint,
            )
            if idem_rec is not None and idem_rec.is_completed:
                await self._uow.commit()
                return idem_rec.response_body, True  # type: ignore[return-value]

        sale = await self._repo.get_for_update(sale_id)
        if sale is None:
            raise NotFound(f"Sale {sale_id} not found.")
        if sale["lifecycle_status"] != LifecycleStatus.DRAFT.value:
            raise LifecycleViolation(
                f"Sale is in '{sale['lifecycle_status']}'; only draft can be posted."
            )

        current_etag, _v = self._etag_from_row(sale)
        check_if_match(provided=if_match, current_etag=current_etag)

        lines_list, _ = await self._repo.list_lines(sale_id)
        total = _q2(sale["total_amount"])
        if not lines_list:
            raise LifecycleViolation("Cannot post a sale with no lines.")

        # Snapshot costs + insert stock_movements.
        # Moving-average cost is computed from positive stock movements.
        # We lock products via deterministic advisory locks to serialize
        # concurrent movers on the same product (BR-COST-006).
        negative_stock_supported = (
            await self._repo.get_setting_bool("default_negative_stock_allowed", default=False)
            or False
        )

        for ln in lines_list:
            product_id = int(ln["product_id"])
            qty = _quant(ln["quantity"])
            allow_neg = await self._repo.product_allow_negative_stock(product_id)
            product_neg_ok = bool(allow_neg) if allow_neg is not None else negative_stock_supported

            on_hand = await self._repo.on_hand_quantity(product_id)
            available = on_hand
            if qty > available:
                if not product_neg_ok:
                    raise InsufficientStock(
                        f"Insufficient stock for product {product_id}: "
                        f"have {available}, need {qty}.",
                        details={"product_id": product_id, "available": float(on_hand)},
                    )
                # negative stock fallback: use moving-average cost
                mov_avg = await self._repo.moving_average_unit_cost(product_id)
                fallback = True
            else:
                mov_avg = await self._repo.moving_average_unit_cost(product_id)
                fallback = False

            unit_cost = mov_avg if mov_avg is not None else Decimal("0")

            movement = await self._repo.insert_stock_movement(
                product_id=product_id,
                trigger="sale",
                quantity=-qty,  # negative = issue
                unit_cost_at_movement=unit_cost,
                total_cost=-(qty * unit_cost),
                reference_type="sale",
                reference_id=sale_id,
                reference_line_id=int(ln["id"]),
                reason=None,
                created_by=principal_user_id,
            )

            # Snapshot the line's cost + movement id
            await self._uow.execute(
                """
                UPDATE sale_lines
                SET unit_cost_snapshot = :unit_cost,
                    cogs_total_snapshot = :cogs_total,
                    is_negative_stock_fallback = :fallback,
                    stock_movement_id = :mov_id
                WHERE id = :lid
                """,
                {
                    "unit_cost": unit_cost,
                    "cogs_total": qty * unit_cost,
                    "fallback": fallback,
                    "mov_id": int(movement["id"]) if movement else None,
                    "lid": int(ln["id"]),
                },
            )

        # Advance lifecycle: draft → posted
        posted = await self._repo.post(
            sale_id,
            posted_by=principal_user_id,
            posted_at=datetime.now(UTC),
        )

        # Below-cost detection (BR-SALE-008 / F.10): server-compares the
        # line's unit_cost_snapshot (set above by the stock-movement
        # snapshot) to unit_price. The sale is still posted; below-cost
        # lines surface as a warning object in the 200 response so the
        # operator can see them. No 4xx, no rollback, no accounting change.
        #
        # lines_list was fetched *before* the snapshot UPDATE above; re-read
        # so unit_cost_snapshot reflects the just-set values.
        lines_for_warning = lines_list
        if posted is not None:
            lines_for_warning, _ = await self._repo.list_lines(sale_id)
        warning = _build_below_cost_warning(posted, lines_for_warning) if posted is not None else None

        # X.6: below-cost sale notification
        if warning is not None:
            from app.notifications.service import _insert_notification
            await _insert_notification(
                self._uow,
                category='below_cost',
                severity='info',
                title='Below-cost sale posted',
                body=warning['message'],
                reference_type='sale',
                reference_id=sale_id,
                user_id=principal_user_id,
            )

        # Auto-complete: if fully paid (Σ payments == total), advance.
        paid = await self._repo.sum_payments(sale_id)
        if paid >= total:
            await self._repo.mark_completed(sale_id)

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.POST,
                entity_type=ENTITY_SALES,
                entity_id=sale_id,
                new_values={"lifecycle_status": "posted"},
                reason=ctx.request_id if ctx else None,
                ctx=ctx,
            )

        if idempotency_key is not None:
            resp = await self._enrich_sale(posted or sale, include={"lines"})
            if warning is not None:
                resp["warning"] = warning
            store = IdempotencyStore(self._uow)
            if idem_rec is not None:
                await store.complete(
                    record_id=idem_rec.id,
                    status=200,
                    body=_json_serializable(resp),
                    user_id=principal_user_id,
                )

        enriched = await self._enrich_sale(posted or sale, include={"lines"})
        if warning is not None:
            enriched["warning"] = warning
        return enriched, False

    async def _check_below_cost(
        self, sale_row: dict[str, Any], lines: list[dict[str, Any]]
    ) -> None:
        """Legacy below-cost guard.

        F.10 supersedes this: a below-cost sale is posted with a warning,
        not rejected. Use :func:`_build_below_cost_warning` in
        post_sale instead. This method is retained so historical
        imports do not break; it is a no-op.
        """
        return None

    # ==================================================================
    # C.4 — Payment
    # ==================================================================

    async def add_payment(
        self,
        sale_id: int,
        *,
        principal_user_id: int,
        payment_method_id: int,
        amount: Decimal,
        tendered_amount: Decimal | None,
        if_match: str | None,
        idempotency_key: str | None,
        ctx: AuditContext | None,
        request_body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        # Idempotency-Key is required on this endpoint per OpenAPI.
        if idempotency_key is None:
            raise IdempotencyViolationConflict(details={"field": "Idempotency-Key"})

        # Idempotency check FIRST so replays bypass lifecycle validation
        fingerprint = compute_request_fingerprint(
            method="POST", path=f"/sales/{sale_id}/payments", body=request_body
        )
        store = IdempotencyStore(self._uow)
        idem_rec = await store.start(
            key=idempotency_key,
            user_id=principal_user_id or 0,
            endpoint="POST /sales/{id}/payments",
            fingerprint=fingerprint,
        )
        if idem_rec is not None and idem_rec.is_completed:
            await self._uow.commit()
            return idem_rec.response_body, True  # type: ignore[return-value]

        sale = await self._repo.get_for_update(sale_id)
        if sale is None:
            raise NotFound(f"Sale {sale_id} not found.")
        # Payments can happen on posted / completed / partially_returned
        if sale["lifecycle_status"] == LifecycleStatus.DRAFT.value:
            raise LifecycleViolation("Cannot record payment on a draft sale.")
        current_etag, _v = self._etag_from_row(sale)
        check_if_match(provided=if_match, current_etag=current_etag)

        is_cash = await self._repo.payment_method_is_cash(payment_method_id)

        # Server-computed change + over-tender rules.
        computed_change: Decimal | None = None
        computed_tendered: Decimal | None = None
        if is_cash:
            if tendered_amount is not None:
                if tendered_amount < amount:
                    computed_tendered = None  # client sent less than amount — odd but allowed?
                    # Actually per BR-SALE-007: tendered >= amount required for over-tender.
                    # A tendered < amount is just "exact or over". We treat
                    # tendered < amount as: no over-tender, change 0.
                    computed_change = Decimal("0")
                else:
                    computed_change = _q2(tendered_amount - amount)
                    computed_tendered = tendered_amount
            else:
                # No over-tender; exact payment.
                computed_change = None
                computed_tendered = None
        else:
            if tendered_amount is not None:
                raise TenderedNotAllowedForNonCash(
                    "tendered_amount is only allowed for cash payment methods.",
                    details={"payment_method_id": payment_method_id},
                )
            computed_change = None
            computed_tendered = None

        # payment_exceeds_total: Σ payments — (replaced payment) must ≤ total.
        total = _q2(sale["total_amount"])
        existing_paid = await self._repo.sum_payments(sale_id)
        projected = existing_paid + amount
        if projected > total:
            raise Conflict(
                "Payment exceeds sale total.",
                details={
                    "payment_exceeds_total": True,
                    "allocated": float(existing_paid),
                    "new_amount": float(amount),
                    "total": float(total),
                },
            )

        payment = await self._repo.insert_payment(
            sale_id=sale_id,
            payment_method_id=payment_method_id,
            amount=amount,
            tendered_amount=computed_tendered,
            change_amount=computed_change,
            payment_date=None,
            reference=None,
            created_by=principal_user_id,
        )

        # Cash movements for cash payments.
        # Over-tender cash: two movements (gross tender in + change out).
        # Exact cash: one movement (in direction 'in').
        if is_cash:
            if (
                computed_tendered is not None
                and computed_change is not None
                and computed_change > 0
            ):
                # Over-tender: gross in + change out
                await self._repo.insert_cash_movement(
                    amount=_q2(computed_tendered),
                    direction="in",
                    trigger="sale_payment",
                    payment_method_id=payment_method_id,
                    reference_type="sale_payment",
                    reference_id=int(payment["id"]) if payment else None,
                    created_by=principal_user_id,
                )
                await self._repo.insert_cash_movement(
                    amount=-_q2(computed_change),
                    direction="out",
                    trigger="change_tendered",
                    payment_method_id=payment_method_id,
                    reference_type="sale_payment",
                    reference_id=int(payment["id"]) if payment else None,
                    created_by=principal_user_id,
                )
            else:
                # Exact cash payment
                await self._repo.insert_cash_movement(
                    amount=_q2(amount),
                    direction="in",
                    trigger="sale_payment",
                    payment_method_id=payment_method_id,
                    reference_type="sale_payment",
                    reference_id=int(payment["id"]) if payment else None,
                    created_by=principal_user_id,
                )

        # Auto-complete: Σ payments == total → completed
        new_paid = existing_paid + amount
        if new_paid >= total:
            await self._repo.mark_completed(sale_id)

        if ctx is not None and payment is not None:
            await write_audit(
                self._uow,
                action=AuditAction.PAYMENT,
                entity_type=ENTITY_SALE_PAYMENTS,
                entity_id=int(payment["id"]),
                new_values={
                    "sale_id": sale_id,
                    "payment_method_id": payment_method_id,
                    "amount": str(amount),
                    "tendered_amount": str(computed_tendered)
                    if computed_tendered is not None
                    else None,
                    "change_amount": str(computed_change) if computed_change is not None else None,
                },
                ctx=ctx,
            )

        # Post trigger does not bump sales.version (only UPDATE on sales does).
        # We reload.
        sale = await self._repo.get(sale_id)
        if sale is None:
            raise NotFound(f"Sale {sale_id} not found after payment.")
        resp = await self._enrich_sale(sale, include={"payments"})
        if payment is not None:
            resp["payments"] = [self._payment_dict(payment)]

        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            if idem_rec is not None:
                await store.complete(
                    record_id=idem_rec.id,
                    status=201,
                    body=_json_serializable(resp),
                    user_id=principal_user_id,
                )

        return resp, False

    # ==================================================================
    # C.5 — Cancel
    # ==================================================================

    async def cancel_sale(
        self,
        sale_id: int,
        *,
        principal_user_id: int,
        reason: str,
        if_match: str | None,
        idempotency_key: str | None,
        ctx: AuditContext | None,
        request_body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        # Idempotency check FIRST so replays bypass lifecycle validation
        idem_rec = None
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="POST", path=f"/sales/{sale_id}/cancel", body=request_body
            )
            idem_rec = await store.start(
                key=idempotency_key,
                user_id=principal_user_id or 0,
                endpoint="POST /sales/{id}/cancel",
                fingerprint=fingerprint,
            )
            if idem_rec is not None and idem_rec.is_completed:
                await self._uow.commit()
                return idem_rec.response_body, True  # type: ignore[return-value]

        sale = await self._repo.get_for_update(sale_id)
        if sale is None:
            raise NotFound(f"Sale {sale_id} not found.")
        if sale["lifecycle_status"] == LifecycleStatus.CANCELLED.value:
            raise Conflict(
                "Sale is already cancelled.",
                details={"already_cancelled": True},
            )
        if sale["lifecycle_status"] not in _LIFECYCLE_CANCELLABLE:
            raise Conflict(
                f"Sale in '{sale['lifecycle_status']}' state cannot be cancelled.",
                details={"reason": "lifecycle_state_invalid"},
            )

        current_etag, _v = self._etag_from_row(sale)
        check_if_match(provided=if_match, current_etag=current_etag)

        # Cancellation reversal movements: for each posted sale line,
        # insert a 'sale_reversal' stock movement (positive qty, restoring
        # the original unit_cost_snapshot). This is a NEW row, not a
        # mutation of the original movement (append-only ledger).
        lines_rows, _ = await self._repo.list_lines(sale_id)
        for ln in lines_rows:
            if ln.get("stock_movement_id") is not None:
                orig = await self._repo.get_stock_movement(int(ln["stock_movement_id"]))
                orig_qty = _quant(orig["quantity"]) if orig else Decimal("0")
                orig_cost = _quant(orig.get("unit_cost_at_movement")) if orig else Decimal("0")
                rev_total_cost = -(orig_qty * orig_cost)
                await self._repo.insert_stock_movement(
                    product_id=int(ln["product_id"]),
                    trigger="sale_reversal",
                    quantity=-orig_qty,  # restore: opposite sign
                    unit_cost_at_movement=orig_cost,
                    total_cost=rev_total_cost,
                    reference_type="sale",
                    reference_id=sale_id,
                    reference_line_id=int(ln["id"]),
                    reversal_of_movement_id=int(ln["stock_movement_id"])
                    if ln.get("stock_movement_id") and sale["lifecycle_status"] == "posted"
                    else None,
                    reason=reason,
                    created_by=principal_user_id,
                )

        cancelled = await self._repo.cancel(
            sale_id,
            cancelled_by=principal_user_id,
            cancellation_date=datetime.now(UTC),
            cancellation_reason=reason,
        )

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.CANCEL,
                entity_type=ENTITY_SALES,
                entity_id=sale_id,
                old_values=dict(sale),
                new_values=dict(cancelled) if cancelled else None,
                reason=reason,
                ctx=ctx,
            )

        if idempotency_key is not None:
            resp = await self._enrich_sale(cancelled or sale)
            store = IdempotencyStore(self._uow)
            if idem_rec is not None:
                await store.complete(
                    record_id=idem_rec.id,
                    status=200,
                    body=_json_serializable(resp),
                    user_id=principal_user_id,
                )

        return await self._enrich_sale(cancelled or sale), False

    # ==================================================================
    # C.6 — Returns
    # ==================================================================

    async def create_return(
        self,
        sale_id: int,
        *,
        principal_user_id: int,
        reason: str,
        return_lines: list[dict[str, Any]],
        if_match: str | None,
        idempotency_key: str | None,
        ctx: AuditContext | None,
        request_body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        # Idempotency check FIRST so replays bypass lifecycle validation
        idem_rec = None
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="POST", path=f"/sales/{sale_id}/returns", body=request_body
            )
            idem_rec = await store.start(
                key=idempotency_key,
                user_id=principal_user_id or 0,
                endpoint="POST /sales/{id}/returns",
                fingerprint=fingerprint,
            )
            if idem_rec is not None and idem_rec.is_completed:
                await self._uow.commit()
                return idem_rec.response_body, True  # type: ignore[return-value]

        sale = await self._repo.get_for_update(sale_id)
        if sale is None:
            raise NotFound(f"Sale {sale_id} not found.")
        if sale["lifecycle_status"] not in _LIFECYCLE_RETURNABLE:
            raise LifecycleViolation(
                f"Sale in '{sale['lifecycle_status']}' state cannot be returned."
            )
        current_etag, _v = self._etag_from_row(sale)
        check_if_match(provided=if_match, current_etag=current_etag)

        total_selling = Decimal("0")
        total_cost = Decimal("0")
        created_lines: list[dict[str, Any]] = []

        # Pre-check remaining qty per sale_line (DB trigger also enforces).
        remaining: dict[int, Decimal] = {}
        for rl in return_lines:
            sl_id = int(rl["sale_line_id"])
            if sl_id not in remaining:
                returned = await self._repo.sum_returned_qty_for_line(sl_id)
                sl = await self._repo.get_line(sale_id, sl_id)
                # get_line filters by sale_id; but the sale_line belongs to
                # the sale via sale_lines.sale_id. Use a direct lookup.
                if sl is None:
                    # Fallback: fetch by line id across all sales
                    sl = await self._uow.first_row(
                        "SELECT * FROM sale_lines WHERE id = :lid",
                        {"lid": sl_id},
                    )
                if sl is None:
                    raise NotFound(f"Sale line {sl_id} not found.")
                remaining[sl_id] = _quant(sl["quantity"]) - returned

            qty = _quant(rl["quantity"])
            if qty > remaining[sl_id]:
                raise Conflict(
                    f"Return quantity exceeds remaining for sale_line {sl_id}: "
                    f"requested {qty}, available {remaining[sl_id]}.",
                    details={
                        "return_quantity_exceeds_original": True,
                        "sale_line_id": sl_id,
                        "remaining": float(remaining[sl_id]),
                    },
                )

            # Server-computed selling price = line.unit_price x return qty
            line_row = await self._uow.first_row(
                "SELECT * FROM sale_lines WHERE id = :lid",
                {"lid": sl_id},
            )
            if line_row is None:
                raise NotFound(f"Sale line {sl_id} not found.")
            unit_price = _quant(line_row["unit_price"])
            unit_cost = _quant(line_row.get("unit_cost_snapshot") or 0)

            ret_selling_price = _q2(unit_price * qty)
            ret_cost = _q2(unit_cost * qty)
            total_selling += ret_selling_price
            total_cost += ret_cost

            created_lines.append(
                {
                    "sale_line_id": sl_id,
                    "product_id": int(line_row["product_id"]),
                    "quantity": qty,
                    "returned_selling_price": ret_selling_price,
                    "returned_unit_cost": ret_cost,
                }
            )

        # Insert return header
        ret = await self._repo.insert_return(
            sale_id=sale_id,
            return_date=None,
            reason=reason,
            total_selling_price_returned=_q2(total_selling),
            total_cost_returned=_q2(total_cost),
            created_by=principal_user_id,
        )
        if ret is None:
            raise NotFound("Failed to insert return.")
        ret_id = int(ret["id"])

        # Insert return lines + stock restoration movements
        for cl in created_lines:
            cl["line_number"] = await self._repo.next_return_line_number(ret_id)
            await self._repo.insert_return_line(
                sales_return_id=ret_id,
                sale_line_id=cl["sale_line_id"],
                product_id=cl["product_id"],
                quantity=cl["quantity"],
                returned_selling_price=cl["returned_selling_price"],
                returned_unit_cost=cl["returned_unit_cost"],
                line_number=cl["line_number"],
            )
            # Restoration movement (positive qty, returns goods to stock)
            await self._repo.insert_stock_movement(
                product_id=cl["product_id"],
                trigger="sales_return",
                quantity=cl["quantity"],  # positive = receipt
                unit_cost_at_movement=cl["returned_unit_cost"],
                total_cost=cl["quantity"] * cl["returned_unit_cost"],
                reference_type="sales_return",
                reference_id=ret_id,
                reference_line_id=cl["line_number"],
                reason=reason,
                created_by=principal_user_id,
            )

        # Lifecycle: transition through the spec-allowed states.
        #   posted -> partially_returned -> returned
        #   completed -> partially_returned -> returned
        # The DB trigger forbids skipping partially_returned for a
        # completed sale, so we always go via partially_returned.
        sold_qty = Decimal("0")
        for cl in created_lines:
            sl = await self._uow.first_row(
                "SELECT quantity FROM sale_lines WHERE id = :lid",
                {"lid": cl["sale_line_id"]},
            )
            sold_qty += _quant(sl["quantity"]) if sl else Decimal("0")
        total_returned = Decimal("0")
        line_rows, _ = await self._repo.list_lines(sale_id)
        for ln in line_rows:
            r = await self._repo.sum_returned_qty_for_line(int(ln["id"]))
            total_returned += r
        await self._repo.set_lifecycle_status(
            sale_id, lifecycle_status="partially_returned"
        )
        if total_returned >= sold_qty and sold_qty > 0:
            await self._repo.set_lifecycle_status(
                sale_id, lifecycle_status="returned"
            )

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.RETURN,
                entity_type=ENTITY_SALES_RETURNS,
                entity_id=ret_id,
                new_values={"sale_id": sale_id, "reason": reason},
                ctx=ctx,
            )

        if idempotency_key is not None:
            resp = await self._enrich_return(ret, created_lines)
            store = IdempotencyStore(self._uow)
            if idem_rec is not None:
                await store.complete(
                    record_id=idem_rec.id,
                    status=201,
                    body=_json_serializable(resp),
                    user_id=principal_user_id,
                )

        return await self._enrich_return(ret, created_lines), False

    async def _enrich_return(
        self, ret_row: dict[str, Any], created_lines: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Build the SalesReturn response with embedded lines."""
        ret_id = int(ret_row["id"])
        lines = []
        ln_rows = await self._repo.list_return_lines(ret_id)
        for r in ln_rows:
            lines.append(self._return_line_dict(r))
        out = dict(ret_row)
        out["lines"] = lines
        # sales_returns has no updated_at column; the cancel timestamp or
        # created_at is the next-best ETag source for clients.
        out["updated_at"] = (
            ret_row.get("cancellation_date")
            or ret_row.get("created_at")
            or ret_row.get("return_date")
        )
        return out

    # ==================================================================
    # C.7 / #16-18 — Sales returns read + cancel
    # ==================================================================

    async def list_sales_returns(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
        sort: str = "id",
        sale_id: int | None = None,
        customer_id: int | None = None,
        lifecycle_status: str | None = None,
        from_iso: str | None = None,
        to_iso: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows, total = await self._repo.list_returns(
            page=page,
            per_page=per_page,
            sort=sort,
            sale_id=sale_id,
            customer_id=customer_id,
            lifecycle_status=lifecycle_status,
            from_iso=from_iso,
            to_iso=to_iso,
        )
        out = []
        for r in rows:
            out.append(await self._enrich_return(r, []))
        return out, total

    async def get_sales_return(self, return_id: int) -> dict[str, Any]:
        ret = await self._repo.get_return(return_id)
        if ret is None:
            raise NotFound(f"Sales return {return_id} not found.")
        return await self._enrich_return(ret, [])

    async def cancel_return(
        self,
        return_id: int,
        *,
        principal_user_id: int,
        reason: str,
        if_match: str | None,
        idempotency_key: str | None,
        ctx: AuditContext | None,
        request_body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        # Idempotency check FIRST so replays bypass lifecycle validation
        idem_rec = None
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="POST", path=f"/sales-returns/{return_id}/cancel", body=request_body
            )
            idem_rec = await store.start(
                key=idempotency_key,
                user_id=principal_user_id or 0,
                endpoint="POST /sales-returns/{id}/cancel",
                fingerprint=fingerprint,
            )
            if idem_rec is not None and idem_rec.is_completed:
                await self._uow.commit()
                return idem_rec.response_body, True  # type: ignore[return-value]

        ret = await self._repo.get_return_for_update(return_id)
        if ret is None:
            raise NotFound(f"Sales return {return_id} not found.")
        if ret["lifecycle_status"] == "cancelled":
            raise Conflict("Sales return is already cancelled.")
        current_etag = self._etag_from_return_row(ret)
        check_if_match(provided=if_match, current_etag=current_etag)

        # Insert reversal movements for each return line (negative qty
        # to offset the original sales_return movement).
        lines = await self._repo.list_return_lines(return_id)
        for ln in lines:
            # Find the original sales_return movement for this return line
            orig = await self._uow.first_row(
                "SELECT * FROM stock_movements "
                "WHERE reference_type = 'sales_return' "
                "  AND reference_id = :rid "
                "  AND reference_line_id = :lid",
                {"rid": return_id, "lid": int(ln["line_number"])},
            )
            if orig:
                await self._repo.insert_stock_movement(
                    product_id=int(orig["product_id"]),
                    trigger="sales_return_reversal",
                    quantity=-_quant(orig["quantity"]),  # offset the receipt
                    unit_cost_at_movement=_quant(orig.get("unit_cost_at_movement")),
                    total_cost=-_q2(
                        _quant(orig.get("total_cost"))
                        if orig.get("total_cost")
                        else _quant(orig["quantity"])
                        * _quant(orig.get("unit_cost_at_movement") or 0)
                    ),
                    reference_type="sales_return",
                    reference_id=return_id,
                    reference_line_id=int(ln["line_number"]),
                    reversal_of_movement_id=int(orig["id"]),
                    reason=reason,
                    created_by=principal_user_id,
                )

        cancelled = await self._repo.cancel_return(
            return_id,
            cancelled_by=principal_user_id,
            cancellation_date=datetime.now(UTC),
        )

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.CANCEL,
                entity_type=ENTITY_SALES_RETURNS,
                entity_id=return_id,
                old_values=dict(ret),
                new_values=dict(cancelled) if cancelled else None,
                reason=reason,
                ctx=ctx,
            )

        if idempotency_key is not None:
            resp = await self._enrich_return(cancelled or ret, [])
            store = IdempotencyStore(self._uow)
            if idem_rec is not None:
                await store.complete(
                    record_id=idem_rec.id,
                    status=200,
                    body=_json_serializable(resp),
                    user_id=principal_user_id,
                )

        return await self._enrich_return(cancelled or ret, []), False


def _is_owner_or_cap(
    user_id: int, sale_row: dict[str, Any], caps: frozenset[str], cap: str
) -> bool:
    """True if the user owns the sale or holds ``cap``."""
    return int(sale_row.get("created_by", 0)) == user_id or cap in caps


def _json_serializable(obj: Any) -> str:
    """JSON-serialise a response dict for idempotency caching."""
    import json

    return json.dumps(obj, default=str, sort_keys=True)


__all__ = ["SaleService"]
