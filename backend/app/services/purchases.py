"""Business logic for the Purchases lifecycle (M4 / Phase D).

Per ``Backend-Architecture-V1.0.md`` + ``openapi.yaml`` §13, the service
is the transaction boundary. Every lifecycle mutation (post, pay,
cancel, return) runs inside one ``UnitOfWork`` so the business mutation
+ idempotency entry + audit log share one ``COMMIT``.

Purchase lifecycle (per PRD §13 + BR-PURCHASE-002):
    draft -> posted -> completed | partially_returned | returned | cancelled
    partially_returned -> returned | cancelled
    returned -> cancelled
    completed -> partially_returned | cancelled

DB triggers enforce the hard invariants:
* ``fn_purchase_lifecycle_terminal`` — illegal transitions rejected.
* ``fn_purchase_payment_allocation_bound`` — Σ payments ≤ total.
* ``fn_prl_quantity_bound`` — Σ return quantities ≤ original qty.
* ``fn_bump_version_and_updated_at`` on purchases / purchase_returns.
* ``fn_stock_movements_immutable`` / ``fn_cash_movements_immutable``.
* ``fn_cash_movements_balance_check`` — cash ≥ 0.

The service does **pre-checks** before the DB sees the mutation, so we
return clean, documented 4xx/409 error codes (with the right ``code``
field) instead of opaque constraint violations. The DB trigger is the
final backstop for concurrency races.

Differences vs Sales (B.8):
* ``purchases.total_amount`` is **derived** (``Σ line_total + shipping``)
  — not stored on the header. We build it server-side on every read.
* Shipping is **capitalized into landed cost** (BR-PURCHASE-005) — the
  post-time stock movement uses ``(line_subtotal + allocated_shipping)``
  as the unit cost basis.
* Supplier is optional (counter/cash buys per OD-15).
* Payment over-tender is supported (cash methods only) and allocates
  against the purchase's outstanding (AP). The DB trigger is the
  authoritative ceiling; we pre-check for clean errors.
* Cancellation is the **same lifecycle** transition as B.8 sales, but
  the stock reversal logic splits on-hand vs consumed (BR-PURCHASE-007
  + ST-3): the on-hand portion is reversed via ``purchase_reversal``;
  the consumed portion (units already issued by sales/production) is
  reversed via ``value_adjustment`` (qty=0, total_cost = -qty*cost).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.audit.service import (
    ENTITY_PURCHASE_LINES,
    ENTITY_PURCHASE_PAYMENTS,
    ENTITY_PURCHASE_RETURNS,
    ENTITY_PURCHASE_SHIPPING,
    ENTITY_PURCHASES,
    AuditContext,
    write_audit,
)
from app.concurrency.etag import check_if_match
from app.concurrency.master_etag import etag_and_version_from_updated_at
from app.db import UnitOfWork
from app.errors import (
    AllocationExceedsPayable,
    Conflict,
    InsufficientCash,
    LifecycleViolation,
    NotFound,
    TenderedNotAllowedForNonCash,
)
from app.logging import get_logger
from app.repositories.purchases import PurchaseRepository
from app.services.idempotency import (
    IdempotencyStore,
    compute_request_fingerprint,
)
from app.validation.enums import AuditAction, LifecycleStatus

logger = get_logger(__name__)


# Canonical lifecycle set for purchases (DB CHECK + OpenAPI).
_LIFECYCLE_TERMINAL = frozenset({"cancelled"})
_LIFECYCLE_POSTABLE = frozenset({"draft"})
_LIFECYCLE_CANCELLABLE = frozenset(
    {"draft", "posted", "completed", "partially_returned", "returned"}
)
_LIFECYCLE_RETURNABLE = frozenset(
    {"posted", "completed", "partially_returned"}
)


# PostgreSQL unique_violation SQLSTATE — mirrors app/services/contacts.py.
_PG_UNIQUE_VIOLATION = "23505"


def _is_unique_violation(exc: Exception) -> bool:
    """True when ``exc`` is a PG unique_violation.

    Mirrors the helper in ``app/services/contacts.py`` so the purchases
    service can surface a clean 409 instead of a raw IntegrityError.
    """
    orig = getattr(exc, "orig", None)
    sqlstate = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
    if sqlstate is not None:
        return str(sqlstate) == _PG_UNIQUE_VIOLATION
    return "duplicate key value" in str(exc).lower()


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


def _is_owner_or_cap(
    user_id: int, purchase_row: dict[str, Any], caps: frozenset[str], cap: str
) -> bool:
    return int(purchase_row.get("created_by", 0)) == user_id or cap in caps


def _json_serializable(obj: Any) -> str:
    import json

    return json.dumps(obj, default=str, sort_keys=True)


class PurchaseService:
    """Business logic for the purchases lifecycle + derived fields."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow
        self._repo = PurchaseRepository(uow)

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
    def _payment_state(total: Decimal, paid: Decimal) -> tuple[str, Decimal, Decimal]:
        paid = paid if paid > 0 else Decimal("0")
        outstanding = max(Decimal("0"), total - paid)
        if paid == 0:
            return "unpaid", paid, outstanding
        if paid < total:
            return "partial", paid, outstanding
        return "paid", paid, outstanding

    async def _purchase_total(self, purchase_id: int) -> Decimal:
        """Authoritative purchase total.

        ``purchase_lines.line_total`` ALREADY includes the line's
        ``allocated_shipping`` (``_recompute_shipping_allocation`` sets
        ``line_total = line_subtotal + allocated_shipping``), and the
        allocation always totals ``purchase_shipping.amount``. So the
        total is simply ``Σ line_total`` — adding ``shipping.amount``
        again would double-count the shipping. When there is shipping
        but no lines yet, fall back to the shipping amount so a
        header-only draft still reports its landed cost.
        """
        line_total = await self._repo.sum_line_total(purchase_id)
        shipping = await self._repo.get_shipping(purchase_id)
        ship_amount = _quant(shipping["amount"]) if shipping else Decimal("0")
        if line_total == 0:
            return _q2(ship_amount)
        return _q2(line_total)

    async def _recompute_shipping_allocation(
        self, purchase_id: int
    ) -> None:
        """Pro-rata allocate shipping amount to purchase_lines.allocated_shipping
        and update each line's line_total = line_subtotal + allocated_shipping.

        Called after line insert/update/delete and after shipping upsert.
        """
        shipping = await self._repo.get_shipping(purchase_id)
        ship_amount = _quant(shipping["amount"]) if shipping else Decimal("0")
        subtotals = await self._repo.sum_line_subtotal(purchase_id)
        if subtotals <= 0:
            # Edge: no lines or zero subtotal. Drop allocation to 0.
            lines, _ = await self._repo.list_lines(purchase_id, per_page=10_000)
            for ln in lines:
                await self._uow.execute(
                    "UPDATE purchase_lines SET allocated_shipping = 0, "
                    "line_total = line_subtotal WHERE id = :id",
                    {"id": int(ln["id"])},
                )
            return
        lines, _ = await self._repo.list_lines(purchase_id, per_page=10_000)
        # Pro-rata by subtotal share; cents floor/round on the last line.
        allocated_remaining = ship_amount
        n = len(lines)
        for idx, ln in enumerate(lines):
            sub = _quant(ln["line_subtotal"])
            share = _q2(ship_amount * sub / subtotals) if subtotals > 0 else Decimal("0")
            if idx == n - 1:
                # Last line absorbs rounding remainder.
                share = _q2(allocated_remaining)
            else:
                allocated_remaining = _q2(allocated_remaining - share)
            new_line_total = _q2(sub + share)
            await self._uow.execute(
                "UPDATE purchase_lines SET allocated_shipping = :a, "
                "line_total = :t WHERE id = :id",
                {"a": share, "t": new_line_total, "id": int(ln["id"])},
            )

    # ------------------------------------------------------------------
    # Enrichment / response shaping
    # ------------------------------------------------------------------

    async def _enrich_purchase(
        self,
        row: dict[str, Any],
        *,
        with_lines: Sequence[dict[str, Any]] | None = None,
        with_payments: Sequence[dict[str, Any]] | None = None,
        with_returns: Sequence[dict[str, Any]] | None = None,
        with_shipping: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        purchase_id = int(row["id"])
        total = await self._purchase_total(purchase_id)
        paid = await self._repo.sum_payments(purchase_id)
        state, paid_amt, outstanding = self._payment_state(total, paid)
        # SREC = supplier receivable = outstanding (same number as AP).
        # supplier_repayments.received_amount is the cash-in from the
        # supplier; outstanding - Σ received_amount = remaining SREC
        # that the supplier still owes. For our purposes, the derived
        # ap / supplier_receivable both equal outstanding (the DB does
        # not store AP — it is derived from payments + repayments).
        srec_received = await self._repo.sum_supplier_repayments_for_purchase(
            purchase_id
        )
        ap = _q2(outstanding)
        srec = _q2(max(Decimal("0"), ap - srec_received))

        etag, _v = self._etag_from_row(row)

        enriched = dict(row)
        enriched["total_amount"] = float(total)
        enriched["payment_state"] = state
        enriched["paid_amount"] = paid_amt
        enriched["outstanding"] = outstanding
        enriched["ap"] = ap
        enriched["supplier_receivable"] = srec
        enriched["version"] = int(row["version"]) if row.get("version") is not None else 1
        enriched["etag"] = etag

        if with_lines is not None:
            enriched["lines"] = [self._line_dict(r) for r in with_lines]
        if with_payments is not None:
            enriched["payments"] = [self._payment_dict(r) for r in with_payments]
        if with_returns is not None:
            enriched["returns"] = [
                await self._return_dict(r) for r in with_returns
            ]
        if with_shipping is not None:
            enriched["shipping"] = self._shipping_dict(with_shipping)
        return enriched

    @staticmethod
    def _line_dict(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(r["id"]),
            "purchase_id": int(r["purchase_id"]),
            "product_id": int(r["product_id"]),
            "quantity": float(r["quantity"]),
            "unit_price": float(r["unit_price"]),
            "line_subtotal": float(r["line_subtotal"]),
            "allocated_shipping": float(r["allocated_shipping"]),
            "line_total": float(r["line_total"]),
            "line_number": int(r["line_number"]),
        }

    @staticmethod
    def _shipping_dict(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(r["id"]),
            "purchase_id": int(r["purchase_id"]),
            "amount": float(r["amount"]),
            "paid_in_cash": bool(r["paid_in_cash"]),
            "supplier_id": r.get("supplier_id"),
            "description": r.get("description"),
        }

    @staticmethod
    def _payment_dict(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(r["id"]),
            "purchase_id": int(r["purchase_id"]),
            "payment_method_id": int(r["payment_method_id"]),
            "amount": float(r["amount"]),
            "tendered_amount": (
                float(r["tendered_amount"])
                if r.get("tendered_amount") is not None
                else None
            ),
            "change_amount": (
                float(r["change_amount"])
                if r.get("change_amount") is not None
                else None
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
            "purchase_id": int(r["purchase_id"]),
            "return_date": r["return_date"],
            "reason": r.get("reason"),
            "total_value_returned": float(r["total_value_returned"]),
            "lifecycle_status": str(r["lifecycle_status"]),
            "lines": [self._return_line_dict(ln) for ln in lines],
            "created_at": r["created_at"],
            "created_by": int(r["created_by"]),
            "updated_at": r.get("created_at") or r.get("return_date"),
        }

    @staticmethod
    def _return_line_dict(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(r["id"]),
            "purchase_return_id": int(r["purchase_return_id"]),
            "purchase_line_id": int(r["purchase_line_id"]),
            "product_id": int(r["product_id"]),
            "quantity": float(r["quantity"]),
            "unit_cost_snapshot": float(r["unit_cost_snapshot"]),
            "line_value": float(r["line_value"]),
            "line_number": int(r["line_number"]),
        }

    # ==================================================================
    # D.2 — List / Get / Create / Patch / Delete draft
    # ==================================================================

    async def list_purchases(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
        q: str | None = None,
        sort: str = "id",
        lifecycle_status: str | None = None,
        supplier_id: int | None = None,
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
            supplier_id=supplier_id,
            payment_state=None,  # filtered client-side in service
            from_iso=from_iso,
            to_iso=to_iso,
        )
        enriched: list[dict[str, Any]] = []
        for r in rows:
            e = await self._enrich_purchase(r)
            if payment_state is not None and e["payment_state"] != payment_state:
                continue
            enriched.append(e)
        if payment_state is not None:
            total = len(enriched)
        return enriched, total

    async def get_purchase(
        self,
        purchase_id: int,
        *,
        include: set[str] | None = None,
    ) -> dict[str, Any]:
        row = await self._repo.get(purchase_id)
        if row is None:
            raise NotFound(f"Purchase {purchase_id} not found.")
        inc = include or set()
        with_lines = None
        with_payments = None
        with_returns = None
        if "lines" in inc:
            lines, _ = await self._repo.list_lines(purchase_id, per_page=10_000)
            with_lines = lines
        if "payments" in inc:
            pays, _ = await self._repo.list_payments(purchase_id, per_page=10_000)
            with_payments = pays
        if "returns" in inc:
            with_returns = await self._repo.list_returns_for_purchase(purchase_id)
        ship = await self._repo.get_shipping(purchase_id)
        return await self._enrich_purchase(
            row,
            with_lines=with_lines,
            with_payments=with_payments,
            with_returns=with_returns,
            with_shipping=ship,
        )

    async def create_draft(
        self,
        *,
        principal_user_id: int,
        supplier_id: int | None,
        purchase_date: datetime | None,
        notes: str | None,
        reference_no: str | None,
        lines: list[dict[str, Any]],
        shipping: dict[str, Any] | None,
        ctx: AuditContext | None,
        idempotency_key: str | None,
        request_body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        idem_rec = None
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="POST", path="/purchases", body=request_body
            )
            idem_rec = await store.start(
                key=idempotency_key,
                user_id=principal_user_id or 0,
                endpoint="POST /purchases",
                fingerprint=fingerprint,
            )
            if idem_rec is not None and idem_rec.is_completed:
                await self._uow.commit()
                return idem_rec.response_body, True  # type: ignore[return-value]

        # Validate supplier FK
        if supplier_id is not None:
            row = await self._uow.first_row(
                "SELECT 1 FROM contacts WHERE id = :id AND type IN ('supplier','both')",
                {"id": supplier_id},
            )
            if row is None:
                raise NotFound(
                    f"Supplier contact {supplier_id} not found or not a supplier."
                )

        # Pre-validate lines + compute line_subtotal
        validated: list[dict[str, Any]] = []
        for ln in lines:
            pid = int(ln["product_id"])
            row = await self._uow.first_row(
                "SELECT id FROM products WHERE id = :id", {"id": pid}
            )
            if row is None:
                raise NotFound(f"Product {pid} not found.")
            qty = _quant(ln["quantity"])
            if qty <= 0:
                raise Conflict(
                    "Purchase line quantity must be positive.",
                    details={"field": "quantity"},
                )
            unit_price = _quant(ln["unit_price"])
            if unit_price < 0:
                raise Conflict(
                    "Purchase line unit_price cannot be negative.",
                    details={"field": "unit_price"},
                )
            line_subtotal = _q2(qty * unit_price)
            validated.append(
                {
                    "product_id": pid,
                    "quantity": qty,
                    "unit_price": unit_price,
                    "line_subtotal": line_subtotal,
                }
            )

        try:
            purchase = await self._repo.create_draft(
                supplier_id=supplier_id,
                purchase_date=purchase_date,
                notes=notes,
                reference_no=reference_no,
                created_by=principal_user_id,
            )
        except Exception as exc:
            if _is_unique_violation(exc):
                raise Conflict(
                    "A purchase with this reference number already exists.",
                    details={"field": "reference_no", "value_type": "conflict"},
                ) from exc
            raise
        if purchase is None:
            raise Conflict(
                "Purchase could not be created.",
                details={"field": "reference_no", "value_type": "conflict"},
            )
        purchase_id = int(purchase["id"])

        # Insert lines (allocated_shipping and line_total start at 0 /
        # line_subtotal; recomputed below after shipping).
        inserted_lines: list[dict[str, Any]] = []
        for v in validated:
            ln_no = await self._repo.next_line_number(purchase_id)
            row = await self._repo.insert_line(
                purchase_id=purchase_id,
                product_id=v["product_id"],
                quantity=v["quantity"],
                unit_price=v["unit_price"],
                line_subtotal=v["line_subtotal"],
                allocated_shipping=Decimal("0"),
                line_total=v["line_subtotal"],
                line_number=ln_no,
            )
            if row is not None:
                inserted_lines.append(row)

        # Insert shipping + allocate
        if shipping is not None:
            await self._repo.upsert_shipping(
                purchase_id=purchase_id,
                amount=_quant(shipping.get("amount") or 0),
                paid_in_cash=bool(shipping.get("paid_in_cash", False)),
                supplier_id=shipping.get("supplier_id"),
                description=shipping.get("description"),
            )
            await self._recompute_shipping_allocation(purchase_id)

        ship_row = await self._repo.get_shipping(purchase_id)
        lines_now, _ = await self._repo.list_lines(purchase_id, per_page=10_000)

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.CREATE,
                entity_type=ENTITY_PURCHASES,
                entity_id=purchase_id,
                new_values={
                    "supplier_id": supplier_id,
                    "purchase_date": (
                        purchase_date.isoformat() if purchase_date else None
                    ),
                    "notes": notes,
                    "reference_no": reference_no,
                    "lines": [
                        {
                            "product_id": v["product_id"],
                            "quantity": str(v["quantity"]),
                            "unit_price": str(v["unit_price"]),
                            "line_subtotal": str(v["line_subtotal"]),
                        }
                        for v in validated
                    ],
                    "shipping": (
                        {
                            "amount": str(shipping.get("amount") or 0),
                            "paid_in_cash": bool(shipping.get("paid_in_cash", False)),
                        }
                        if shipping
                        else None
                    ),
                },
                ctx=ctx,
            )

        enriched = await self._enrich_purchase(
            purchase,
            with_lines=lines_now,
            with_shipping=ship_row,
        )

        if idempotency_key is not None and idem_rec is not None:
            store = IdempotencyStore(self._uow)
            await store.complete(
                record_id=idem_rec.id,
                status=201,
                body=_json_serializable(enriched),
                user_id=principal_user_id,
            )

        return enriched, False

    async def update_draft(
        self,
        purchase_id: int,
        *,
        supplier_id: int | None,
        purchase_date: datetime | None,
        notes: str | None,
        if_match: str | None,
        ctx: AuditContext | None,
    ) -> dict[str, Any]:
        purchase = await self._repo.get(purchase_id)
        if purchase is None:
            raise NotFound(f"Purchase {purchase_id} not found.")
        if purchase["lifecycle_status"] != LifecycleStatus.DRAFT.value:
            raise LifecycleViolation(
                f"Purchase is in '{purchase['lifecycle_status']}' state; "
                "only draft can be patched."
            )
        current_etag, _v = self._etag_from_row(purchase)
        check_if_match(provided=if_match, current_etag=current_etag)
        old_values = dict(purchase)
        purchase = await self._repo.update_draft(
            purchase_id,
            supplier_id=supplier_id,
            purchase_date=purchase_date,
            notes=notes,
        )
        if purchase is None:
            raise NotFound(f"Purchase {purchase_id} not found after update.")
        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.UPDATE,
                entity_type=ENTITY_PURCHASES,
                entity_id=purchase_id,
                old_values=old_values,
                new_values=dict(purchase),
                ctx=ctx,
            )
        return await self._enrich_purchase(purchase)

    async def delete_draft(
        self,
        purchase_id: int,
        *,
        principal_user_id: int,
        idempotency_key: str | None,
        if_match: str | None,
        ctx: AuditContext | None,
        request_body: dict[str, Any] | None,
    ) -> tuple[bool, str]:
        idem_rec = None
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="DELETE", path=f"/purchases/{purchase_id}", body=request_body
            )
            idem_rec = await store.start(
                key=idempotency_key,
                user_id=principal_user_id or 0,
                endpoint="DELETE /purchases/{id}",
                fingerprint=fingerprint,
            )
            if idem_rec is not None and idem_rec.is_completed:
                await self._uow.commit()
                return False, ""

        purchase = await self._repo.get_for_update(purchase_id)
        if purchase is None:
            raise NotFound(f"Purchase {purchase_id} not found.")
        if purchase["lifecycle_status"] != LifecycleStatus.DRAFT.value:
            raise LifecycleViolation("Only a draft purchase can be deleted.")
        current_etag, _v = self._etag_from_row(purchase)
        check_if_match(provided=if_match, current_etag=current_etag)

        deleted = await self._repo.delete_draft(purchase_id)

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.DEACTIVATE,
                entity_type=ENTITY_PURCHASES,
                entity_id=purchase_id,
                old_values=dict(purchase),
                new_values={},
                ctx=ctx,
            )

        if idempotency_key is not None and idem_rec is not None:
            store = IdempotencyStore(self._uow)
            await store.complete(
                record_id=idem_rec.id,
                status=204,
                body="{}",
                user_id=principal_user_id,
            )
        return bool(deleted), current_etag

    # ==================================================================
    # D.3 — Lines CRUD
    # ==================================================================

    async def list_lines(self, purchase_id: int) -> list[dict[str, Any]]:
        purchase = await self._repo.get(purchase_id)
        if purchase is None:
            raise NotFound(f"Purchase {purchase_id} not found.")
        lines, _ = await self._repo.list_lines(purchase_id, per_page=10_000)
        return [self._line_dict(r) for r in lines]

    async def add_line(
        self,
        purchase_id: int,
        *,
        principal_user_id: int,
        line: dict[str, Any],
        idempotency_key: str | None,
        if_match: str | None,
        ctx: AuditContext | None,
        request_body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        idem_rec = None
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="POST",
                path=f"/purchases/{purchase_id}/lines",
                body=request_body,
            )
            idem_rec = await store.start(
                key=idempotency_key,
                user_id=principal_user_id or 0,
                endpoint="POST /purchases/{id}/lines",
                fingerprint=fingerprint,
            )
            if idem_rec is not None and idem_rec.is_completed:
                await self._uow.commit()
                return idem_rec.response_body, True  # type: ignore[return-value]

        purchase = await self._repo.get(purchase_id)
        if purchase is None:
            raise NotFound(f"Purchase {purchase_id} not found.")
        if purchase["lifecycle_status"] != LifecycleStatus.DRAFT.value:
            raise LifecycleViolation("Lines can only be added to a draft purchase.")
        current_etag, _v = self._etag_from_row(purchase)
        check_if_match(provided=if_match, current_etag=current_etag)

        pid = int(line["product_id"])
        row = await self._uow.first_row(
            "SELECT id FROM products WHERE id = :id", {"id": pid}
        )
        if row is None:
            raise NotFound(f"Product {pid} not found.")
        qty = _quant(line["quantity"])
        if qty <= 0:
            raise Conflict(
                "Purchase line quantity must be positive.",
                details={"field": "quantity"},
            )
        unit_price = _quant(line["unit_price"])
        if unit_price < 0:
            raise Conflict(
                "unit_price cannot be negative.",
                details={"field": "unit_price"},
            )
        line_subtotal = _q2(qty * unit_price)

        ln_no = await self._repo.next_line_number(purchase_id)
        inserted = await self._repo.insert_line(
            purchase_id=purchase_id,
            product_id=pid,
            quantity=qty,
            unit_price=unit_price,
            line_subtotal=line_subtotal,
            allocated_shipping=Decimal("0"),
            line_total=line_subtotal,
            line_number=ln_no,
        )

        # Re-allocate shipping across all lines (pro-rata).
        await self._recompute_shipping_allocation(purchase_id)

        # Bump the parent so ETag changes for the next caller.
        bumped = await self._repo.get(purchase_id)
        if bumped is None:
            raise NotFound("Purchase missing after line add.")
        new_etag, _v = self._etag_from_row(bumped)

        if ctx is not None and inserted is not None:
            await write_audit(
                self._uow,
                action=AuditAction.CREATE,
                entity_type=ENTITY_PURCHASE_LINES,
                entity_id=int(inserted["id"]),
                new_values={
                    "product_id": pid,
                    "quantity": str(qty),
                    "unit_price": str(unit_price),
                    "line_subtotal": str(line_subtotal),
                },
                ctx=ctx,
            )

        line_dict = self._line_dict(inserted) if inserted else {}
        line_dict["updated_at"] = bumped.get("updated_at")
        line_dict["etag"] = new_etag

        if idempotency_key is not None and idem_rec is not None:
            store = IdempotencyStore(self._uow)
            await store.complete(
                record_id=idem_rec.id,
                status=201,
                body=_json_serializable(line_dict),
                user_id=principal_user_id,
            )

        return line_dict, False

    async def update_line(
        self,
        purchase_id: int,
        line_id: int,
        *,
        principal_user_id: int,
        quantity: Decimal | None,
        unit_price: Decimal | None,
        if_match: str | None,
        ctx: AuditContext | None,
    ) -> dict[str, Any]:
        purchase = await self._repo.get(purchase_id)
        if purchase is None:
            raise NotFound(f"Purchase {purchase_id} not found.")
        if purchase["lifecycle_status"] != LifecycleStatus.DRAFT.value:
            raise LifecycleViolation("Can only edit lines of a draft purchase.")
        current_etag, _v = self._etag_from_row(purchase)
        check_if_match(provided=if_match, current_etag=current_etag)

        line = await self._repo.get_line(purchase_id, line_id)
        if line is None:
            raise NotFound(f"Purchase line {line_id} not found.")

        new_qty = quantity if quantity is not None else _quant(line["quantity"])
        new_price = unit_price if unit_price is not None else _quant(line["unit_price"])
        new_subtotal = _q2(new_qty * new_price)

        updated = await self._repo.update_line(
            purchase_id,
            line_id,
            quantity=new_qty,
            unit_price=new_price,
        )
        if updated is None:
            raise NotFound(f"Purchase line {line_id} not found after update.")

        # Reset line_subtotal + re-allocate shipping + recompute line_total.
        await self._uow.execute(
            "UPDATE purchase_lines SET line_subtotal = :s WHERE id = :id",
            {"s": new_subtotal, "id": line_id},
        )
        await self._recompute_shipping_allocation(purchase_id)

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.UPDATE,
                entity_type=ENTITY_PURCHASE_LINES,
                entity_id=line_id,
                old_values=dict(line),
                new_values=dict(updated),
                ctx=ctx,
            )

        # Reload the line post-allocation.
        line_now = await self._repo.get_line(purchase_id, line_id)
        bumped = await self._repo.get(purchase_id)
        if bumped is None:
            raise NotFound("Purchase missing after line update.")
        new_etag, _v = self._etag_from_row(bumped)
        line_dict = self._line_dict(line_now or updated)
        line_dict["updated_at"] = bumped.get("updated_at")
        line_dict["etag"] = new_etag
        return line_dict

    async def delete_line(
        self,
        purchase_id: int,
        line_id: int,
        *,
        principal_user_id: int,
        idempotency_key: str | None,
        if_match: str | None,
        ctx: AuditContext | None,
    ) -> tuple[None, bool]:
        idem_rec = None
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="DELETE",
                path=f"/purchases/{purchase_id}/lines/{line_id}",
                body=None,
            )
            idem_rec = await store.start(
                key=idempotency_key,
                user_id=principal_user_id or 0,
                endpoint="DELETE /purchases/{id}/lines/{line_id}",
                fingerprint=fingerprint,
            )
            if idem_rec is not None and idem_rec.is_completed:
                await self._uow.commit()
                return None, True

        purchase = await self._repo.get(purchase_id)
        if purchase is None:
            raise NotFound(f"Purchase {purchase_id} not found.")
        if purchase["lifecycle_status"] != LifecycleStatus.DRAFT.value:
            raise LifecycleViolation("Can only delete lines of a draft purchase.")
        current_etag, _v = self._etag_from_row(purchase)
        check_if_match(provided=if_match, current_etag=current_etag)

        line = await self._repo.get_line(purchase_id, line_id)
        if line is None:
            if idempotency_key is not None and idem_rec is not None:
                store = IdempotencyStore(self._uow)
                await store.complete(
                    record_id=idem_rec.id,
                    status=204,
                    body="{}",
                    user_id=principal_user_id,
                )
                await self._uow.commit()
            else:
                raise NotFound(f"Purchase line {line_id} not found.")
            return None, False

        await self._repo.delete_line(purchase_id, line_id)
        # Renumber remaining lines to keep them contiguous starting at 1.
        await self._uow.execute(
            "UPDATE purchase_lines SET line_number = sub.rn "
            "FROM (SELECT id, ROW_NUMBER() OVER (ORDER BY line_number) AS rn "
            "FROM purchase_lines WHERE purchase_id = :id) sub "
            "WHERE purchase_lines.id = sub.id",
            {"id": purchase_id},
        )
        await self._recompute_shipping_allocation(purchase_id)

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.UPDATE,
                entity_type=ENTITY_PURCHASE_LINES,
                entity_id=line_id,
                old_values=dict(line),
                ctx=ctx,
            )

        if idempotency_key is not None and idem_rec is not None:
            store = IdempotencyStore(self._uow)
            await store.complete(
                record_id=idem_rec.id,
                status=204,
                body="{}",
                user_id=principal_user_id,
            )

        return None, False

    # ==================================================================
    # D.4 — Shipping
    # ==================================================================

    async def set_shipping(
        self,
        purchase_id: int,
        *,
        principal_user_id: int,
        amount: Decimal,
        paid_in_cash: bool,
        supplier_id: int | None,
        description: str | None,
        if_match: str | None,
        idempotency_key: str | None,
        ctx: AuditContext | None,
        request_body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        idem_rec = None
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="POST",
                path=f"/purchases/{purchase_id}/shipping",
                body=request_body,
            )
            idem_rec = await store.start(
                key=idempotency_key,
                user_id=principal_user_id or 0,
                endpoint="POST /purchases/{id}/shipping",
                fingerprint=fingerprint,
            )
            if idem_rec is not None and idem_rec.is_completed:
                await self._uow.commit()
                return idem_rec.response_body, True  # type: ignore[return-value]

        purchase = await self._repo.get(purchase_id)
        if purchase is None:
            raise NotFound(f"Purchase {purchase_id} not found.")
        if purchase["lifecycle_status"] != LifecycleStatus.DRAFT.value:
            raise LifecycleViolation(
                "Shipping can only be set on a draft purchase."
            )
        current_etag, _v = self._etag_from_row(purchase)
        check_if_match(provided=if_match, current_etag=current_etag)

        if amount < 0:
            raise Conflict(
                "Shipping amount cannot be negative.",
                details={"field": "amount"},
            )
        existing = await self._repo.get_shipping(purchase_id)
        old_values = dict(existing) if existing else None

        ship = await self._repo.upsert_shipping(
            purchase_id=purchase_id,
            amount=amount,
            paid_in_cash=paid_in_cash,
            supplier_id=supplier_id,
            description=description,
        )
        if ship is None:
            raise NotFound("Shipping insert failed.")
        await self._recompute_shipping_allocation(purchase_id)

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.UPDATE,
                entity_type=ENTITY_PURCHASE_SHIPPING,
                entity_id=int(ship["id"]),
                old_values=old_values,
                new_values=dict(ship),
                ctx=ctx,
            )

        # Bump parent so ETag rotates for the next caller.
        bumped = await self._repo.get(purchase_id)
        if bumped is None:
            raise NotFound("Purchase missing after shipping set.")
        new_etag, _v = self._etag_from_row(bumped)
        ship_dict = self._shipping_dict(ship)
        ship_dict["updated_at"] = bumped.get("updated_at")
        ship_dict["etag"] = new_etag

        if idempotency_key is not None and idem_rec is not None:
            store = IdempotencyStore(self._uow)
            await store.complete(
                record_id=idem_rec.id,
                status=200,
                body=_json_serializable(ship_dict),
                user_id=principal_user_id,
            )

        return ship_dict, False

    async def update_shipping(
        self,
        purchase_id: int,
        *,
        principal_user_id: int,
        amount: Decimal,
        paid_in_cash: bool,
        supplier_id: int | None,
        description: str | None,
        if_match: str | None,
        ctx: AuditContext | None,
    ) -> dict[str, Any]:
        # Same logic as set — upsert by UNIQUE(purchase_id). The
        # OpenAPI declares POST and PATCH; both produce the same row.
        purchase = await self._repo.get(purchase_id)
        if purchase is None:
            raise NotFound(f"Purchase {purchase_id} not found.")
        if purchase["lifecycle_status"] != LifecycleStatus.DRAFT.value:
            raise LifecycleViolation(
                "Shipping can only be edited on a draft purchase."
            )
        current_etag, _v = self._etag_from_row(purchase)
        check_if_match(provided=if_match, current_etag=current_etag)
        existing = await self._repo.get_shipping(purchase_id)
        if existing is None:
            raise NotFound("No shipping row to update; use setPurchaseShipping.")
        if amount < 0:
            raise Conflict(
                "Shipping amount cannot be negative.",
                details={"field": "amount"},
            )
        old_values = dict(existing)
        ship = await self._repo.upsert_shipping(
            purchase_id=purchase_id,
            amount=amount,
            paid_in_cash=paid_in_cash,
            supplier_id=supplier_id,
            description=description,
        )
        if ship is None:
            raise NotFound("Shipping upsert failed.")
        await self._recompute_shipping_allocation(purchase_id)
        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.UPDATE,
                entity_type=ENTITY_PURCHASE_SHIPPING,
                entity_id=int(ship["id"]),
                old_values=old_values,
                new_values=dict(ship),
                ctx=ctx,
            )
        bumped = await self._repo.get(purchase_id)
        if bumped is None:
            raise NotFound("Purchase missing after shipping update.")
        new_etag, _v = self._etag_from_row(bumped)
        ship_dict = self._shipping_dict(ship)
        ship_dict["updated_at"] = bumped.get("updated_at")
        ship_dict["etag"] = new_etag
        return ship_dict

    # ==================================================================
    # D.4 — Post (lifecycle)
    # ==================================================================

    async def post_purchase(
        self,
        purchase_id: int,
        *,
        principal_user_id: int,
        if_match: str | None,
        idempotency_key: str | None,
        ctx: AuditContext | None,
        request_body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        idem_rec = None
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="POST",
                path=f"/purchases/{purchase_id}/post",
                body=request_body,
            )
            idem_rec = await store.start(
                key=idempotency_key,
                user_id=principal_user_id or 0,
                endpoint="POST /purchases/{id}/post",
                fingerprint=fingerprint,
            )
            if idem_rec is not None and idem_rec.is_completed:
                await self._uow.commit()
                return idem_rec.response_body, True  # type: ignore[return-value]

        purchase = await self._repo.get_for_update(purchase_id)
        if purchase is None:
            raise NotFound(f"Purchase {purchase_id} not found.")
        if purchase["lifecycle_status"] != LifecycleStatus.DRAFT.value:
            raise LifecycleViolation(
                f"Purchase is in '{purchase['lifecycle_status']}'; "
                "only draft can be posted."
            )
        current_etag, _v = self._etag_from_row(purchase)
        check_if_match(provided=if_match, current_etag=current_etag)

        lines_list, _ = await self._repo.list_lines(purchase_id, per_page=10_000)
        if not lines_list:
            raise LifecycleViolation("Cannot post a purchase with no lines.")

        shipping = await self._repo.get_shipping(purchase_id)
        ship_amount = _quant(shipping["amount"]) if shipping else Decimal("0")

        posted_at = datetime.now(UTC)
        # Insert purchase_receipt stock_movements with LANDED unit cost
        # (line_subtotal + allocated_shipping) per BR-PURCHASE-005.
        # moving-average cost is recomputed by the DB trigger for
        # value_adjustment / sale fallback paths; here we use the
        # original landed unit cost.
        for ln in lines_list:
            landed_unit_cost = (
                _quant(ln["line_subtotal"]) / _quant(ln["quantity"])
                + _quant(ln["allocated_shipping"]) / _quant(ln["quantity"])
            )
            qty = _quant(ln["quantity"])
            total_cost = _q2(landed_unit_cost * qty)
            await self._uow.execute(
                """
                INSERT INTO stock_movements (
                    product_id, trigger, quantity,
                    unit_cost_at_movement, total_cost,
                    reference_type, reference_id, reference_line_id,
                    reason, created_by
                ) VALUES (
                    :product_id, 'purchase_receipt', :quantity,
                    :unit_cost, :total_cost,
                    'purchase', :purchase_id, :reference_line_id,
                    NULL, :created_by
                )
                """,
                {
                    "product_id": int(ln["product_id"]),
                    "quantity": qty,
                    "unit_cost": landed_unit_cost,
                    "total_cost": total_cost,
                    "purchase_id": purchase_id,
                    "reference_line_id": int(ln["id"]),
                    "created_by": principal_user_id,
                },
            )

        posted = await self._repo.post(
            purchase_id,
            posted_by=principal_user_id,
            posted_at=posted_at,
            received_date=posted_at,
        )

        # AP creation is implicit: outstanding = total - payments.
        # No DB trigger needed for that since AP is server-computed.

        # If shipping was paid in cash, log a cash_movement (Dr Inventory /
        # Cr Cash) at post. This is for the freight_cash movement; it
        # generates a separate cash out equal to ship_amount.
        if shipping is not None and bool(shipping["paid_in_cash"]) and ship_amount > 0:
            await self._uow.execute(
                """
                INSERT INTO cash_movements (
                    amount, direction, trigger,
                    payment_method_id, reference_type, reference_id, created_by
                ) VALUES (
                    :amount, 'out', 'freight_cash',
                    1, 'purchase', :purchase_id, :created_by
                )
                """,
                {
                    "amount": -_q2(ship_amount),
                    "purchase_id": purchase_id,
                    "created_by": principal_user_id,
                },
            )

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.POST,
                entity_type=ENTITY_PURCHASES,
                entity_id=purchase_id,
                new_values={
                    "lifecycle_status": "posted",
                    "received_date": posted_at.isoformat(),
                    "stock_received_value": str(
                        sum(_q2(_quant(ln["line_total"])) for ln in lines_list)
                    ),
                },
                ctx=ctx,
            )

        lines_now, _ = await self._repo.list_lines(purchase_id, per_page=10_000)
        enriched = await self._enrich_purchase(
            posted or purchase,
            with_lines=lines_now,
            with_shipping=shipping,
        )

        if idempotency_key is not None and idem_rec is not None:
            store = IdempotencyStore(self._uow)
            await store.complete(
                record_id=idem_rec.id,
                status=200,
                body=_json_serializable(enriched),
                user_id=principal_user_id,
            )

        return enriched, False

    # ==================================================================
    # D.5 — Payments
    # ==================================================================

    async def list_payments(self, purchase_id: int) -> list[dict[str, Any]]:
        purchase = await self._repo.get(purchase_id)
        if purchase is None:
            raise NotFound(f"Purchase {purchase_id} not found.")
        rows, _ = await self._repo.list_payments(purchase_id, per_page=10_000)
        return [self._payment_dict(r) for r in rows]

    async def add_payment(
        self,
        purchase_id: int,
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
        # Idempotency required.
        if idempotency_key is None:
            from app.errors import MissingHeader

            raise MissingHeader(
                "Idempotency-Key header is required for addPurchasePayment."
            )

        idem_rec = None
        store = IdempotencyStore(self._uow)
        fingerprint = compute_request_fingerprint(
            method="POST",
            path=f"/purchases/{purchase_id}/payments",
            body=request_body,
        )
        idem_rec = await store.start(
            key=idempotency_key,
            user_id=principal_user_id or 0,
            endpoint="POST /purchases/{id}/payments",
            fingerprint=fingerprint,
        )
        if idem_rec is not None and idem_rec.is_completed:
            await self._uow.commit()
            return idem_rec.response_body, True  # type: ignore[return-value]

        purchase = await self._repo.get_for_update(purchase_id)
        if purchase is None:
            raise NotFound(f"Purchase {purchase_id} not found.")
        if purchase["lifecycle_status"] == LifecycleStatus.DRAFT.value:
            raise LifecycleViolation(
                "Cannot record payment on a draft purchase."
            )
        if purchase["lifecycle_status"] == LifecycleStatus.CANCELLED.value:
            raise LifecycleViolation(
                "Cannot record payment on a cancelled purchase."
            )
        current_etag, _v = self._etag_from_row(purchase)
        check_if_match(provided=if_match, current_etag=current_etag)

        # Cash over-tender rules (mirror sales).
        is_cash = await self._uow.first_row(
            "SELECT is_cash FROM payment_methods WHERE id = :id",
            {"id": payment_method_id},
        )
        if is_cash is None:
            raise NotFound(f"Payment method {payment_method_id} not found.")
        is_cash_flag = bool(is_cash["is_cash"])

        computed_change: Decimal | None = None
        computed_tendered: Decimal | None = None
        if is_cash_flag:
            if tendered_amount is not None:
                if tendered_amount < amount:
                    computed_change = Decimal("0")
                else:
                    computed_change = _q2(tendered_amount - amount)
                    computed_tendered = tendered_amount
            else:
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

        # Pre-check allocation against outstanding (the DB trigger will
        # also enforce; we raise a clean error code first).
        total = await self._purchase_total(purchase_id)
        existing_paid = await self._repo.sum_payments(purchase_id)
        # Outstanding is reduced by supplier_repayments(received_amount)
        # as well — for an AP context the supplier may have given us
        # goods back via supplier_repayments(received), so we deduct.
        srec_received = await self._repo.sum_supplier_repayments_for_purchase(
            purchase_id
        )
        max_payable = _q2(max(Decimal("0"), total - existing_paid - srec_received))
        if _q2(amount) > max_payable:
            raise AllocationExceedsPayable(
                "Payment exceeds outstanding payable.",
                details={
                    "allocated": float(existing_paid),
                    "new_amount": float(amount),
                    "total": float(total),
                    "outstanding": float(max_payable),
                },
            )

        # Cash availability (cash payments only) — DB trigger
        # fn_cash_movements_balance_check is the real guard.
        if is_cash_flag and amount > 0:
            row = await self._uow.first_row(
                "SELECT COALESCE(SUM(amount), 0) AS b FROM cash_movements"
            )
            current_cash = Decimal(str((row or {"b": 0})["b"]))
            if current_cash < amount:
                raise InsufficientCash(
                    "Insufficient cash balance for this payment.",
                    details={
                        "current_cash": float(current_cash),
                        "required": float(amount),
                    },
                )

        payment = await self._repo.insert_payment(
            purchase_id=purchase_id,
            payment_method_id=payment_method_id,
            amount=amount,
            tendered_amount=computed_tendered,
            change_amount=computed_change,
            payment_date=None,
            reference=request_body.get("reference") if request_body else None,
            created_by=principal_user_id,
        )

        # Cash movement for cash payments.
        if is_cash_flag:
            await self._uow.execute(
                """
                INSERT INTO cash_movements (
                    amount, direction, trigger,
                    payment_method_id, reference_type, reference_id, created_by
                ) VALUES (
                    :amount, 'out', 'purchase_payment',
                    :method, 'purchase_payment', :ref_id, :created_by
                )
                """,
                {
                    "amount": -_q2(amount),
                    "method": payment_method_id,
                    "ref_id": int(payment["id"]) if payment else None,
                    "created_by": principal_user_id,
                },
            )

        # Auto-complete when Σ payments == total.
        new_paid = _q2(existing_paid + amount)
        if new_paid >= total:
            try:
                await self._repo.set_lifecycle_status(
                    purchase_id, lifecycle_status="completed"
                )
            except Exception as exc:
                # Lifecycle terminal guard: only set if allowed.
                logger.debug("lifecycle_status_completed_blocked", error=str(exc))

        if ctx is not None and payment is not None:
            await write_audit(
                self._uow,
                action=AuditAction.PAYMENT,
                entity_type=ENTITY_PURCHASE_PAYMENTS,
                entity_id=int(payment["id"]),
                new_values={
                    "purchase_id": purchase_id,
                    "payment_method_id": payment_method_id,
                    "amount": str(amount),
                    "tendered_amount": (
                        str(computed_tendered) if computed_tendered is not None else None
                    ),
                    "change_amount": (
                        str(computed_change) if computed_change is not None else None
                    ),
                },
                ctx=ctx,
            )

        bumped = await self._repo.get(purchase_id)
        if bumped is None:
            raise NotFound("Purchase missing after payment.")
        enriched = await self._enrich_purchase(bumped)
        if payment is not None:
            enriched["payments"] = [self._payment_dict(payment)]

        if idem_rec is not None:
            await store.complete(
                record_id=idem_rec.id,
                status=201,
                body=_json_serializable(enriched),
                user_id=principal_user_id,
            )

        return enriched, False

    # ==================================================================
    # D.6 — Cancel
    # ==================================================================

    async def cancel_purchase(
        self,
        purchase_id: int,
        *,
        principal_user_id: int,
        reason: str,
        if_match: str | None,
        idempotency_key: str | None,
        ctx: AuditContext | None,
        request_body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        idem_rec = None
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="POST",
                path=f"/purchases/{purchase_id}/cancel",
                body=request_body,
            )
            idem_rec = await store.start(
                key=idempotency_key,
                user_id=principal_user_id or 0,
                endpoint="POST /purchases/{id}/cancel",
                fingerprint=fingerprint,
            )
            if idem_rec is not None and idem_rec.is_completed:
                await self._uow.commit()
                return idem_rec.response_body, True  # type: ignore[return-value]

        purchase = await self._repo.get_for_update(purchase_id)
        if purchase is None:
            raise NotFound(f"Purchase {purchase_id} not found.")
        if purchase["lifecycle_status"] == LifecycleStatus.CANCELLED.value:
            raise Conflict(
                "Purchase is already cancelled.",
                details={"already_cancelled": True},
            )
        if purchase["lifecycle_status"] not in _LIFECYCLE_CANCELLABLE:
            raise LifecycleViolation(
                f"Purchase in '{purchase['lifecycle_status']}' state "
                "cannot be cancelled."
            )
        current_etag, _v = self._etag_from_row(purchase)
        check_if_match(provided=if_match, current_etag=current_etag)

        # Reverse stock effects per BR-PURCHASE-007 + ST-3:
        # For each purchase_receipt movement, find the consumed portion
        # (negative Σ quantity from sale / production_input / etc. for
        # that product since the purchase) and split:
        #   * On-hand (Σ qty receipts - consumed) → purchase_reversal (qty = -receipt_qty,
        #     total_cost = -receipt_qty * landed_cost)
        #   * Consumed portion → value_adjustment (qty = 0, total_cost = -consumed * landed_cost)
        lines_list, _ = await self._repo.list_lines(purchase_id, per_page=10_000)
        for ln in lines_list:
            pid = int(ln["product_id"])
            # Find the original purchase_receipt movement(s) for this line.
            receipts = await self._uow.fetch_all(
                "SELECT id, quantity, unit_cost_at_movement, total_cost "
                "FROM stock_movements "
                "WHERE product_id = :pid AND reference_type = 'purchase' "
                "  AND reference_id = :pid2 AND reference_line_id = :lnid "
                "  AND trigger = 'purchase_receipt' "
                "ORDER BY id",
                {"pid": pid, "pid2": purchase_id, "lnid": int(ln["id"])},
            )
            for rec in receipts:
                landed_cost = _quant(rec["unit_cost_at_movement"])
                receipt_qty = _quant(rec["quantity"])
                # Sum movements with id >= this receipt id (all subsequent
                # activity) to compute the *running* net quantity for this
                # product that the receipt still "owes" stock for.
                post_row = await self._uow.first_row(
                    "SELECT COALESCE(SUM(quantity), 0) AS q "
                    "FROM stock_movements "
                    "WHERE product_id = :pid AND id >= :after_id",
                    {"pid": pid, "after_id": int(rec["id"])},
                )
                post_qty = Decimal(str((post_row or {"q": 0})["q"]))
                # post_qty = sum of movements with id >= this receipt id
                # (includes the receipt itself, +receipt_qty, and any
                # subsequent issues). Net still in stock attributable
                # to this batch = max(0, post_qty). consumed = receipt_qty
                # minus that.
                net_in_stock = max(Decimal("0"), post_qty)
                consumed = max(Decimal("0"), receipt_qty - net_in_stock)
                consumed = min(consumed, receipt_qty)
                # INV-05: each stock_movement can be reversed at most
                # once (reversal_of_movement_id is unique per row).
                # Issue a single ``purchase_reversal`` row that fully
                # unwinds the receipt (qty=-receipt_qty, total_cost =
                # -receipt_qty*landed_cost), then issue a balancing
                # ``value_adjustment`` row with qty=0 +consumed*landed_cost
                # so the sum of quantity stays -receipt_qty + 0 = -receipt_qty
                # (stock off-hand) and the sum of cost is
                # -receipt_qty*landed_cost + consumed*landed_cost
                # = -on_hand*landed_cost (matching the on-hand accounting).
                if receipt_qty > 0:
                    await self._uow.execute(
                        """
                        INSERT INTO stock_movements (
                            product_id, trigger, quantity,
                            unit_cost_at_movement, total_cost,
                            reference_type, reference_id, reference_line_id,
                            reversal_of_movement_id, reason, created_by
                        ) VALUES (
                            :pid, 'purchase_reversal', :qty,
                            :unit_cost, :total_cost,
                            'purchase', :rid, :lnid,
                            :reversal_of, :reason, :created_by
                        )
                        """,
                        {
                            "pid": pid,
                            "qty": -receipt_qty,
                            "unit_cost": landed_cost,
                            "total_cost": _q2(-receipt_qty * landed_cost),
                            "rid": purchase_id,
                            "lnid": int(ln["id"]),
                            "reversal_of": int(rec["id"]),
                            "reason": reason,
                            "created_by": principal_user_id,
                        },
                    )
                if consumed > 0:
                    # Companion value_adjustment (no reversal_of link,
                    # qty=0, total_cost=+consumed*landed_cost) to record
                    # the consumed-side reversal so the net cost effect is
                    # -on_hand*landed_cost (the on-hand portion only).
                    await self._uow.execute(
                        """
                        INSERT INTO stock_movements (
                            product_id, trigger, quantity,
                            unit_cost_at_movement, total_cost,
                            reference_type, reference_id, reference_line_id,
                            reason, created_by
                        ) VALUES (
                            :pid, 'value_adjustment', 0,
                            :unit_cost, :total_cost,
                            'purchase', :rid, :lnid,
                            :reason, :created_by
                        )
                        """,
                        {
                            "pid": pid,
                            "unit_cost": landed_cost,
                            "total_cost": _q2(consumed * landed_cost),
                            "rid": purchase_id,
                            "lnid": int(ln["id"]),
                            "reason": reason,
                            "created_by": principal_user_id,
                        },
                    )

        cancelled = await self._repo.cancel(
            purchase_id,
            cancelled_by=principal_user_id,
            cancellation_date=datetime.now(UTC),
            cancellation_reason=reason,
        )

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.CANCEL,
                entity_type=ENTITY_PURCHASES,
                entity_id=purchase_id,
                old_values=dict(purchase),
                new_values=dict(cancelled) if cancelled else None,
                reason=reason,
                ctx=ctx,
            )

        enriched = await self._enrich_purchase(cancelled or purchase)

        if idempotency_key is not None and idem_rec is not None:
            store = IdempotencyStore(self._uow)
            await store.complete(
                record_id=idem_rec.id,
                status=200,
                body=_json_serializable(enriched),
                user_id=principal_user_id,
            )

        return enriched, False

    # ==================================================================
    # D.7 / D.8 — Returns
    # ==================================================================

    async def create_return(
        self,
        purchase_id: int,
        *,
        principal_user_id: int,
        reason: str,
        return_lines: list[dict[str, Any]],
        if_match: str | None,
        idempotency_key: str | None,
        ctx: AuditContext | None,
        request_body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        idem_rec = None
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="POST",
                path=f"/purchases/{purchase_id}/returns",
                body=request_body,
            )
            idem_rec = await store.start(
                key=idempotency_key,
                user_id=principal_user_id or 0,
                endpoint="POST /purchases/{id}/returns",
                fingerprint=fingerprint,
            )
            if idem_rec is not None and idem_rec.is_completed:
                await self._uow.commit()
                return idem_rec.response_body, True  # type: ignore[return-value]

        purchase = await self._repo.get_for_update(purchase_id)
        if purchase is None:
            raise NotFound(f"Purchase {purchase_id} not found.")
        if purchase["lifecycle_status"] not in _LIFECYCLE_RETURNABLE:
            raise LifecycleViolation(
                f"Purchase in '{purchase['lifecycle_status']}' state "
                "cannot be returned."
            )
        current_etag, _v = self._etag_from_row(purchase)
        check_if_match(provided=if_match, current_etag=current_etag)

        total_value = Decimal("0")
        created_lines: list[dict[str, Any]] = []
        for rl in return_lines:
            pl_id = int(rl["purchase_line_id"])
            line_row = await self._repo.get_line(purchase_id, pl_id)
            if line_row is None:
                # Cross-purchase line lookup (line_id is unique anyway)
                line_row = await self._uow.first_row(
                    "SELECT * FROM purchase_lines WHERE id = :id",
                    {"id": pl_id},
                )
            if line_row is None:
                raise NotFound(f"Purchase line {pl_id} not found.")
            returned_so_far = await self._repo.sum_returned_qty_for_line(pl_id)
            original_qty = _quant(line_row["quantity"])
            requested = _quant(rl["quantity"])
            if requested <= 0:
                raise Conflict(
                    "Return quantity must be positive.",
                    details={"purchase_line_id": pl_id},
                )
            remaining = _q2(original_qty - returned_so_far)
            if requested > remaining:
                raise Conflict(
                    f"Return quantity exceeds remaining for purchase_line "
                    f"{pl_id}: requested {requested}, remaining {remaining}.",
                    details={
                        "purchase_return_quantity_exceeds_original": True,
                        "purchase_line_id": pl_id,
                        "requested": float(requested),
                        "remaining": float(remaining),
                    },
                )

            # Server-computed unit cost snapshot: pro-rata line_total / qty.
            unit_cost = (
                _quant(line_row["line_total"]) / original_qty
                if original_qty > 0
                else Decimal("0")
            )
            line_value = _q2(unit_cost * requested)
            total_value = _q2(total_value + line_value)
            created_lines.append(
                {
                    "purchase_line_id": pl_id,
                    "product_id": int(line_row["product_id"]),
                    "quantity": requested,
                    "unit_cost_snapshot": _q2(unit_cost),
                    "line_value": line_value,
                }
            )

        if total_value <= 0:
            raise LifecycleViolation("Cannot post a return with zero total value.")

        ret = await self._repo.insert_return(
            purchase_id=purchase_id,
            return_date=None,
            reason=reason,
            total_value_returned=total_value,
            created_by=principal_user_id,
        )
        if ret is None:
            raise NotFound("Return insert failed.")
        ret_id = int(ret["id"])

        for cl in created_lines:
            ln_no = await self._repo.next_return_line_number(ret_id)
            await self._repo.insert_return_line(
                purchase_return_id=ret_id,
                purchase_line_id=cl["purchase_line_id"],
                product_id=cl["product_id"],
                quantity=cl["quantity"],
                unit_cost_snapshot=cl["unit_cost_snapshot"],
                line_value=cl["line_value"],
                line_number=ln_no,
            )
            # Stock movement: negative qty (decrease on-hand) at original
            # unit_cost_snapshot, per BR-PURCHASE-006.
            await self._uow.execute(
                """
                INSERT INTO stock_movements (
                    product_id, trigger, quantity,
                    unit_cost_at_movement, total_cost,
                    reference_type, reference_id, reference_line_id,
                    reason, created_by
                ) VALUES (
                    :product_id, 'purchase_return', :qty,
                    :unit_cost, :total_cost,
                    'purchase_return', :ref_id, :ref_line,
                    :reason, :created_by
                )
                """,
                {
                    "product_id": cl["product_id"],
                    "qty": -cl["quantity"],
                    "unit_cost": cl["unit_cost_snapshot"],
                    "total_cost": _q2(-cl["quantity"] * cl["unit_cost_snapshot"]),
                    "ref_id": ret_id,
                    "ref_line": ln_no,
                    "reason": reason,
                    "created_by": principal_user_id,
                },
            )

        # Lifecycle transition: posted/completed → partially_returned.
        # If all lines fully returned → returned.
        all_lines, _ = await self._repo.list_lines(purchase_id, per_page=10_000)
        fully_returned = True
        for ln in all_lines:
            ret_qty = await self._repo.sum_returned_qty_for_line(int(ln["id"]))
            if ret_qty < _quant(ln["quantity"]):
                fully_returned = False
                break
        target = "returned" if fully_returned else "partially_returned"
        try:
            await self._repo.set_lifecycle_status(purchase_id, lifecycle_status=target)
        except Exception as exc:
            # Trigger guards may block if transition not allowed.
            logger.debug("lifecycle_status_returned_blocked", error=str(exc))

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.RETURN,
                entity_type=ENTITY_PURCHASE_RETURNS,
                entity_id=ret_id,
                new_values={"purchase_id": purchase_id, "reason": reason},
                ctx=ctx,
            )

        lines_now = await self._repo.list_return_lines(ret_id)
        enriched = {
            "id": ret_id,
            "purchase_id": purchase_id,
            "return_date": ret["return_date"],
            "reason": reason,
            "total_value_returned": float(total_value),
            "lifecycle_status": "posted",
            "lines": [self._return_line_dict(r) for r in lines_now],
            "created_at": ret["created_at"],
            "created_by": principal_user_id,
            "updated_at": ret["created_at"],
        }

        if idempotency_key is not None and idem_rec is not None:
            store = IdempotencyStore(self._uow)
            await store.complete(
                record_id=idem_rec.id,
                status=201,
                body=_json_serializable(enriched),
                user_id=principal_user_id,
            )

        return enriched, False

    async def list_returns(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
        sort: str = "id",
        purchase_id: int | None = None,
        supplier_id: int | None = None,
        lifecycle_status: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows, total = await self._repo.list_returns(
            page=page,
            per_page=per_page,
            sort=sort,
            purchase_id=purchase_id,
            supplier_id=supplier_id,
            lifecycle_status=lifecycle_status,
        )
        enriched: list[dict[str, Any]] = []
        for r in rows:
            lines = await self._repo.list_return_lines(int(r["id"]))
            enriched.append(
                {
                    "id": int(r["id"]),
                    "purchase_id": int(r["purchase_id"]),
                    "return_date": r["return_date"],
                    "reason": r.get("reason"),
                    "total_value_returned": float(r["total_value_returned"]),
                    "lifecycle_status": str(r["lifecycle_status"]),
                    "lines": [self._return_line_dict(ln) for ln in lines],
                    "created_at": r["created_at"],
                    "created_by": int(r["created_by"]),
                    "updated_at": r.get("created_at") or r.get("return_date"),
                }
            )
        return enriched, total

    async def get_return(self, return_id: int) -> dict[str, Any]:
        ret = await self._repo.get_return(return_id)
        if ret is None:
            raise NotFound(f"Purchase return {return_id} not found.")
        lines = await self._repo.list_return_lines(return_id)
        return {
            "id": int(ret["id"]),
            "purchase_id": int(ret["purchase_id"]),
            "return_date": ret["return_date"],
            "reason": ret.get("reason"),
            "total_value_returned": float(ret["total_value_returned"]),
            "lifecycle_status": str(ret["lifecycle_status"]),
            "lines": [self._return_line_dict(ln) for ln in lines],
            "created_at": ret["created_at"],
            "created_by": int(ret["created_by"]),
            "updated_at": ret.get("created_at") or ret.get("return_date"),
        }

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
        idem_rec = None
        if idempotency_key is not None:
            store = IdempotencyStore(self._uow)
            fingerprint = compute_request_fingerprint(
                method="POST",
                path=f"/purchase-returns/{return_id}/cancel",
                body=request_body,
            )
            idem_rec = await store.start(
                key=idempotency_key,
                user_id=principal_user_id or 0,
                endpoint="POST /purchase-returns/{id}/cancel",
                fingerprint=fingerprint,
            )
            if idem_rec is not None and idem_rec.is_completed:
                await self._uow.commit()
                return idem_rec.response_body, True  # type: ignore[return-value]

        ret = await self._repo.get_return_for_update(return_id)
        if ret is None:
            raise NotFound(f"Purchase return {return_id} not found.")
        if ret["lifecycle_status"] == "cancelled":
            raise Conflict("Purchase return is already cancelled.")
        # No `updated_at` on purchase_returns; treat version as the
        # ETag. Use the integer version for the If-Match check.
        current_etag = f'"{int(ret["version"])}"'
        check_if_match(provided=if_match, current_etag=current_etag)

        # Insert reversal movements (positive qty to restore stock).
        lines = await self._repo.list_return_lines(return_id)
        for ln in lines:
            orig = await self._uow.first_row(
                "SELECT * FROM stock_movements "
                "WHERE reference_type = 'purchase_return' "
                "  AND reference_id = :rid AND reference_line_id = :lnid "
                "  AND trigger = 'purchase_return'",
                {"rid": return_id, "lnid": int(ln["line_number"])},
            )
            if orig:
                await self._uow.execute(
                    """
                    INSERT INTO stock_movements (
                        product_id, trigger, quantity,
                        unit_cost_at_movement, total_cost,
                        reference_type, reference_id, reference_line_id,
                        reversal_of_movement_id, reason, created_by
                    ) VALUES (
                        :pid, 'purchase_return_reversal', :qty,
                        :unit_cost, :total_cost,
                        'purchase_return', :rid, :lnid,
                        :reversal_of, :reason, :created_by
                    )
                    """,
                    {
                        "pid": int(orig["product_id"]),
                        "qty": -_quant(orig["quantity"]),  # offset (restores stock)
                        "unit_cost": _quant(orig.get("unit_cost_at_movement")),
                        "total_cost": _q2(-_quant(orig.get("total_cost") or 0)),
                        "rid": return_id,
                        "lnid": int(ln["line_number"]),
                        "reversal_of": int(orig["id"]),
                        "reason": reason,
                        "created_by": principal_user_id,
                    },
                )

        await self._repo.cancel_return(return_id)

        if ctx is not None:
            await write_audit(
                self._uow,
                action=AuditAction.CANCEL,
                entity_type=ENTITY_PURCHASE_RETURNS,
                entity_id=return_id,
                old_values=dict(ret),
                new_values={"lifecycle_status": "cancelled"},
                reason=reason,
                ctx=ctx,
            )

        refreshed = await self._repo.get_return(return_id)
        if refreshed is None:
            raise NotFound("Purchase return missing after cancel.")
        lines_now = await self._repo.list_return_lines(return_id)
        enriched = {
            "id": int(refreshed["id"]),
            "purchase_id": int(refreshed["purchase_id"]),
            "return_date": refreshed["return_date"],
            "reason": refreshed.get("reason"),
            "total_value_returned": float(refreshed["total_value_returned"]),
            "lifecycle_status": str(refreshed["lifecycle_status"]),
            "lines": [self._return_line_dict(ln) for ln in lines_now],
            "created_at": refreshed["created_at"],
            "created_by": int(refreshed["created_by"]),
            "updated_at": refreshed.get("created_at") or refreshed.get("return_date"),
        }

        if idempotency_key is not None and idem_rec is not None:
            store = IdempotencyStore(self._uow)
            await store.complete(
                record_id=idem_rec.id,
                status=200,
                body=_json_serializable(enriched),
                user_id=principal_user_id,
            )

        return enriched, False


__all__ = ["PurchaseService"]
