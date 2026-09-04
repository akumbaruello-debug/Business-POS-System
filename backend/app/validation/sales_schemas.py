"""Request/response schemas for the Sales lifecycle (B.8).

Mirrors ``openapi.yaml`` §15 (Sales, Sale Lines, Sale Payments, Sales
Returns). Schemas use ``extra='forbid'`` everywhere so unknown fields
raise ``validation_failed`` at parse time.

Known schema/DB reconciliations (documented, not silently resolved):

* The OpenAPI ``Sale`` schema exposes ``payment_state`` (enum:
  ``unpaid|partial|paid``), ``paid_amount``, ``outstanding``, ``ar``,
  ``crl``, and ``version`` as required fields. None of these are
  stored on the ``sales`` table — they are all server-computed from
  ``sales.total_amount`` + the sale_payments / refunds / sales_returns
  aggregates. The service layer computes them on read.

* ``lines`` / ``payments`` / ``returns`` are embedded sub-resources
  included via the ``?include=`` query param. Default responses do
  NOT include them (lighter payload).

* ``version`` is taken from the ``sales.version`` column (DB trigger
  ``fn_bump_version_and_updated_at`` auto-increments on every write).
  The OpenAPI ETag uses ``updated_at`` (also auto-managed by the
  same trigger).

* ``unit_price`` and ``discount_amount`` on a sale line are
  client-supplied: a non-default ``unit_price`` is a "price override"
  (requires ``sale.price_override``) and a non-zero
  ``discount_amount`` is a "discount" (requires ``sale.discount``).
  Both capabilities are checked at line create/update time.

* The sale header has no ``line_total`` field — only the lines do
  (server-computed ``quantity * unit_price - discount_amount``).
  ``sales.total_amount = Σ line_total - sales.discount_amount``.

* ``reference_no`` is optional and partial-unique. Auto-generation
  via ``enable_sequential_doc_numbers`` is an F-phase feature; V1
  clients must supply their own.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# -----------------------------------------------------------------------------
# Sale Lines
# -----------------------------------------------------------------------------


class SaleLineInput(BaseModel):
    """Body for ``POST /sales/{id}/lines``. Mirrors openapi.yaml §15.10.7."""

    model_config = ConfigDict(extra="forbid")

    product_id: int = Field(..., ge=1)
    quantity: float = Field(..., gt=0, le=1_000_000_000_000.0)
    unit_price: float | None = Field(default=None, ge=0)
    discount_amount: float | None = Field(default=None, ge=0)


class SaleLinePatch(BaseModel):
    """Body for ``PATCH /sales/{id}/lines/{line_id}``.

    Per openapi.yaml all three fields are optional; a no-op PATCH
    (empty body) is allowed and re-derives ``line_total``. ``product_id``
    is immutable after creation (the DB FK + the route layer enforce).
    """

    model_config = ConfigDict(extra="forbid")

    quantity: float | None = Field(default=None, gt=0, le=1_000_000_000_000.0)
    unit_price: float | None = Field(default=None, ge=0)
    discount_amount: float | None = Field(default=None, ge=0)


class SaleLine(BaseModel):
    """Response shape for a sale line. Mirrors openapi.yaml §15.10.7."""

    model_config = ConfigDict(extra="forbid")

    id: int
    sale_id: int
    product_id: int
    quantity: float
    unit_price: float
    discount_amount: float
    line_total: float
    unit_cost_snapshot: float | None = None
    cogs_total_snapshot: float | None = None
    is_negative_stock_fallback: bool = False
    stock_movement_id: int | None = None
    line_number: int = Field(..., ge=1)


# -----------------------------------------------------------------------------
# Sale Payments
# -----------------------------------------------------------------------------


class SalePaymentInput(BaseModel):
    """Body for ``POST /sales/{id}/payments``. Mirrors openapi.yaml §15.10.9."""

    model_config = ConfigDict(extra="forbid")

    payment_method_id: int = Field(..., ge=1)
    amount: float = Field(..., gt=0, le=10_000_000_000_000.0)
    tendered_amount: float | None = Field(default=None, ge=0)
    change_amount: float | None = Field(default=None, ge=0)
    payment_date: datetime | None = None
    reference: str | None = Field(default=None, max_length=100)


class SalePayment(BaseModel):
    """Response shape for a sale payment. Mirrors openapi.yaml §15.10.9."""

    model_config = ConfigDict(extra="forbid")

    id: int
    sale_id: int
    payment_method_id: int
    amount: float
    tendered_amount: float | None = None
    change_amount: float | None = None
    payment_date: datetime
    reference: str | None = None
    created_at: datetime
    created_by: int


# -----------------------------------------------------------------------------
# Sale header
# -----------------------------------------------------------------------------


class SaleCreateRequest(BaseModel):
    """Body for ``POST /sales``. Mirrors openapi.yaml §15.10.5.

    ``lines`` is required and must be non-empty (minItems=1) — the
    service layer validates this server-side even though Pydantic v2
    does not yet support minItems on nested list fields directly.
    """

    model_config = ConfigDict(extra="forbid")

    customer_id: int | None = Field(default=None, ge=1)
    sale_date: datetime | None = None
    discount_amount: float = Field(default=0, ge=0)
    notes: str | None = Field(default=None, max_length=2000)
    reference_no: str | None = Field(default=None, max_length=50)
    lines: list[SaleLineInput] = Field(default_factory=list, min_length=1)


class SalePatch(BaseModel):
    """Body for ``PATCH /sales/{id}``. Draft-only edit.

    Cannot change ``lifecycle_status``, ``lines``, or created rows —
    the route layer + DB trigger enforce the draft-only invariant.
    """

    model_config = ConfigDict(extra="forbid")

    customer_id: int | None = Field(default=None, ge=1)
    sale_date: datetime | None = None
    discount_amount: float | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=2000)


class SaleCancelRequest(BaseModel):
    """Body for ``POST /sales/{id}/cancel``. Mirrors openapi.yaml §15.10.10."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=1000)


