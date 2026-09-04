"""Request/response schemas for the M4 purchases / refunds / supplier
repayments lifecycle.

Mirrors ``openapi.yaml`` §13-§16 exactly. ``extra='forbid'`` everywhere so
unknown fields raise ``validation_failed`` at parse time (per the project
convention established by the sales schemas).

Known reconciliations against the locked schema.sql (documented, not
silently resolved):

* ``Purchase.total_amount`` is required on the response but is **not**
  stored on ``purchases`` — it is ``Σ purchase_lines.line_total``. The
  service computes it server-side on read (mirroring how B.8 computes
  ``sales.total_amount``, which IS stored — but for purchases the total
  is derived from the line + shipping sum per schema.sql §7.2 comment).

* ``PurchaseLine.allocated_shipping`` and ``line_total`` are
  server-computed (derived from ``line_subtotal`` + pro-rata shipping).
  The DB trigger is NOT the authority here — the service computes them
  on insert, and the ``fn_purchase_payment_allocation_bound`` trigger
  computes the purchase total for the allocation ceiling from
  ``Σ line_total + shipping.amount``.

* ``Purchase.ap`` = outstanding payable = total - Σ payments - Σ
  supplier_repayments(received). Server-computed on read.

* ``Purchase.supplier_receivable`` (SREC) = total - Σ payments - Σ
  supplier_repayments(received). Same as ap. Server-computed.

* ``PurchaseShipping`` has no ``updated_at`` — ETag is the parent
  purchase's ``updated_at`` (shipping mutations bump the purchase row
  via the trigger? No — shipping rows are separate. We use the parent
  purchase's ETag for If-Match on the shipping endpoint, since shipping
  is always edited on a draft purchase and the route re-reads the parent.)

* ``Refund.idempotent`` replay returns the cached body. The route layer
  handles idempotency exactly like B.8 ``createSale``.

* ``RefundRequest`` is bounded by CRL AND cash; ``SupplierRepaymentRequest``
  by SREC AND cash.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ----------------------------------------------------------------------------
# Purchases
# ----------------------------------------------------------------------------


class PurchaseLineInput(BaseModel):
    """Body for adding a purchase line. Mirrors openapi.yaml §13 ``PurchaseLineInput``."""

    model_config = ConfigDict(extra="forbid")

    product_id: int = Field(..., ge=1)
    quantity: float = Field(..., gt=0, le=1_000_000_000_000.0)
    unit_price: float = Field(..., ge=0)


class PurchaseLinePatch(BaseModel):
    """Body for updating a purchase line. All fields optional."""

    model_config = ConfigDict(extra="forbid")

    quantity: float | None = Field(default=None, gt=0, le=1_000_000_000_000.0)
    unit_price: float | None = Field(default=None, ge=0)


class PurchaseLine(BaseModel):
    """Response shape for a purchase line. Mirrors openapi.yaml §13."""

    model_config = ConfigDict(extra="forbid")

    id: int
    purchase_id: int
    product_id: int
    quantity: float
    unit_price: float
    line_subtotal: float
    allocated_shipping: float
    line_total: float
    line_number: int = Field(..., ge=1)


class PurchaseShippingBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: float = Field(..., ge=0)
    paid_in_cash: bool = Field(default=False)
    supplier_id: int | None = Field(default=None, ge=1)
    description: str | None = Field(default=None, max_length=500)


class PurchaseShippingRequest(PurchaseShippingBase):
    """Body for setPurchaseShipping / updatePurchaseShipping."""


class PurchaseShipping(PurchaseShippingBase):
    """Response for PurchaseShipping. Mirrors openapi.yaml §13."""

    id: int
    purchase_id: int
    amount: float
    paid_in_cash: bool


class PurchaseCreateRequest(BaseModel):
    """Body for createPurchase. Mirrors openapi.yaml §13."""

    model_config = ConfigDict(extra="forbid")

    supplier_id: int | None = Field(default=None, ge=1)
    purchase_date: datetime | None = None
    notes: str | None = Field(default=None, max_length=2000)
    reference_no: str | None = Field(default=None, max_length=50)
    lines: list[PurchaseLineInput] = Field(default_factory=list, min_length=1)
    shipping: PurchaseShippingRequest | None = None


class PurchasePatch(BaseModel):
    """Body for updatePurchaseDraft. Mirrors openapi.yaml §13."""

    model_config = ConfigDict(extra="forbid")

    supplier_id: int | None = Field(default=None, ge=1)
    purchase_date: datetime | None = None
    notes: str | None = Field(default=None, max_length=2000)


# Re-use the sale payment input shape for purchase payments (openapi says
# ``allOf: SalePaymentInput``). We import lazily to avoid a hard dependency
# cycle; the sales schemas module does not import from here.
class _SalePaymentInputShape(BaseModel):
    """Local copy of SalePaymentInput shape used via allOf on
    ``PurchasePaymentInput`` in openapi.yaml §15.10.9 / §15.12.8."""

    model_config = ConfigDict(extra="forbid")

    payment_method_id: int = Field(..., ge=1)
    amount: float = Field(..., gt=0, le=10_000_000_000_000.0)
    tendered_amount: float | None = Field(default=None, ge=0)
    change_amount: float | None = Field(default=None, ge=0)
    payment_date: datetime | None = None
    reference: str | None = Field(default=None, max_length=100)


class PurchasePaymentInput(_SalePaymentInputShape):
    """Body for addPurchasePayment. Mirrors openapi.yaml §13
    ``PurchasePaymentInput = allOf(SalePaymentInput)``."""


class PurchasePayment(BaseModel):
    """Response for a purchase payment. Mirrors openapi.yaml §13."""

    model_config = ConfigDict(extra="forbid")

    id: int
    purchase_id: int
    payment_method_id: int
    amount: float
    tendered_amount: float | None = None
    change_amount: float | None = None
    payment_date: datetime
    reference: str | None = None
    created_at: datetime
    created_by: int


class PurchaseCancelRequest(BaseModel):
    """Body for cancelPurchase. Mirrors openapi.yaml §13."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=1000)


