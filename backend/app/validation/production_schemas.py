"""Pydantic request/response schemas for the production-runs domain
(M5 / Phase E.1).

Mirrors ``openapi.yaml`` §15 (Production Runs) + §15 (Production Inputs /
Cost Lines) + §15 (Production Output) exactly. ``extra='forbid'`` is used
everywhere so unknown fields raise ``validation_failed`` at parse time
(per the project convention established by the M4 schemas).

Lifecycle notes (per Backend-Architecture §15.10-§15.12 + DB trigger
``fn_production_lifecycle_terminal``):

* New runs are created in ``draft`` (DB default; never client-supplied).
* The draft endpoint (``PATCH /production-runs/{id}``) only mutates
  fields allowed while in draft (run_date, output_product_id,
  output_quantity, notes). Lifecycle transitions are NOT in E.1 — they
  belong to E.3 (post) and E.4 (cancel).
* The ``ProductionRun`` response shape REQUIRES ``inputs``, ``output``,
  and ``cost_lines`` as required fields (openapi.yaml §15). On a draft
  run with no overhead yet, ``cost_lines`` is ``[]`` and ``output`` is
  the denormalised finished-good snapshot.

Reconciliation notes (frozen, per the M4 schema-validation style):

* ``ProductionInput.line_cost`` is server-computed (``quantity x unit_cost_snapshot``).
* ``ProductionOutput.unit_cost_snapshot`` = ``production_runs.finished_unit_cost``
  at the time the run was last touched; ``total_cost`` = ``unit_cost_snapshot x quantity``.
* ``ProductionRun.finished_unit_cost`` is initially the best-effort
  computation from the supplied inputs + zero overhead. It is NOT the
  authoritative post-time value (that is computed at post per Architecture
  §15.8). The draft field is a stable placeholder so the CHECK constraint
  ``ck_prun_finished_unit_cost_pos`` passes for every draft.
* Cost-line ``paid_in_cash`` defaults to ``True`` (per PRD §15 + E.2
  plan), but E.1 only stores it — no cash movement is written until E.3.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# ----------------------------------------------------------------------------
# Production Inputs (raw materials)
# ----------------------------------------------------------------------------


class ProductionInputRequest(BaseModel):
    """Body for ``ProductionInputRequest`` (used inline on create + E.2
    ``POST /production-runs/{id}/inputs``). Mirrors openapi.yaml §15.
    """

    model_config = ConfigDict(extra="forbid")

    product_id: int = Field(..., ge=1)
    quantity: float = Field(..., gt=0, le=1_000_000_000_000.0)


class ProductionInput(BaseModel):
    """Response for ``ProductionInput``. Mirrors openapi.yaml §15."""

    model_config = ConfigDict(extra="forbid")

    id: int
    production_run_id: int
    product_id: int
    quantity: float
    unit_cost_snapshot: float
    line_cost: float
    line_number: int = Field(..., ge=1)


# ----------------------------------------------------------------------------
# Production Output (finished-goods row)
# ----------------------------------------------------------------------------


class ProductionOutput(BaseModel):
    """Response for ``ProductionOutput``. Mirrors openapi.yaml §15.

    One row per run (DB UNIQUE constraint). ``unit_cost_snapshot`` mirrors
    the parent run's ``finished_unit_cost`` at the time it was written;
    ``total_cost`` = ``unit_cost_snapshot x quantity``.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    production_run_id: int
    product_id: int
    quantity: float
    unit_cost_snapshot: float
    total_cost: float


# ----------------------------------------------------------------------------
# Production Cost Lines (overhead)
# ----------------------------------------------------------------------------


class ProductionCostLineRequest(BaseModel):
    """Body for ``ProductionCostLineRequest``. Mirrors openapi.yaml §15.

    ``paid_in_cash`` defaults to ``True`` per PRD §15 / plan E.2.
    """

    model_config = ConfigDict(extra="forbid")

    cost_type_id: int = Field(..., ge=1)
    amount: float = Field(..., gt=0)
    paid_in_cash: bool = Field(default=True)
    description: str | None = Field(default=None, max_length=500)


class ProductionCostLine(BaseModel):
    """Response for ``ProductionCostLine``. Mirrors openapi.yaml §15."""

    model_config = ConfigDict(extra="forbid")

    id: int
    production_run_id: int
    cost_type_id: int
    description: str | None = None
    amount: float
    paid_in_cash: bool
    line_number: int = Field(..., ge=1)


# ----------------------------------------------------------------------------
# Production Runs (header)
# ----------------------------------------------------------------------------


class ProductionRunCreateRequest(BaseModel):
    """Body for ``createProductionRun``. Mirrors openapi.yaml §15.

    Required: ``output_product_id``, ``output_quantity``, ``inputs`` (>= 1).
    Optional: ``run_date`` (defaults to NOW()), ``notes``, ``cost_lines``.

    The output product must have ``is_producible = TRUE`` and
    ``is_active = TRUE``; the service enforces this before the row hits
    the DB.
    """

    model_config = ConfigDict(extra="forbid")

    run_date: datetime | None = None
    output_product_id: int = Field(..., ge=1)
    output_quantity: float = Field(..., gt=0, le=1_000_000_000_000.0)
    notes: str | None = Field(default=None, max_length=2000)
    inputs: list[ProductionInputRequest] = Field(..., min_length=1)
    cost_lines: list[ProductionCostLineRequest] | None = None


class ProductionRunPatch(BaseModel):
    """Body for ``updateProductionRunDraft``. Mirrors openapi.yaml §15.

    All fields optional; only the editable draft fields are exposed
    here. Lifecycle transitions are NOT in E.1.
    """

    model_config = ConfigDict(extra="forbid")

    run_date: datetime | None = None
    output_product_id: int | None = Field(default=None, ge=1)
    output_quantity: float | None = Field(
        default=None, gt=0, le=1_000_000_000_000.0
    )
    notes: str | None = Field(default=None, max_length=2000)


class ProductionCancelRequest(BaseModel):
    """Body for ``cancelProductionRun``. Mirrors openapi.yaml §15.

    ``reason`` is required (1-1000 chars), matching the
    ``cancellation_reason`` column on ``production_runs``.
    """

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=1000)


class ProductionRun(BaseModel):
    """Response for ``ProductionRun``. Mirrors openapi.yaml §15.

    ``inputs``, ``output``, ``cost_lines`` are required (non-nullable)
    per the OpenAPI schema. On a brand-new draft with no overhead yet,
    ``cost_lines`` is ``[]`` and ``output`` carries the initial
    finished-good snapshot.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    run_date: datetime
    output_product_id: int
    output_quantity: float
    finished_unit_cost: float
    total_raw_cost: float
    total_overhead_cost: float
    lifecycle_status: str
    posted_at: datetime | None = None
    posted_by: int | None = None
    cancellation_date: datetime | None = None
    cancellation_reason: str | None = None
    cancelled_by: int | None = None
    notes: str | None = None
    inputs: list[ProductionInput]
    output: ProductionOutput
    cost_lines: list[ProductionCostLine]
    created_at: datetime
    updated_at: datetime
    created_by: int
    version: int


# Resolve forward refs.
ProductionRun.model_rebuild()


__all__ = [
    "ProductionCancelRequest",
    "ProductionCostLine",
    "ProductionCostLineRequest",
    "ProductionInput",
    "ProductionInputRequest",
    "ProductionOutput",
    "ProductionRun",
    "ProductionRunCreateRequest",
    "ProductionRunPatch",
]
