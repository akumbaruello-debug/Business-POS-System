"""Request/response schemas for Products (M2 / B.6).

Mirrors ``openapi.yaml`` §15.7 (Products tag) + Database-Design §3.1
(schema.sql §5.3).

Known schema/DB contradictions (documented, NOT silently resolved):

* The OpenAPI ``Product`` schema includes a required ``version`` field.
  The locked ``products`` DB table has NO ``version`` column (uses
  ``updated_at``). We derive ``version`` app-side via
  ``etag_and_version_from_updated_at`` (see Backend-Architecture §12).

* The OpenAPI schemas use ``integer`` for ``purchase_price`` /
  ``selling_price`` / ``low_stock_threshold`` / ``on_hand_quantity``.
  The locked DB uses ``NUMERIC(15,2)`` for prices and ``NUMERIC(15,4)``
  for the threshold. We model prices as ``float`` in the Pydantic layer
  (JSON serializes NUMERIC as a number, not string, via SQLAlchemy/asyncpg).

* ``created_by`` / ``updated_by`` are NOT NULL on the DB and reference
  ``users(id)``. The OpenAPI lists them as optional (nullable). We model
  them as ``int | None`` with a default of None so the contract is honored
  when the DB column is populated by the authenticated principal.

* ``code`` is immutable via PATCH (OpenAPI ``ProductPatch`` excludes it;
  ``extra='forbid'`` enforces this). The DB column is nullable + partial
  unique (``ux_products_code WHERE code IS NOT NULL``).

* ``low_stock`` is computed: ``on_hand_quantity <= low_stock_threshold``
  when threshold is not NULL.
* ``negative_stock_fallback_supported`` is derived from:
  ``product.allow_negative_stock OR system_settings.default_negative_stock_allowed``.
* Price changes on PATCH insert a ``product_price_history`` row (BR-PRODUCT-002;
  no DB trigger → service layer owns this).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProductBase(BaseModel):
    """Common fields shared by create/patch/response."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    code: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
        description="Product code (1-64 chars, alphanumeric + dot + underscore + hyphen).",
    )
    category_id: int | None = Field(default=None, ge=1)
    unit_id: int | None = Field(default=None, ge=1)
    purchase_price: float | None = Field(
        default=None, ge=0, description="Purchase price in minor units (cents)."
    )
    selling_price: float | None = Field(
        default=None, ge=0, description="Selling price in minor units (cents)."
    )
    low_stock_threshold: float | None = Field(default=None, ge=0)
    allow_negative_stock: bool | None = None
    notes: str | None = Field(default=None, max_length=2000)
    is_sellable: bool | None = None
    is_purchasable: bool | None = None
    is_producible: bool | None = None
    is_active: bool | None = None


class ProductCreateRequest(ProductBase):
    """Body for ``POST /products``.

    Only ``name`` is required. Defaults per OpenAPI §15.7:
    - ``purchase_price=0``
    - ``selling_price=0``
    - ``is_sellable=true``
    - ``is_purchasable=true``
    - ``is_producible=false``
    - ``is_active=true``
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=200)
    purchase_price: float = Field(default=0, ge=0)
    selling_price: float = Field(default=0, ge=0)
    is_sellable: bool = True
    is_purchasable: bool = True
    is_producible: bool = False
    is_active: bool = True


class ProductPatch(BaseModel):
    """Body for ``PATCH /products/{id}``.

    Per OpenAPI §15.7 schema ``ProductPatch``: ``code`` is NOT exposed
    (code is immutable through the public API, even though the DB
    column itself is nullable + mutable). ``extra='forbid'`` enforces
    this — sending ``code`` yields 400 ``validation_failed``.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    category_id: int | None = Field(default=None, ge=1)
    unit_id: int | None = Field(default=None, ge=1)
    purchase_price: float | None = Field(default=None, ge=0)
    selling_price: float | None = Field(default=None, ge=0)
    low_stock_threshold: float | None = Field(default=None, ge=0)
    allow_negative_stock: bool | None = None
    notes: str | None = Field(default=None, max_length=2000)
    is_sellable: bool | None = None
    is_purchasable: bool | None = None
    is_producible: bool | None = None
    is_active: bool | None = None


class ProductPriceHistory(BaseModel):
    """Response for ``GET /products/{id}/price-history`` (B.7 scope)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    purchase_price: float
    selling_price: float
    effective_at: str  # ISO 8601 datetime string
    changed_by: int


class StockMovement(BaseModel):
    """Response for ``GET /products/{id}/stock-movements`` (B.7 scope).

    Per OpenAPI §15.7 ``StockMovement``. The DB stores ``quantity`` as
    ``NUMERIC(15,4)`` and ``unit_cost_at_movement`` as ``NUMERIC(15,4)``;
    ``total_cost`` as ``NUMERIC(15,2)``. We model both as ``float`` to
    match the OpenAPI ``type: number``. The ``trigger`` field uses plain
    ``str`` (not a closed Literal) because of contradiction C-1: the
    OpenAPI enum and the DB ``ck_sm_trigger`` CHECK constraint diverge
    (OpenAPI has ``production_output_reversal``; DB has
    ``production_reversal``). Using ``str`` lets the endpoint faithfully
    emit whatever the DB stores without rejecting valid rows.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    product_id: int
    movement_date: str  # ISO 8601 date-time
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
    created_at: str  # ISO 8601 datetime string
    created_by: int


class ProductValuation(BaseModel):
    """Response for ``GET /products/{id}/valuation`` (B.7 scope)."""

    model_config = ConfigDict(from_attributes=True)

    product_id: int
    on_hand_quantity: int
    inventory_value: float
    as_of: str  # ISO 8601 datetime string
    moving_average_unit_cost: float | None = None


class DeactivateRequest(BaseModel):
    """Body for ``POST /products/{id}/deactivate``."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=1000)


class ProductResponse(BaseModel):
    """Response for product endpoints (matches ``Product`` in openapi.yaml).

    The OpenAPI-required ``version`` is derived from ``updated_at`` via
    ``etag_and_version_from_updated_at`` (no DB ``version`` column).
    ``on_hand_quantity``, ``moving_average_unit_cost``, ``inventory_value``,
    ``low_stock``, and ``negative_stock_fallback_supported`` are computed
    from the ``product_valuation`` view + product row + system settings.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str | None
    category_id: int | None
    unit_id: int | None
    purchase_price: float
    selling_price: float
    low_stock_threshold: float | None
    allow_negative_stock: bool | None
    notes: str | None
    is_sellable: bool
    is_purchasable: bool
    is_producible: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime
    version: int
    on_hand_quantity: int
    moving_average_unit_cost: float | None
    inventory_value: float
    low_stock: bool
    negative_stock_fallback_supported: bool
    created_by: int | None = None
    updated_by: int | None = None


class ProductImportResult(BaseModel):
    """Result of a single row import."""

    model_config = ConfigDict(extra="forbid")

    row: int  # 1-indexed row number in the file
    action: str  # "created" or "skipped" or "failed"
    product_id: int | None = None
    errors: list[str] = Field(default_factory=list)


class ProductImportReport(BaseModel):
    """Response for POST /products/import."""

    model_config = ConfigDict(extra="forbid")

    total_rows: int
    created: int
    skipped: int
    failed: int
    results: list[ProductImportResult] = Field(default_factory=list)


__all__ = [
    "DeactivateRequest",
    "ProductBase",
    "ProductCreateRequest",
    "ProductImportReport",
    "ProductImportResult",
    "ProductPatch",
    "ProductPriceHistory",
    "ProductResponse",
    "ProductValuation",
    "StockMovement",
]
