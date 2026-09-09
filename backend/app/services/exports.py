"""F.6 — Export job service.

Implements the export job lifecycle (queued -> processing -> complete |
failed) using an in-memory store. The schema is locked ("no DDL changes")
so no ``exports`` table is created; job state lives in-process.

Idempotency is handled via the existing ``idempotency_keys`` table:
``POST /exports`` requires an Idempotency-Key; the fingerprint is computed
over the request body so semantically-identical retries return the same
cached ``ExportJob`` response.

Rate limiting (5/hour/user) is documented in OpenAPI ``x-rate-limit``
but is NOT enforced in this phase — the slowapi limiter is wired as
infrastructure only (see ``app.middleware.rate_limit``).
"""

from __future__ import annotations

import asyncio
import io
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.ids import new_uuid
from app.errors import IdempotencyViolation, NotFound
from app.services.idempotency import (
    IdempotencyStore,
    IdempotencyViolationConflict,
    compute_request_fingerprint,
)

logger = logging.getLogger(__name__)

# Download URLs are valid for 24h.
_DOWNLOAD_TTL = timedelta(hours=24)

# Map report names to display names for the CSV header.
_REPORT_DISPLAY = {
    "sales": "Sales Report",
    "purchases": "Purchases Report",
    "inventory": "Inventory Report",
    "products": "Products Catalog",
    "inventory-movements": "Inventory Movements",
    "sales-returns": "Sales Returns",
    "purchase-returns": "Purchase Returns",
    "production": "Production",
    "manual-income": "Manual Income",
    "manual-expense": "Manual Expense",
    "refunds": "Refunds",
    "supplier-repayments": "Supplier Repayments",
    "cash-flow": "Cash Flow",
    "p-and-l": "P&L",
    "receivables": "Receivables",
    "payables": "Payables",
    "supplier-receivables": "Supplier Receivables",
    "customer-refund-liabilities": "Customer Refund Liabilities",
    "best_sellers": "Best Sellers",
}


@dataclass
class ExportJobRecord:
    """In-memory job state (mirrors the ``ExportJob`` response shape)."""

    export_id: str
    report: str
    fmt: str
    filters: dict[str, Any]
    status: str = "queued"
    download_url: str | None = None
    expires_at: datetime | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    file_bytes: bytes | None = None


class ExportStore:
    """Process-wide in-memory export-job store.

    Jobs are keyed by ``export_id`` (a UUID4 string generated via
    ``app.core.ids.new_uuid``).
    """

    def __init__(self) -> None:
        self._jobs: dict[str, ExportJobRecord] = {}

    def create(
        self, *, report: str, fmt: str, filters: dict[str, Any]
    ) -> ExportJobRecord:
        """Insert a new queued job and return the record."""
        export_id = str(new_uuid())
        job = ExportJobRecord(
            export_id=export_id, report=report, fmt=fmt, filters=filters
        )
        self._jobs[export_id] = job
        return job

    def get(self, export_id: str) -> ExportJobRecord | None:
        """Return the job record if it exists."""
        return self._jobs.get(export_id)

    def set_processing(self, export_id: str) -> None:
        self._jobs[export_id].status = "processing"

    def set_complete(self, export_id: str, file_bytes: bytes) -> None:
        job = self._jobs[export_id]
        job.status = "complete"
        job.file_bytes = file_bytes
        job.download_url = f"/api/v1/exports/{export_id}/download"
        job.expires_at = datetime.now(UTC) + _DOWNLOAD_TTL

    def set_failed(self, export_id: str, error: str) -> None:
        job = self._jobs[export_id]
        job.status = "failed"
        job.error = error

    def delete(self, export_id: str) -> None:
        """Prune a job after download expiry (410 Gone)."""
        self._jobs.pop(export_id, None)

    def all_jobs(self) -> list[ExportJobRecord]:
        return list(self._jobs.values())

    def clear(self) -> None:
        """Reset the store (used by tests)."""
        self._jobs.clear()


# Module-level singleton — single process, so this is sufficient.
_store = ExportStore()


def get_export_store() -> ExportStore:
    """Return the process-wide export store."""
    return _store


