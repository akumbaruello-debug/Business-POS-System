"""Business logic for the Refunds lifecycle (M4 / Phase D.1).

CRL = sum(sale_payments) - sum(refunds) for the parent sale (INV-04).
Cash guard via :class:`InsufficientCash`; the DB trigger
``fn_cash_movements_balance_check`` is the authoritative guard for
concurrent races.

Mirrors the B.8 SaleService style (UoW-scoped, raw-SQL repo, idempotency
cache through :class:`IdempotencyStore`).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from app.audit.service import ENTITY_REFUNDS, AuditContext, write_audit
from app.concurrency.etag import check_if_match
from app.concurrency.master_etag import etag_and_version_from_updated_at
from app.db import UnitOfWork
from app.errors import (
    InsufficientCash,
    NotFound,
    RefundExceedsCRL,
)
from app.logging import get_logger
from app.repositories.purchases import PurchaseRepository
from app.repositories.sales import SaleRepository
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


class RefundService:
    """Business logic for customer refunds (D.1)."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow
        self._repo = PurchaseRepository(uow)  # owns refunds table
        self._sale_repo = SaleRepository(uow)

    @staticmethod
    def _etag_from_sale(row: dict[str, Any]) -> tuple[str, int]:
        ts = row["updated_at"]
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return etag_and_version_from_updated_at(ts)

    # ------------------------------------------------------------------
    # Response shape
    # ------------------------------------------------------------------

    @staticmethod
    def _refund_dict(r: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": int(r["id"]),
            "sale_id": int(r["sale_id"]),
            "amount": float(r["amount"]),
            "payment_method_id": int(r["payment_method_id"]),
            "refund_date": r["refund_date"],
            "reason": r.get("reason"),
            "refundable_amount_snapshot": float(r["refundable_amount_snapshot"]),
            "created_at": r["created_at"],
            "created_by": int(r["created_by"]),
        }

    # ------------------------------------------------------------------
    # List / Get
    # ------------------------------------------------------------------

    async def list_refunds(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
        sort: str = "id",
        q: str | None = None,
        sale_id: int | None = None,
        payment_method_id: int | None = None,
        from_iso: str | None = None,
        to_iso: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows, total = await self._repo.list_refunds(
            page=page,
            per_page=per_page,
            sort=sort,
            q=q,
            sale_id=sale_id,
            payment_method_id=payment_method_id,
            from_iso=from_iso,
            to_iso=to_iso,
        )
        return [self._refund_dict(r) for r in rows], total

    async def get_refund(self, refund_id: int) -> dict[str, Any]:
        row = await self._repo.get_refund(refund_id)
        if row is None:
            raise NotFound(f"Refund {refund_id} not found.")
        return self._refund_dict(row)

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create_refund(
        self,
        *,
        principal_user_id: int,
        sale_id: int,
        amount: Decimal,
        payment_method_id: int,
        refund_date: datetime | None,
        reason: str | None,
        if_match: str | None,
        idempotency_key: str | None,
        ctx: AuditContext | None,
        request_body: dict[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        # Idempotency required (OpenAPI: IdempotencyKeyRequired).
        if idempotency_key is None:
            from app.errors import MissingHeader

            raise MissingHeader(
                "Idempotency-Key header is required for createRefund."
            )
        store = IdempotencyStore(self._uow)
        fingerprint = compute_request_fingerprint(
            method="POST",
            path="/refunds",
            body=request_body,
        )
        idem_rec = await store.start(
            key=idempotency_key,
            user_id=principal_user_id or 0,
            endpoint="POST /refunds",
            fingerprint=fingerprint,
        )
        if idem_rec is not None and idem_rec.is_completed:
            await self._uow.commit()
            return idem_rec.response_body, True  # type: ignore[return-value]

        # Lock the sale row so concurrent refund creators serialize.
        sale = await self._sale_repo.get_for_update(sale_id)
        if sale is None:
            raise NotFound(f"Sale {sale_id} not found.")
        current_etag, _v = self._etag_from_sale(sale)
        check_if_match(provided=if_match, current_etag=current_etag)

        # CRL = Σ payments - Σ refunds (for this sale).
        paid = await self._sale_repo.sum_payments(sale_id)
        already_refunded = await self._repo.sum_refunds_for_sale(sale_id)
        crl = _q2(max(Decimal("0"), paid - already_refunded))

        if amount > crl:
            raise RefundExceedsCRL(
                "Refund exceeds current refundable balance (CRL).",
                details={
                    "requested": float(amount),
                    "crl": float(crl),
                },
            )

        # Cash guard.
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
                    "Insufficient cash balance for refund.",
                    details={
                        "current_cash": float(current_cash),
                        "required": float(amount),
                    },
                )

        refund = await self._repo.insert_refund(
            sale_id=sale_id,
            amount=_q2(amount),
            payment_method_id=payment_method_id,
            refund_date=refund_date,
            reason=reason,
            refundable_amount_snapshot=crl,
            created_by=principal_user_id,
        )

        # Cash movement (out) for cash refunds.
        if refund is not None and bool(row["is_cash"]):
            await self._uow.execute(
                """
                INSERT INTO cash_movements (
                    amount, direction, trigger,
                    payment_method_id, reference_type, reference_id, created_by
                ) VALUES (
                    :amount, 'out', 'refund',
                    :method, 'refund', :ref_id, :created_by
                )
                """,
                {
                    "amount": -_q2(amount),
                    "method": payment_method_id,
                    "ref_id": int(refund["id"]),
                    "created_by": principal_user_id,
                },
            )

        if ctx is not None and refund is not None:
            await write_audit(
                self._uow,
                action=AuditAction.REFUND,
                entity_type=ENTITY_REFUNDS,
                entity_id=int(refund["id"]),
                new_values={
                    "sale_id": sale_id,
                    "amount": str(_q2(amount)),
                    "payment_method_id": payment_method_id,
                    "reason": reason,
                    "crl_snapshot": str(crl),
                },
                ctx=ctx,
            )

        body = self._refund_dict(refund) if refund else {}

        await store.complete(
            record_id=idem_rec.id,
            status=201,
            body=_json_serializable(body),
            user_id=principal_user_id,
        )

        return body, False


__all__ = ["RefundService"]
