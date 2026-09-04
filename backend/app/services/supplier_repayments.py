"""Business logic for the Supplier Repayments lifecycle (M4 / Phase D.9).

SREC (Supplier Receivable) = outstanding AP - sum(supplier_repayments(received_amount)).
Cash guard via :class:`InsufficientCash`; DB trigger is the
authoritative guard.

Mirrors the B.8 SaleService style.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from app.audit.service import ENTITY_SUPPLIER_REPAYMENTS, AuditContext, write_audit
from app.concurrency.etag import check_if_match
from app.concurrency.master_etag import etag_and_version_from_updated_at
from app.db import UnitOfWork
from app.errors import (
    InsufficientCash,
    NotFound,
    RepaymentExceedsSREC,
)
from app.logging import get_logger
from app.repositories.purchases import PurchaseRepository
from app.services.idempotency import (
    IdempotencyStore,
    compute_request_fingerprint,
)
from app.validation.enums import AuditAction

logger = get_logger(__name__)

_2DP = Decimal("0.01")


def _quant(x: Any) -> Decimal:
    if x is None:
        return Decimal("0")
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


def _q2(x: Any) -> Decimal:
    return _quant(x).quantize(_2DP)


def _json_serializable(obj: Any) -> str:
    import json

    return json.dumps(obj, default=str, sort_keys=True)


class SupplierRepaymentService:
    """Business logic for supplier repayments (D.9)."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow
        self._repo = PurchaseRepository(uow)

    @staticmethod
    def _etag_from_purchase(row: dict[str, Any]) -> tuple[str, int]:
        ts = row["updated_at"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return etag_and_version_from_updated_at(ts)

    @staticmethod
    def _repayment_dict(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(r["id"]),
            "purchase_id": int(r["purchase_id"]),
            "amount": float(r["amount"]),
            "received_amount": float(r["received_amount"]),
            "payment_method_id": int(r["payment_method_id"]),
            "repayment_date": r["repayment_date"],
            "reason": r.get("reason"),
            "refundable_amount_snapshot": float(r["refundable_amount_snapshot"]),
            "created_at": r["created_at"],
            "created_by": int(r["created_by"]),
        }

    # ------------------------------------------------------------------
    # List / Get
    # ------------------------------------------------------------------

    async def list_repayments(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
        sort: str = "id",
        q: str | None = None,
        purchase_id: int | None = None,
        payment_method_id: int | None = None,
        from_iso: str | None = None,
        to_iso: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows, total = await self._repo.list_supplier_repayments(
            page=page,
            per_page=per_page,
            sort=sort,
            q=q,
            purchase_id=purchase_id,
            payment_method_id=payment_method_id,
            from_iso=from_iso,
            to_iso=to_iso,
        )
        return [self._repayment_dict(r) for r in rows], total

    async def get_repayment(self, repayment_id: int) -> dict[str, Any]:
        row = await self._repo.get_supplier_repayment(repayment_id)
        if row is None:
            raise NotFound(f"Supplier repayment {repayment_id} not found.")
        return self._repayment_dict(row)

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create_repayment(
        self,
        *,
        principal_user_id: int,
        purchase_id: int,
        amount: Decimal,
        payment_method_id: int,
        repayment_date: datetime | None,
        reason: str | None,
        if_match: str | None,
        idempotency_key: str | None,
        ctx: AuditContext | None,
        request_body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        if idempotency_key is None:
            from app.errors import MissingHeader

            raise MissingHeader(
                "Idempotency-Key header is required for createSupplierRepayment."
            )
        store = IdempotencyStore(self._uow)
        fingerprint = compute_request_fingerprint(
            method="POST",
            path="/supplier-repayments",
            body=request_body,
        )
        idem_rec = await store.start(
            key=idempotency_key,
            user_id=principal_user_id or 0,
            endpoint="POST /supplier-repayments",
            fingerprint=fingerprint,
        )
        if idem_rec is not None and idem_rec.is_completed:
            await self._uow.commit()
            return idem_rec.response_body, True  # type: ignore[return-value]

        # Lock the purchase row.
        purchase = await self._repo.get_for_update(purchase_id)
        if purchase is None:
            raise NotFound(f"Purchase {purchase_id} not found.")
        current_etag, _v = self._etag_from_purchase(purchase)
        check_if_match(provided=if_match, current_etag=current_etag)

        # Compute outstanding (Σ lines + shipping - Σ payments) and
        # SREC = outstanding - Σ supplier_repayments(received_amount).
        line_total = await self._repo.sum_line_total(purchase_id)
        ship = await self._repo.get_shipping(purchase_id)
        ship_amount = _quant(ship["amount"]) if ship else Decimal("0")
        total = _q2(line_total + ship_amount)
        paid = await self._repo.sum_payments(purchase_id)
        outstanding = _q2(max(Decimal("0"), total - paid))
        srec_received = await self._repo.sum_supplier_repayments_for_purchase(
            purchase_id
        )
        srec = _q2(max(Decimal("0"), outstanding - srec_received))

        if amount > srec:
            raise RepaymentExceedsSREC(
                "Supplier repayment exceeds current SREC.",
                details={
                    "requested": float(amount),
                    "srec": float(srec),
                },
            )

        # Cash guard (cash methods).
        row = await self._uow.first_row(
            "SELECT is_cash FROM payment_methods WHERE id = :id",
            {"id": payment_method_id},
        )
        if row is None:
            raise NotFound(f"Payment method {payment_method_id} not found.")
        if bool(row["is_cash"]):
            cb = await self._uow.first_row(
                "SELECT COALESCE(SUM(amount), 0) AS b FROM cash_movements"
            )
            current_cash = Decimal(str((cb or {"b": 0})["b"]))
            if current_cash < amount:
                raise InsufficientCash(
                    "Insufficient cash balance for supplier repayment.",
                    details={
                        "current_cash": float(current_cash),
                        "required": float(amount),
                    },
                )

        # amount = total obligation (Σ payments for this purchase at the
        # time of recording), received_amount = this cash receipt.
        # Per DB §11.2 schema comment, amount is the total obligation;
        # since the model is simplified to one row per purchase, we set
        # amount = outstanding (lines + shipping - payments) and
        # received_amount increments by this receipt. We do that with a
        # bounded UPDATE so concurrent inserts cannot push past amount.
        # NOTE: For an unpaid / pure-credit purchase there are no
        # purchase_payments rows, so Σ purchase_payments = 0 would
        # incorrectly mask the cumulative cap. `outstanding` is already
        # computed above as (lines + shipping - payments) and is the
        # correct obligation for the SREC cumulative bound.
        total_obligation = outstanding
        new_received = _q2(srec_received + amount)
        if new_received > total_obligation:
            new_received = total_obligation

        repayment = await self._repo.insert_supplier_repayment(
            purchase_id=purchase_id,
            amount=total_obligation if total_obligation > 0 else _q2(amount),
            received_amount=new_received,
            payment_method_id=payment_method_id,
            repayment_date=repayment_date,
            reason=reason,
            refundable_amount_snapshot=srec,
            created_by=principal_user_id,
        )

        if repayment is not None and bool(row["is_cash"]):
            await self._uow.execute(
                """
                INSERT INTO cash_movements (
                    amount, direction, trigger,
                    payment_method_id, reference_type, reference_id, created_by
                ) VALUES (
                    :amount, 'in', 'supplier_repayment',
                    :method, 'supplier_repayment', :ref_id, :created_by
                )
                """,
                {
                    "amount": _q2(amount),
                    "method": payment_method_id,
                    "ref_id": int(repayment["id"]),
                    "created_by": principal_user_id,
                },
            )

        if ctx is not None and repayment is not None:
            await write_audit(
                self._uow,
                action=AuditAction.PAYMENT,
                entity_type=ENTITY_SUPPLIER_REPAYMENTS,
                entity_id=int(repayment["id"]),
                new_values={
                    "purchase_id": purchase_id,
                    "amount": str(_q2(amount)),
                    "payment_method_id": payment_method_id,
                    "reason": reason,
                    "srec_snapshot": str(srec),
                },
                ctx=ctx,
            )

        body = self._repayment_dict(repayment) if repayment else {}

        await store.complete(
            record_id=idem_rec.id,
            status=201,
            body=_json_serializable(body),
            user_id=principal_user_id,
        )

        return body, False


__all__ = ["SupplierRepaymentService"]