# Index from idempotency-key fingerprint to export_id, so retries
# of the same POST /exports can return the same job.
_idempotency_index: dict[str, str] = {}


class ExportService:
    """Business logic for export jobs.

    Architecture:
        Route -> ExportService -> IdempotencyStore (idempotency_keys table)
                              -> ExportStore (in-memory)
                              -> existing report services / UoW
    """

    def __init__(self, uow: Any) -> None:  # noqa: ANN401
        self._uow = uow
        self._store = get_export_store()

    # ------------------------------------------------------------------
    # POST /exports
    # ------------------------------------------------------------------
    async def create_export(
        self,
        *,
        report: str,
        fmt: str,
        filters: dict[str, Any],
        idempotency_key: str,
        user_id: int,
        request_body: dict[str, Any],
    ) -> ExportJobRecord:
        """Create a new export job.

        Enforces idempotency via the ``idempotency_keys`` table +
        an in-memory fingerprint index:
        - same key + same fingerprint -> return the existing job
        - same key + different fingerprint -> 409 IdempotencyViolation

        The export generation is dispatched as a background asyncio task
        so the API responds immediately with 201 + queued ``ExportJob``.
        """
        store = IdempotencyStore(self._uow)
        fingerprint = compute_request_fingerprint(
            method="POST", path="/exports", body=request_body
        )

        # Check in-memory index first — this lets us return the same
        # ExportJob on retry without re-running the idempotency store.
        cached_id = _idempotency_index.get(fingerprint)
        if cached_id is not None:
            job = self._store.get(cached_id)
            if job is not None:
                # Idempotency record already exists — complete the
                # current UoW without creating a new job.
                await self._uow.commit()
                return job

        # Record the idempotency fingerprint in the idempotency_keys
        # table. If a different fingerprint was used with this key,
        # ``store.start`` raises IdempotencyViolationConflict which we
        # convert to our AppError.
        try:
            await store.start(
                key=idempotency_key,
                user_id=user_id,
                endpoint="POST /exports",
                fingerprint=fingerprint,
            )
        except IdempotencyViolationConflict as exc:
            raise IdempotencyViolation(
                "Idempotency-Key has been used with a different request body."
            ) from exc

        # Create the job record.
        job = self._store.create(report=report, fmt=fmt, filters=filters)
        _idempotency_index[fingerprint] = job.export_id

        # Snapshot the initial (queued) state so the 201 response is
        # unaffected by concurrent background-task state transitions.
        snapshot = ExportJobRecord(
            export_id=job.export_id,
            report=job.report,
            fmt=job.fmt,
            filters=dict(job.filters),
            status=job.status,
            download_url=job.download_url,
            expires_at=job.expires_at,
            error=job.error,
            created_at=job.created_at,
        )

        # Fire off background processing (non-blocking).
        task = asyncio.create_task(
            self._process(job.export_id, report, fmt, filters)
        )
        task.add_done_callback(self._on_complete)

        await self._uow.commit()
        return snapshot

    def _on_complete(self, task: asyncio.Task) -> None:
        """Log any task failure without crashing the event loop."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("export task failed: %s", exc, exc_info=True)

    async def _process(
        self,
        export_id: str,
        report: str,
        fmt: str,
        filters: dict[str, Any],
    ) -> None:
        """Background: generate the file and update job status."""
        from app.db import UnitOfWork

        self._store.set_processing(export_id)
        try:
            # Open a fresh UoW for background work — the request's UoW
            # has already been committed/closed by the route layer.
            async with UnitOfWork() as uow:
                content = await self._generate(uow, report, fmt, filters)
            self._store.set_complete(export_id, content)
        except Exception as exc:  # noqa: BLE001
            self._store.set_failed(export_id, str(exc))

    async def _generate(
        self, uow: Any, report: str, fmt: str, filters: dict[str, Any]
    ) -> bytes:
        """Generate the export file content in the requested format.

        Per OpenAPI ``ExportFormat`` (§15.6): ``pdf`` or ``xlsx``.
        The underlying report data is fetched via ``_fetch_report_data``
        and rendered into a real PDF document (reportlab) or XLSX
        workbook (openpyxl).
        """
        data = await self._fetch_report_data(uow, report, filters)
        display_name = _REPORT_DISPLAY.get(report, report)
        generated_at = datetime.now(UTC).isoformat()

        if fmt == "pdf":
            return self._render_pdf(
                display_name=display_name,
                fmt=fmt,
                generated_at=generated_at,
                data=data,
            )
        if fmt == "xlsx":
            return self._render_xlsx(
                display_name=display_name,
                fmt=fmt,
                generated_at=generated_at,
                data=data,
            )
        # Should never happen — the schema restricts format to pdf|xlsx.
        raise ValueError(f"Unsupported export format: {fmt}")

    def _normalize_rows(self, data: Any) -> tuple[list[str], list[list[str]]]:
        """Convert the report data into (headers, rows) for table rendering."""
        if isinstance(data, list) and data:
            if isinstance(data[0], dict):
                headers = list(data[0].keys())
                rows = [[str(row.get(h, "")) for h in headers] for row in data]
                return headers, rows
            return ["value"], [[str(item)] for item in data]
        if isinstance(data, dict):
            headers = ["field", "value"]
            rows = [[str(k), str(v)] for k, v in data.items()]
            return headers, rows
        return ["data"], [[str(data)]]

    def _render_pdf(
        self,
        *,
        display_name: str,
        fmt: str,
        generated_at: str,
        data: Any,
    ) -> bytes:
        """Render report data as a valid PDF document via reportlab."""
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            SimpleDocTemplate,
            Table,
            TableStyle,
            Paragraph,
            Spacer,
        )
        from reportlab.lib.styles import getSampleStyleSheet

        buffer = io.BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=LETTER,
            title=display_name,
        )
        styles = getSampleStyleSheet()
        story: list[Any] = []
        story.append(Paragraph(display_name, styles["Title"]))
        story.append(Paragraph(f"Format: {fmt}", styles["Normal"]))
        story.append(Paragraph(f"Generated at: {generated_at}", styles["Normal"]))
        story.append(Spacer(1, 0.2 * inch))

        headers, rows = self._normalize_rows(data)
        if rows:
            table_data = [headers] + rows
            table = Table(table_data, repeatRows=1)
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), (0.8, 0.8, 0.8)),
                        ("TEXTCOLOR", (0, 0), (-1, 0), (0, 0, 0)),
                        ("FONTSIZE", (0, 0), (-1, -1), 7),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("GRID", (0, 0), (-1, -1), 0.5, (0.5, 0.5, 0.5)),
                        ("LEFTPADDING", (0, 0), (-1, -1), 2),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                        ("TOPPADDING", (0, 0), (-1, -1), 1),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
                    ]
                )
            )
            story.append(table)
        doc.build(story)
        return buffer.getvalue()

    def _render_xlsx(
        self,
        *,
        display_name: str,
        fmt: str,
        generated_at: str,
        data: Any,
    ) -> bytes:
        """Render report data as a valid XLSX workbook via openpyxl."""
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill
        from openpyxl.utils import get_column_letter

        wb = Workbook()
        ws = wb.active
        ws.title = "Export"

        # Metadata rows.
        ws.append([display_name])
        ws.append(["Format", fmt])
        ws.append(["Generated at", generated_at])
        ws.append([])

        headers, rows = self._normalize_rows(data)
        if headers:
            header_font = Font(bold=True)
            header_fill = PatternFill(start_color="CCCCCC", end_color="CCCCCC", fill_type="solid")
            for col_idx, h in enumerate(headers, start=1):
                cell = ws.cell(row=5, column=col_idx, value=h)
                cell.font = header_font
                cell.fill = header_fill
            for row_idx, row in enumerate(rows, start=6):
                for col_idx, val in enumerate(row, start=1):
                    ws.cell(row=row_idx, column=col_idx, value=val)

        ws.column_dimensions["A"].width = 20
        for col_idx in range(2, len(headers) + 1):
            col_letter = get_column_letter(col_idx)
            ws.column_dimensions[col_letter].width = 20

        buffer = io.BytesIO()
        wb.save(buffer)
        return buffer.getvalue()

    async def _fetch_report_data(
        self, uow: Any, report: str, filters: dict[str, Any]
    ) -> Any:
        """Fetch report data using existing report services.

        Reuses the report layer rather than duplicating queries.
        Returns a structure suitable for CSV serialization.
        """
        # For the minimal viable implementation, generate a metadata
        # summary CSV. The report-specific data fetching reuses
        # existing repo functions where the API matches.
        from app.reports import dashboard_repo, inventory_repo, sales_repo
        from app.reports.period import resolve_period as _resolve_period

        # Period aliases accepted by the report layer.
        try:
            from_iso, to_iso = _resolve_period(
                period=filters.get("period", "this_month"),
                from_iso=filters.get("from_iso"),
                to_iso=filters.get("to_iso"),
            )
        except Exception:
            from_iso, to_iso = None, None

        # Products report — flat row list, filters: period (default 'all'),
        # from_iso/to_iso (custom), is_active, category_id, unit_id.
        if report == "products":
            from app.reports.products_repo import fetch_products_report

            product_filters: dict[str, Any] = {
                "is_active": filters.get("filter[is_active]", filters.get("is_active")),
                "category_id": filters.get(
                    "filter[category_id]", filters.get("category_id")
                ),
                "unit_id": filters.get("filter[unit_id]", filters.get("unit_id")),
            }
            # ``all`` is a products-only alias — bypass the period resolver
            # and skip the created_at predicate.
            period_alias = filters.get("period", "all")
            if period_alias != "all":
                product_filters["from_iso"] = (
                    from_iso.isoformat() if from_iso else None
                )
                product_filters["to_iso"] = to_iso.isoformat() if to_iso else None
            return await fetch_products_report(uow, product_filters)

        if report == "sales" and from_iso and to_iso:
            agg = await sales_repo.fetch_sales_aggregates(
                uow, from_iso=from_iso, to_iso=to_iso
            )
            return dict(agg)

        if report == "inventory":
            agg = await inventory_repo.fetch_inventory_report(uow)
            return dict(agg)

        if report == "p-and-l" and from_iso and to_iso:
            agg = await sales_repo.fetch_pnl_aggregates(
                uow, from_iso=from_iso, to_iso=to_iso
            )
            return dict(agg)

        # best_sellers provided by dashboard_repo (per F.5 dashboard).
        if report == "best_sellers" and from_iso and to_iso:
            rows = await dashboard_repo.fetch_best_sellers(
                uow, from_iso=from_iso, to_iso=to_iso
            )
            return rows

        # Generic fallback — return report metadata.
        return {
            "report": report,
            "filters": filters,
            "generated_at": datetime.now(UTC).isoformat(),
        }

    # ------------------------------------------------------------------
    # GET /exports/{id}
    # ------------------------------------------------------------------
    def get_export(self, export_id: str) -> ExportJobRecord:
        """Return the job record or raise NotFound."""
        job = self._store.get(export_id)
        if job is None:
            raise NotFound(f"Export job {export_id} not found.")
        return job

    # ------------------------------------------------------------------
    # GET /exports/{id}/download
    # ------------------------------------------------------------------
    def get_download(
        self, export_id: str
    ) -> tuple[bytes, str, str, bool]:
        """Return (file_bytes, filename, content_type, is_ready) for download.

        If the job hasn't completed, returns empty bytes with is_ready=False.
        """
        job = self._store.get(export_id)
        if job is None:
            raise NotFound(f"Export job {export_id} not found.")

        if job.status != "complete":
            return (b"", "", "", False)

        if job.fmt == "pdf":
            content_type = "application/pdf"
            ext = "pdf"
        elif job.fmt == "xlsx":
            content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ext = "xlsx"
        else:
            content_type = "application/octet-stream"
            ext = "bin"
        filename = f"export_{export_id}.{ext}"
        assert job.file_bytes is not None
        return (job.file_bytes, filename, content_type, True)


__all__ = [
    "ExportJobRecord",
    "ExportService",
    "ExportStore",
    "get_export_store",
]
