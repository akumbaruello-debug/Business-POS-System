"""F.6 — Export job API routes.

Per ``openapi.yaml`` (lines 5689-5794):

* ``POST /exports``        — create an export job (export.data, Idempotency-Key)
* ``GET /exports/{id}``    — get export job status (export.data)
* ``GET /exports/{id}/download`` — download the file (export.data)

Business rules:
* ``POST /exports`` requires ``export.data`` capability + ``Idempotency-Key``.
* ``GET /exports/{id}`` and ``GET /exports/{id}/download`` require
  ``export.data`` capability (no Idempotency-Key).
* ``GET /exports/{id}/download`` returns 404 if the job doesn't exist,
  or 410 if the job expired/was pruned.
* Rate limit: 5/hour/user (documented in OAS ``x-rate-limit`` but not
  enforced in this phase — slowapi infrastructure is wired only).

Architecture:
    Route -> ExportService -> (idempotency via IdempotencyStore)
                                -> in-memory ExportStore
                                -> existing report services
                                -> database (UoW)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.deps import (
    current_principal,
    get_uow,
    require_capability,
)
from app.auth.principal import Principal
from app.db import UnitOfWork
from app.services.exports import ExportService
from app.validation.exports_schemas import (
    ExportCreateRequest,
    ExportJob,
    ExportStatus,
)
from app.validation.headers import parse_idempotency_key

RequireExport = require_capability("export.data")

logger = __import__("logging").getLogger(__name__)

router = APIRouter(prefix="/exports", tags=["Exports"])


def _to_job_response(job: Any) -> ExportJob:
    """Convert an ExportJobRecord to the OpenAPI ExportJob schema."""
    return ExportJob(
        export_id=job.export_id,
        status=ExportStatus(job.status),
        download_url=job.download_url,
        expires_at=job.expires_at,
        error=job.error,
        created_at=job.created_at,
    )


@router.post(
    "",
    operation_id="createExport",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(RequireExport)],
)
async def create_export(
    request: Request,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> ExportJob:
    """Create an export job. Idempotency-Key required."""
    # Validate Idempotency-Key presence + format.
    idem = parse_idempotency_key(idempotency_key_header)
    key_str = str(idem.value)

    body = await request.json()
    payload = ExportCreateRequest(**body)

    svc = ExportService(uow)
    job = await svc.create_export(
        report=payload.report.value,
        fmt=payload.format.value,
        filters=payload.filters,
        idempotency_key=key_str,
        user_id=principal.user_id,
        request_body=body,
    )
    return _to_job_response(job)


@router.get(
    "/{id}",
    operation_id="getExport",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireExport)],
)
async def get_export(
    request: Request,
    id: str,
    uow: UnitOfWork = Depends(get_uow),
) -> ExportJob:
    """Get export job status and (when complete) download URL."""
    svc = ExportService(uow)
    job = svc.get_export(id)
    return _to_job_response(job)


@router.get(
    "/{id}/download",
    operation_id="downloadExport",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireExport)],
)
async def download_export(
    request: Request,
    id: str,
    uow: UnitOfWork = Depends(get_uow),
) -> Response:
    """Download the export file (streams binary content).

    Returns 202 if the job exists but isn't ready yet, 200 with
    file content when complete, 404 if the job doesn't exist.
    """
    svc = ExportService(uow)
    file_bytes, filename, content_type, is_ready = svc.get_download(id)

    if not is_ready:
        # Job exists but not ready yet — 202 Accepted.
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "error": {
                    "code": "export_not_ready",
                    "message": "Export is still being processed.",
                }
            },
        )

    return StreamingResponse(
        iter([file_bytes]),
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


__all__ = ["router"]
