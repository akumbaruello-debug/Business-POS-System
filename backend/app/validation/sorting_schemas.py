from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class SortingOutputInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: int = Field(..., ge=1)
    quantity: Decimal = Field(..., gt=0, max_digits=15, decimal_places=4)


class SortingEventInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pending_receipt_batch_id: int = Field(..., ge=1)
    input_quantity: Decimal = Field(..., gt=0, max_digits=15, decimal_places=4)
    loss_quantity: Decimal = Field(default=Decimal("0"), ge=0, max_digits=15, decimal_places=4)
    remaining_quantity: Decimal = Field(default=Decimal("0"), ge=0, max_digits=15, decimal_places=4)
    financial_category_id: int | None = Field(default=None, ge=1)
    reason: str = Field(..., min_length=1, max_length=1000)
    outputs: list[SortingOutputInput] = Field(..., min_length=1)


class SortingEventReverseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(..., min_length=1, max_length=1000)


class SortingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    sorting_event_id: int
    product_id: int
    product_name: str | None = None
    quantity: float
    unit_id: int
    unit_code_snapshot: str
    factor_to_base_snapshot: float
    base_quantity: float
    value_snapshot: float
    line_number: int
    stock_movement_id: int | None = None


class SortingEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    pending_receipt_batch_id: int
    input_quantity: float
    loss_quantity: float
    remaining_quantity: float
    source_value_snapshot: float | None = None
    loss_value_snapshot: float | None = None
    financial_category_id: int | None = None
    reason: str
    created_at: datetime
    created_by: int
    posted_at: datetime | None = None
    posted_by: int | None = None
    version: int
    reversed: bool = False
    outputs: list[SortingOutput] = Field(default_factory=list)


__all__ = ["SortingEvent", "SortingEventInput", "SortingEventReverseRequest", "SortingOutput", "SortingOutputInput"]
