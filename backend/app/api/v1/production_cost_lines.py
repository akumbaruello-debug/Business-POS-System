"""Production Cost Lines sub-resource API routes (M5 / Phase E.2).

Endpoints match ``openapi.yaml`` E.2 paths exactly:

* ``listProductionCostLines``  GET    /production-runs/{id}/cost-lines
* ``addProductionCostLine``    POST   /production-runs/{id}/cost-lines
* ``updateProductionCostLine`` PATCH  /production-runs/{id}/cost-lines/{line_id}
* ``deleteProductionCostLine`` DELETE /production-runs/{id}/cost-lines/{line_id}

Route layer is thin: authenticate, parse Pydantic body, build
:class:`AuditContext`, delegate to :class:`ProductionRunService`.

POST requires both ``Idempotency-Key`` + ``If-Match`` (per OAS
``IdempotencyKeyRequired`` + ``IfMatchRequired``). PATCH requires
``If-Match`` only. DELETE requires ``Idempotency-Key`` only. GET
requires ``production.view``.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.audit.service import AuditContext
from app.auth.principal import Principal
from app.authz.deps import require_capability
from app.concurrency.etag import parse_if_match_optional
from app.db import UnitOfWork
from app.errors import IdempotencyViolation, MissingHeader
from app.logging import get_logger
from app.production.service import ProductionRunService
from app.services.idempotency import IdempotencyViolationConflict
from app.util import client_ip
from app.validation.headers import parse_idempotency_key
from app.validation.pagination import MetaEnvelope, make_pagination
from app.validation.production_schemas import ProductionCostLineRequest

logger = get_logger(__name__)

# Mounted under /production-runs so the full paths are
# /production-runs/{run_id}/cost-lines and /production-runs/{run_id}/cost-lines/{line_id}
router = APIRouter(prefix="/production-runs", tags=["production-cost-lines"])


def _client_ip(request: Request) -> str | None:
    return client_ip(request.scope.get("headers", []))


def _audit_ctx(principal: Principal, request: Request) -> AuditContext:
    return AuditContext(
        user_id=principal.user_id,
        ip_address=_client_ip(request),
        request_id=getattr(request.state, "request_id", None),
    )


def _etag_for(result: dict[str, Any]) -> str:
    etag = result.get("etag")
    if etag:
        return str(etag)
    fallback = result.get("updated_at") or result.get("version") or ""
    return f'"{fallback}"'


def _json_response(result: Any, status_code: int = status.HTTP_200_OK) -> JSONResponse:
    headers = {}
    if isinstance(result, dict):
        headers["ETag"] = _etag_for(result)
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(result),
        headers=headers,
    )


def _idempotent_response(
    result: Any, status_code: int, is_replay: bool
) -> JSONResponse:
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (ValueError, TypeError):
            pass
    headers = {}
    if isinstance(result, dict):
        etag = _etag_for(result)
        if etag:
            headers["ETag"] = etag
    if is_replay:
        headers["Idempotent-Replay"] = "true"
        status_code = status.HTTP_200_OK
    return JSONResponse(
        status_code=status_code,
        content=jsonable_encoder(result),
        headers=headers,
    )


@router.get(
    "/{run_id}/cost-lines",
    operation_id="listProductionCostLines",
    summary="List production cost lines for a run.",
    status_code=status.HTTP_200_OK,
)
async def list_production_cost_lines(
    request: Request,
    run_id: int,
    principal: Annotated[Principal, Depends(require_capability("production.view"))],
    page: int = 1,
    per_page: int = 50,
) -> JSONResponse:
    async with UnitOfWork() as uow:
        svc = ProductionRunService(uow)
        result = await svc.list_cost_lines(run_id)
        await uow.commit()
        pag = make_pagination(
            page=page, per_page=per_page, total=len(result)
        )
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content=jsonable_encoder(
                MetaEnvelope(data=result, pagination=pag)
            ),
        )


@router.post(
    "/{run_id}/cost-lines",
    operation_id="addProductionCostLine",
    summary="Add a production cost line to a Draft run. Requires Idempotency-Key + If-Match.",
    status_code=status.HTTP_201_CREATED,
)
async def add_production_cost_line(
    request: Request,
    run_id: int,
    principal: Annotated[Principal, Depends(require_capability("production.edit_own_draft"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    parsed_key = parse_idempotency_key(idempotency_key)
    etag_parsed = parse_if_match_optional(if_match)
    if etag_parsed is None:
        raise MissingHeader(
            "If-Match header is required for POST /production-runs/{id}/cost-lines."
        )
    body_dict = await request.json()
    payload = ProductionCostLineRequest.model_validate(body_dict)
    async with UnitOfWork() as uow:
        svc = ProductionRunService(uow)
        try:
            result, is_replay = await svc.add_cost_line(
                run_id=run_id,
                principal_user_id=principal.user_id,
                cost_line_data=payload.model_dump(mode="json"),
                idempotency_key=str(parsed_key.value),
                if_match=etag_parsed,
                ctx=_audit_ctx(principal, request),
                request_body=body_dict,
            )
        except IdempotencyViolationConflict as exc:
            raise IdempotencyViolation(
                "Idempotency-Key reused with a different request body.",
                details=exc.details,
            ) from exc
        await uow.commit()
        return _idempotent_response(result, status.HTTP_201_CREATED, is_replay)


@router.patch(
    "/{run_id}/cost-lines/{line_id}",
    operation_id="updateProductionCostLine",
    summary="Update a production cost line on a Draft run. Requires If-Match.",
    status_code=status.HTTP_200_OK,
)
async def update_production_cost_line(
    request: Request,
    run_id: int,
    line_id: int,
    principal: Annotated[Principal, Depends(require_capability("production.edit_own_draft"))],
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    etag_parsed = parse_if_match_optional(if_match)
    if etag_parsed is None:
        raise MissingHeader(
            "If-Match header is required for PATCH /production-runs/{id}/cost-lines/{line_id}."
        )
    body_dict = await request.json()
    payload = ProductionCostLineRequest.model_validate(body_dict)
    async with UnitOfWork() as uow:
        svc = ProductionRunService(uow)
        result = await svc.update_cost_line(
            run_id=run_id,
            line_id=line_id,
            principal_user_id=principal.user_id,
            cost_line_data=payload.model_dump(mode="json"),
            if_match=etag_parsed,
            ctx=_audit_ctx(principal, request),
        )
        await uow.commit()
        return _json_response(result)


@router.delete(
    "/{run_id}/cost-lines/{line_id}",
    operation_id="deleteProductionCostLine",
    summary="Delete a production cost line from a Draft run. Requires Idempotency-Key.",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_production_cost_line(
    request: Request,
    run_id: int,
    line_id: int,
    principal: Annotated[Principal, Depends(require_capability("production.edit_own_draft"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> Response:
    parsed_key = parse_idempotency_key(idempotency_key)
    async with UnitOfWork() as uow:
        svc = ProductionRunService(uow)
        _result, is_replay = await svc.delete_cost_line(
            run_id=run_id,
            line_id=line_id,
            principal_user_id=principal.user_id,
            idempotency_key=str(parsed_key.value),
            if_match=parse_if_match_optional(if_match),
            ctx=_audit_ctx(principal, request),
        )
        await uow.commit()
        if is_replay:
            return Response(
                status_code=status.HTTP_200_OK,
                content=b"",
                headers={"Idempotent-Replay": "true"},
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT, content=b"")


__all__ = ["router"]