class Purchase(BaseModel):
    """Response for a purchase. Mirrors openapi.yaml §13."""

    model_config = ConfigDict(extra="forbid")

    id: int
    reference_no: str | None = None
    supplier_id: int | None = None
    supplier: dict[str, Any] | None = None
    purchase_date: datetime
    received_date: datetime | None = None
    total_amount: float
    lifecycle_status: str
    payment_state: str
    paid_amount: float
    outstanding: float
    ap: float
    supplier_receivable: float
    posted_at: datetime | None = None
    posted_by: int | None = None
    cancellation_date: datetime | None = None
    cancellation_reason: str | None = None
    cancelled_by: int | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime
    created_by: int
    version: int
    lines: list[PurchaseLine] | None = None
    shipping: PurchaseShipping | None = None
    payments: list[PurchasePayment] | None = None
    returns: list[PurchaseReturn] | None = None


# ----------------------------------------------------------------------------
# Purchase Returns
# ----------------------------------------------------------------------------


class PurchaseReturnLineRequest(BaseModel):
    """Line in a return. Mirrors openapi.yaml §13."""

    model_config = ConfigDict(extra="forbid")

    purchase_line_id: int = Field(..., ge=1)
    quantity: float = Field(..., gt=0, le=1_000_000_000_000.0)


class PurchaseReturnRequest(BaseModel):
    """Body for createPurchaseReturn. Mirrors openapi.yaml §13."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=1000)
    lines: list[PurchaseReturnLineRequest] = Field(min_length=1)


class PurchaseReturnLine(BaseModel):
    """Response for a purchase return line. Mirrors openapi.yaml §13."""

    model_config = ConfigDict(extra="forbid")

    id: int
    purchase_return_id: int
    purchase_line_id: int
    product_id: int
    quantity: float
    unit_cost_snapshot: float
    line_value: float
    line_number: int = Field(..., ge=1)


class PurchaseReturnCancelRequest(BaseModel):
    """Body for cancelPurchaseReturn (= SalesReturnCancelRequest)."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=1000)


class PurchaseReturn(BaseModel):
    """Response for a purchase return. Mirrors openapi.yaml §13."""

    model_config = ConfigDict(extra="forbid")

    id: int
    purchase_id: int
    return_date: datetime
    reason: str | None = None
    total_value_returned: float
    lifecycle_status: str
    lines: list[PurchaseReturnLine]
    created_at: datetime
    created_by: int
    updated_at: datetime | None = None  # cancellation_date if cancelled


# ----------------------------------------------------------------------------
# Refunds (D.1)
# ----------------------------------------------------------------------------


class RefundRequest(BaseModel):
    """Body for createRefund. Mirrors openapi.yaml §16.7."""

    model_config = ConfigDict(extra="forbid")

    sale_id: int = Field(..., ge=1)
    amount: float = Field(..., gt=0)
    payment_method_id: int = Field(..., ge=1)
    refund_date: datetime | None = None
    reason: str | None = Field(default=None, max_length=1000)


class Refund(BaseModel):
    """Response for a refund. Mirrors openapi.yaml §16.7."""

    model_config = ConfigDict(extra="forbid")

    id: int
    sale_id: int
    amount: float
    payment_method_id: int
    refund_date: datetime
    reason: str | None = None
    refundable_amount_snapshot: float
    created_at: datetime
    created_by: int


# ----------------------------------------------------------------------------
# Supplier Repayments (D.9)
# ----------------------------------------------------------------------------


class SupplierRepaymentRequest(BaseModel):
    """Body for createSupplierRepayment. Mirrors openapi.yaml §14.8."""

    model_config = ConfigDict(extra="forbid")

    purchase_id: int = Field(..., ge=1)
    amount: float = Field(..., gt=0)
    payment_method_id: int = Field(..., ge=1)
    repayment_date: datetime | None = None
    reason: str | None = Field(default=None, max_length=1000)


class SupplierRepayment(BaseModel):
    """Response for a supplier repayment. Mirrors openapi.yaml §14.8."""

    model_config = ConfigDict(extra="forbid")

    id: int
    purchase_id: int
    amount: float
    received_amount: float
    payment_method_id: int
    repayment_date: datetime
    reason: str | None = None
    refundable_amount_snapshot: float
    created_at: datetime
    created_by: int


# Resolve forward refs.
Purchase.model_rebuild()
PurchaseReturn.model_rebuild()


__all__ = [
    "Purchase",
    "PurchaseCancelRequest",
    "PurchaseCreateRequest",
    "PurchaseLine",
    "PurchaseLineInput",
    "PurchaseLinePatch",
    "PurchasePatch",
    "PurchasePayment",
    "PurchasePaymentInput",
    "PurchaseReturn",
    "PurchaseReturnCancelRequest",
    "PurchaseReturnLine",
    "PurchaseReturnLineRequest",
    "PurchaseReturnRequest",
    "PurchaseShipping",
    "PurchaseShippingRequest",
    "Refund",
    "RefundRequest",
    "SupplierRepayment",
    "SupplierRepaymentRequest",
]
