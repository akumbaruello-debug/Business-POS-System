"""Pydantic request/response schemas for the inventory domain
(M5 / Phase E — E.5 reads + E.6 write).

Mirrors ``openapi.yaml`` §15.2 (Inventory) exactly. ``extra='forbid'`` is
used everywhere so unknown fields raise ``validation_failed`` at parse
time (per the project convention established by the M4 schemas).

E.6 stock adjustment (this file's main addition):

* ``StockAdjustmentRequest`` — body for ``POST /inventory/adjustments``.
  Required: ``product_id`` (≥1), ``quantity`` (signed ≠0), ``reason``
  (1-1000 chars). Mirrors the OpenAPI ``StockAdjustmentRequest``
  schema lines 8388-8406.
* ``StockMovement`` — response shape for the same endpoint (also the
  shared ledger row shape referenced elsewhere). Mirrors the OpenAPI
  ``StockMovement`` schema lines 8296-8387.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StockAdjustmentRequest(BaseModel):
    """Body for ``createStockAdjustment``. Mirrors openapi.yaml §15.2.

    Per BR-STOCK-008: ``reason`` is required and stored on both the
    movement row and the audit row (``audit_log.new_values.reason`` +
    ``audit_log.reason``). The Pydantic ``min_length=1`` +
    ``max_length=1000`` mirrors the OpenAPI schema constraints; a
    missing/empty reason surfaces as 400 ``validation_failed`` at the
    parse layer (handled by FastAPI's standard 422 → envelope
    translation).
    """

    model_config = ConfigDict(extra="forbid")

    product_id: int = Field(..., ge=1)
    # Quantity must be a non-zero signed delta. The OpenAPI contract
    # uses ``exclusiveMinimum``/``maximum`` for the ±1e12 band; the
    # additional ``not-equal-to-0`` rule is required by the DB
    # constraint ``ck_sm_quantity`` (which only permits 0 for
    # ``value_adjustment`` triggers) and ``ck_sm_total_cost_nonzero``.
    # We surface this as a 400 ``validation_failed`` so the client
    # gets a clean envelope rather than a 500 from a check_violation.
    quantity: float = Field(
        ...,
        gt=-1_000_000_000_000.0,
        lt=1_000_000_000_000.0,
        description=(
            "Signed delta in base units; must be non-zero. "
            "Negative values reduce on-hand (subject to "
            "allow_negative_stock or the system default)."
        ),
    )
    reason: str = Field(..., min_length=1, max_length=1000)

    @field_validator("quantity")
    @classmethod
    def _qty_not_zero(cls, v: float) -> float:
        if v == 0:
            raise ValueError(
                "quantity must be non-zero (a zero adjustment is not "
                "representable in the stock_movements ledger)."
            )
        return v


class StockMovement(BaseModel):
    """Response for ``createStockAdjustment``. Mirrors openapi.yaml §15.2.

    Every field is optional except the required
    ``id``, ``product_id``, ``movement_date``, ``trigger``,
    ``quantity``, ``created_at``, ``created_by``. The Pydantic model
    enforces those required slots; the optional ones (cost snapshots,
    reference pointers, reason) pass through as ``None`` when not set.

    Movements are immutable (schema §8.1 + DB trigger blocks UPDATE /
    DELETE except for system-defined reversal routines). The DB is the
    source of truth; this model only shapes the response payload.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    product_id: int
    movement_date: datetime
    trigger: str
    quantity: float
    unit_cost_at_movement: float | None = None
    total_cost: float | None = None
    reference_type: str | None = None
    reference_id: int | None = None
    reference_line_id: int | None = None
    reversal_of_movement_id: int | None = None
    reversed_by_movement_id: int | None = None
    reason: str | None = None
    created_at: datetime
    created_by: int


__all__ = [
    "StockAdjustmentRequest",
    "StockMovement",
]
