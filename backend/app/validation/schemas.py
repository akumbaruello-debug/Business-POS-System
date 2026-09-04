"""Request/response schemas for M2 foundation modules.

Schemas are Pydantic v2 models that mirror ``openapi.yaml`` exactly.
They live in the validation package (architecture layer) so the api
layer stays free of business-model concerns and there is a single
source of truth for request/response shapes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.validation.enums import FinancialEntryType
from app.validation.pagination import MetaEnvelope

# ---------------------------------------------------------------------------
# Payment Method
# ---------------------------------------------------------------------------


class PaymentMethodBase(BaseModel):
    """Common fields for payment-method create/patch.

    Mirrors ``openapi.yaml`` ``PaymentMethodRequest`` / ``PaymentMethodPatch``.
    """

    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=100)
    is_cash: bool = True
    is_active: bool = True


class PaymentMethodRequest(PaymentMethodBase):
    """Body for ``POST /payment-methods``."""


class PaymentMethodPatch(BaseModel):
    """Body for ``PATCH /payment-methods/{id}``.

    All fields optional — only supplied fields are updated. ``code`` cannot
    be changed (immutable once set, like products.code).
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    is_cash: bool | None = None
    is_active: bool | None = None


class PaymentMethodResponse(BaseModel):
    """Response for payment-method endpoints (matches ``PaymentMethod``)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    is_cash: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class DeactivateRequest(BaseModel):
    """Body for ``POST .../deactivate`` endpoints."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)


# ---------------------------------------------------------------------------
# Financial Category
# ---------------------------------------------------------------------------


class FinancialCategoryBase(BaseModel):
    """Common fields for financial-category create/patch."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=1, max_length=50)
    name: str = Field(..., min_length=1, max_length=100)
    entry_type: FinancialEntryType
    is_active: bool = True


class FinancialCategoryRequest(FinancialCategoryBase):
    """Body for ``POST /financial-categories``."""


class FinancialCategoryPatch(BaseModel):
    """Body for ``PATCH /financial-categories/{id}``.

    ``code`` and ``entry_type`` are immutable once set.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None


class FinancialCategoryResponse(BaseModel):
    """Response for financial-category endpoints (matches ``FinancialCategory``)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    entry_type: FinancialEntryType
    is_active: bool
    created_at: datetime
    updated_at: datetime


# -----------------------------------------------------------------------------
# Manual Finance Entry (E.8)
# -----------------------------------------------------------------------------


class ManualEntryRequest(BaseModel):
    """Body for ``POST /manual-entries`` (E.8).

    Mirrors ``openapi.yaml`` ``ManualEntryRequest`` exactly:
    ``category_id`` + ``entry_type`` + ``amount`` + ``payment_method_id``
    are required; ``entry_date`` and ``notes`` are optional.
    """

    model_config = ConfigDict(extra="forbid")

    category_id: int = Field(..., ge=1)
    entry_type: FinancialEntryType
    amount: float = Field(..., gt=0, le=10000000000000.0)
    payment_method_id: int = Field(..., ge=1)
    entry_date: datetime | None = None
    notes: str | None = Field(default=None, max_length=2000)


class ManualEntryCancelRequest(BaseModel):
    """Body for ``POST /manual-entries/{id}/cancel`` (E.8)."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=1000)


class ManualEntryResponse(BaseModel):
    """Response for E.8 endpoints (matches ``ManualEntry``)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    category_id: int
    category_name: str | None = None
    entry_type: FinancialEntryType
    amount: float
    payment_method_id: int
    entry_date: datetime
    notes: str | None = None
    lifecycle_status: str
    cancellation_date: datetime | None = None
    cancellation_reason: str | None = None
    cancelled_by: int | None = None
    created_at: datetime
    created_by: int


# ----------------------------------------------------------------------------
# Cash Movement (E.9 — read-only ledger)
# ----------------------------------------------------------------------------


class CashMovement(BaseModel):
    """Response for ``listCashMovements``. Mirrors openapi.yaml §15.2."""

    model_config = ConfigDict(extra="forbid")

    id: int
    movement_date: datetime
    amount: float
    direction: str
    trigger: str
    payment_method_id: int
    reference_type: str | None = None
    reference_id: int | None = None
    created_at: datetime
    created_by: int


class CashBalance(BaseModel):
    """Response for ``getCashBalance``. Mirrors openapi.yaml §15.2."""

    model_config = ConfigDict(extra="forbid")

    balance: float
    as_of: datetime


# Re-export a generic any-type alias for flexible dict responses.
AnyBody = dict[str, Any]


__all__ = [
    "AnyBody",
    "CashBalance",
    "CashMovement",
    "DeactivateRequest",
    "FinancialCategoryBase",
    "FinancialCategoryPatch",
    "FinancialCategoryRequest",
    "FinancialCategoryResponse",
    "ManualEntryCancelRequest",
    "ManualEntryRequest",
    "ManualEntryResponse",
    "MetaEnvelope",
    "PaymentMethodBase",
    "PaymentMethodPatch",
    "PaymentMethodRequest",
    "PaymentMethodResponse",
]
