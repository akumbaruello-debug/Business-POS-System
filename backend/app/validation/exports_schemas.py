"""F.6 — Export job schemas.

Mirrors ``openapi.yaml`` ``ExportCreateRequest`` (9177) and
``ExportJob`` (9211) component definitions.

Business rules:
* ``report`` is the report type to export (16 enumerated types).
* ``format`` is the output format (pdf or xlsx).
* ``filters`` is a free-form object that mirrors the report query
  parameters (``period``, ``compare_to``, ``q``, ``filter[...]``).
* ``ExportJob.status`` transitions: queued -> processing -> complete | failed.
* ``download_url`` and ``expires_at`` are ``null`` until the job completes.
* ``error`` is ``null`` unless the job failed.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ReportType(str, enum.Enum):
    """Report types that can be exported, per OpenAPI ExportCreateRequest."""

    SALES = "sales"
    PURCHASES = "purchases"
    INVENTORY = "inventory"
    INVENTORY_MOVEMENTS = "inventory-movements"
    SALES_RETURNS = "sales-returns"
    PURCHASE_RETURNS = "purchase-returns"
    PRODUCTION = "production"
    MANUAL_INCOME = "manual-income"
    MANUAL_EXPENSE = "manual-expense"
    REFUNDS = "refunds"
    SUPPLIER_REPAYMENTS = "supplier-repayments"
    CASH_FLOW = "cash-flow"
    P_AND_L = "p-and-l"
    RECEIVABLES = "receivables"
    PAYABLES = "payables"
    SUPPLIER_RECEIVABLES = "supplier-receivables"
    CUSTOMER_REFUND_LIABILITIES = "customer-refund-liabilities"


class ExportFormat(str, enum.Enum):
    """Export formats per OpenAPI ExportCreateRequest."""

    PDF = "pdf"
    XLSX = "xlsx"


class ExportStatus(str, enum.Enum):
    """Export job lifecycle states per OpenAPI ExportJob."""

    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETE = "complete"
    FAILED = "failed"


class ExportCreateRequest(BaseModel):
    """Body for ``POST /exports``. Mirrors openapi.yaml ExportCreateRequest."""

    model_config = ConfigDict(extra="forbid")

    report: ReportType
    format: ExportFormat = Field(default=ExportFormat.PDF)
    filters: dict[str, Any] = Field(default_factory=dict)


class ExportJob(BaseModel):
    """Response for export endpoints. Mirrors openapi.yaml ExportJob."""

    model_config = ConfigDict(extra="forbid")

    export_id: str
    status: ExportStatus
    download_url: str | None = None
    expires_at: datetime | None = None
    error: str | None = None
    created_at: datetime


__all__ = [
    "ExportCreateRequest",
    "ExportFormat",
    "ExportJob",
    "ExportStatus",
    "ReportType",
]