class Sale(BaseModel):
    """Response shape for a sale. Mirrors openapi.yaml §15.10.5.

    All server-computed fields (payment_state, paid_amount, outstanding,
    ar, crl, version, lifecycle_status, total_amount, discount_amount)
    are populated by the service layer on read.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    reference_no: str | None = None
    customer_id: int | None = None
    customer: dict[str, Any] | None = None
    sale_date: datetime
    total_amount: float
    discount_amount: float
    lifecycle_status: str
    payment_state: str
    paid_amount: float
    outstanding: float
    ar: float
    crl: float
    cancellation_date: datetime | None = None
    cancellation_reason: str | None = None
    cancelled_by: int | None = None
    posted_at: datetime | None = None
    posted_by: int | None = None
    notes: str | None = None
    lines: list[SaleLine] | None = None
    payments: list[SalePayment] | None = None
    returns: list[SalesReturn] | None = None
    created_at: datetime
    updated_at: datetime
    created_by: int
    version: int


# -----------------------------------------------------------------------------
# Sales Returns
# -----------------------------------------------------------------------------


class SaleReturnLineRequest(BaseModel):
    """One line in a return. Mirrors openapi.yaml §15.10.11."""

    model_config = ConfigDict(extra="forbid")

    sale_line_id: int = Field(..., ge=1)
    quantity: float = Field(..., gt=0, le=1_000_000_000_000.0)


class SaleReturnRequest(BaseModel):
    """Body for ``POST /sales/{id}/returns``. Mirrors openapi.yaml §15.10.11."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=1000)
    lines: list[SaleReturnLineRequest] = Field(default_factory=list, min_length=1)


class SalesReturnCancelRequest(BaseModel):
    """Body for ``POST /sales-returns/{id}/cancel``. Mirrors openapi.yaml §15.10.12."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=1000)


class SalesReturnLine(BaseModel):
    """Response shape for a return line. Mirrors openapi.yaml §15.10.12."""

    model_config = ConfigDict(extra="forbid")

    id: int
    sales_return_id: int
    sale_line_id: int
    product_id: int
    quantity: float
    returned_selling_price: float
    returned_unit_cost: float
    line_value: float
    line_number: int = Field(..., ge=1)


class SalesReturn(BaseModel):
    """Response shape for a sales return. Mirrors openapi.yaml §15.10.11.

    ``lifecycle_status`` is the smaller enum (``posted|cancelled``) per
    schema; the header-level lifecycle is on ``sales``.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    sale_id: int
    return_date: datetime
    reason: str | None = None
    total_selling_price_returned: float
    total_cost_returned: float
    lifecycle_status: str
    lines: list[SalesReturnLine]
    created_at: datetime
    created_by: int


__all__ = [
    "Sale",
    "SaleCancelRequest",
    "SaleCreateRequest",
    "SaleLine",
    "SaleLineInput",
    "SaleLinePatch",
    "SalePatch",
    "SalePayment",
    "SalePaymentInput",
    "SaleReturnLineRequest",
    "SaleReturnRequest",
    "SalesReturn",
    "SalesReturnCancelRequest",
    "SalesReturnLine",
]
