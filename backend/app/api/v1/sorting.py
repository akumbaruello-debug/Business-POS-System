"""Pending receipt and mixed-inventory sorting endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, Request, status

from app.api.deps import get_uow, require_capability
from app.audit.service import AuditContext
from app.auth.principal import Principal
from app.db import UnitOfWork
from app.errors import NotFound
from app.inventory.sorting_service import SortingService
from app.validation.headers import parse_idempotency_key
from app.validation.sorting_schemas import (
    SortingEvent,
    SortingEventInput,
    SortingEventReverseRequest,
)

router = APIRouter(tags=["inventory sorting"])
View = require_capability("inventory.view")
Sort = require_capability("inventory.sort")


def _ctx(request: Request, principal: Principal) -> AuditContext:
    return AuditContext(user_id=principal.user_id, request_id=request.headers.get("x-request-id"),
                        ip_address=request.client.host if request.client else None)


@router.get("/pending-receipts")
async def list_pending(principal: Principal = Depends(View), uow: UnitOfWork = Depends(get_uow)) -> dict[str, Any]:
    return {"data": await SortingService(uow).list_pending()}


@router.get("/sorting-products")
async def list_sorting_products(principal: Principal = Depends(View), uow: UnitOfWork = Depends(get_uow)) -> dict[str, Any]:
    return {"data": await uow.fetch_all(
        """SELECT p.id,p.name,p.code,p.unit_id,u.code AS unit_code,u.factor_to_base
           FROM products p JOIN units u ON u.id=p.unit_id
           WHERE p.is_active AND p.is_sellable AND u.dimension='mass' AND u.factor_to_base IS NOT NULL
           ORDER BY p.name,p.id"""
    )}


@router.get("/sorting-expense-categories")
async def list_sorting_categories(principal: Principal = Depends(View), uow: UnitOfWork = Depends(get_uow)) -> dict[str, Any]:
    return {"data": await uow.fetch_all(
        "SELECT id,name FROM financial_categories WHERE entry_type='expense' AND is_active ORDER BY name,id"
    )}


@router.get("/sorting-events")
async def list_events(principal: Principal = Depends(View), uow: UnitOfWork = Depends(get_uow),
                      pending_receipt_batch_id: int | None = None) -> dict[str, Any]:
    return {"data": await SortingService(uow).list_events(pending_receipt_batch_id)}


@router.post("/sorting-events", status_code=status.HTTP_201_CREATED)
async def create_event(payload: SortingEventInput, request: Request,
                       principal: Principal = Depends(Sort), uow: UnitOfWork = Depends(get_uow)) -> SortingEvent:
    row = await SortingService(uow).create_draft(payload.model_dump(), user_id=principal.user_id,
                                                   ctx=_ctx(request, principal))
    await uow.commit()
    return SortingEvent.model_validate(row)


@router.get("/sorting-events/{event_id}")
async def get_event(event_id: int, principal: Principal = Depends(View),
                    uow: UnitOfWork = Depends(get_uow)) -> SortingEvent:
    row = await SortingService(uow).get_event(event_id)
    if row is None:
        raise NotFound(f"Sorting event {event_id} not found.")
    return SortingEvent.model_validate(row)


@router.patch("/sorting-events/{event_id}")
async def update_event(event_id: int, payload: SortingEventInput, request: Request,
                       principal: Principal = Depends(Sort), uow: UnitOfWork = Depends(get_uow),
                       if_match: str | None = Header(None, alias="If-Match")) -> SortingEvent:
    row = await SortingService(uow).update_draft(event_id, payload.model_dump(), if_match=if_match,
                                                  ctx=_ctx(request, principal))
    await uow.commit()
    return SortingEvent.model_validate(row)


@router.post("/sorting-events/{event_id}/preview")
async def preview_event(event_id: int, principal: Principal = Depends(View),
                        uow: UnitOfWork = Depends(get_uow)) -> dict[str, Any]:
    return await SortingService(uow).preview(event_id)


@router.post("/sorting-events/{event_id}/post")
async def post_event(event_id: int, request: Request, principal: Principal = Depends(Sort),
                     uow: UnitOfWork = Depends(get_uow),
                     if_match: str | None = Header(None, alias="If-Match"),
                     idempotency_key: str | None = Header(None, alias="Idempotency-Key")) -> SortingEvent:
    key = str(parse_idempotency_key(idempotency_key).value)
    result, replayed = await SortingService(uow).post(
        event_id, user_id=principal.user_id, if_match=if_match, idempotency_key=key,
        ctx=_ctx(request, principal), request_body=None)
    await uow.commit()
    response = SortingEvent.model_validate(result)
    return response


@router.post("/sorting-events/{event_id}/reverse")
async def reverse_event(event_id: int, payload: SortingEventReverseRequest, request: Request,
                        principal: Principal = Depends(Sort), uow: UnitOfWork = Depends(get_uow),
                        if_match: str | None = Header(None, alias="If-Match")) -> SortingEvent:
    row = await SortingService(uow).reverse(event_id, user_id=principal.user_id, reason=payload.reason,
                                             if_match=if_match, ctx=_ctx(request, principal))
    await uow.commit()
    return SortingEvent.model_validate(row)
