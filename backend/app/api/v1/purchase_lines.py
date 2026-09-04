"""Purchase Lines API routes (M4 / Phase D.3).

Endpoints match ``openapi.yaml`` §13 exactly:
* ``listPurchaseLines``  GET    /purchases/{id}/lines
* ``addPurchaseLine``    POST   /purchases/{id}/lines
* ``updatePurchaseLine`` PATCH  /purchases/{id}/lines/{line_id}
* ``deletePurchaseLine`` DELETE /purchases/{id}/lines/{line_id}
"""

from __future__ import annotations

import json
from decimal import Decimal
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
from app.services.idempotency import IdempotencyViolationConflict
from app.services.purchases import PurchaseService
from app.util import client_ip
from app.validation.headers import parse_idempotency_key
from app.validation.purchases_schemas import PurchaseLineInput, PurchaseLinePatch

logger = get_logger(__name__)

# Mounted under the same /purchases prefix as the parent router.
router = APIRouter(prefix="/purchases", tags=["purchase_lines"])


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


def _dec(v: float | int | str | None) -> Decimal | None:
    if v is None:
        return None
    return Decimal(str(v))


@router.get(
    "/{purchase_id}/lines",
    operation_id="listPurchaseLines",
    summary="List purchase lines.",
    status_code=status.HTTP_200_OK,
)
async def list_purchase_lines(
    request: Request,
    purchase_id: int,
    principal: Annotated[Principal, Depends(require_capability("purchase.view"))],
) -> JSONResponse:
    async with UnitOfWork() as uow:
        svc = PurchaseService(uow)
        result = await svc.list_lines(purchase_id)
        await uow.commit()
        return _json_response(result)


@router.post(
    "/{purchase_id}/lines",
    operation_id="addPurchaseLine",
    summary="Add a Draft purchase line. Mutates draft totals; requires If-Match.",
    status_code=status.HTTP_201_CREATED,
)
async def add_purchase_line(
    request: Request,
    purchase_id: int,
    principal: Annotated[Principal, Depends(require_capability("purchase.edit_own_draft"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    parsed_key = parse_idempotency_key(idempotency_key)
    body_dict = await request.json()
    payload = PurchaseLineInput.model_validate(body_dict)
    async with UnitOfWork() as uow:
        svc = PurchaseService(uow)
        try:
            result, is_replay = await svc.add_line(
                purchase_id=purchase_id,
                principal_user_id=principal.user_id,
                line=payload.model_dump(mode="json"),
                idempotency_key=str(parsed_key.value),
                if_match=parse_if_match_optional(if_match),
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
    "/{purchase_id}/lines/{line_id}",
    operation_id="updatePurchaseLine",
    summary="Update a Draft purchase line.",
    status_code=status.HTTP_200_OK,
)
async def update_purchase_line(
    request: Request,
    purchase_id: int,
    line_id: int,
    principal: Annotated[Principal, Depends(require_capability("purchase.edit_own_draft"))],
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> JSONResponse:
    etag_parsed = parse_if_match_optional(if_match)
    if etag_parsed is None:
        raise MissingHeader("If-Match header is required.")
    body_dict = await request.json()
    payload = PurchaseLinePatch.model_validate(body_dict)
    async with UnitOfWork() as uow:
        svc = PurchaseService(uow)
        result = await svc.update_line(
            purchase_id=purchase_id,
            line_id=line_id,
            principal_user_id=principal.user_id,
            quantity=_dec(payload.quantity),
            unit_price=_dec(payload.unit_price),
            if_match=etag_parsed,
            ctx=_audit_ctx(principal, request),
        )
        await uow.commit()
        return _json_response(result)


@router.delete(
    "/{purchase_id}/lines/{line_id}",
    operation_id="deletePurchaseLine",
    summary="Delete a Draft purchase line.",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_purchase_line(
    request: Request,
    purchase_id: int,
    line_id: int,
    principal: Annotated[Principal, Depends(require_capability("purchase.edit_own_draft"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> Response:
    parsed_key = parse_idempotency_key(idempotency_key)
    async with UnitOfWork() as uow:
        svc = PurchaseService(uow)
        _result, is_replay = await svc.delete_line(
            purchase_id=purchase_id,
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
