"""Owner-managed purchase cost categories and draft purchase cost lines."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Response, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.api.deps import current_principal, get_uow, require_capability
from app.audit.service import AuditContext, write_audit
from app.auth.principal import Principal
from app.concurrency.etag import check_if_match
from app.db import UnitOfWork
from app.errors import Conflict, IdempotencyViolation, MissingHeader, NotFound, ValidationFailed
from app.services.idempotency import (
    IdempotencyStore,
    IdempotencyViolationConflict,
    compute_request_fingerprint,
)
from app.services.purchases import PurchaseService
from app.validation.headers import parse_idempotency_key

router = APIRouter(tags=["Purchase Costs"])


class CategoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    capitalizable: bool
    financial_category_id: int | None = Field(default=None, ge=1)
    is_active: bool = True

    @model_validator(mode="after")
    def mapping_matches_policy(self):
        if self.capitalizable and self.financial_category_id is not None:
            raise ValueError("Capitalizable categories cannot map to an operating expense category.")
        if not self.capitalizable and self.financial_category_id is None:
            raise ValueError("Operating expense categories require an existing expense category mapping.")
        return self


class ManualAllocation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purchase_line_id: int = Field(ge=1)
    amount: Decimal = Field(ge=0, decimal_places=2)


class PurchaseCostInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category_id: int = Field(ge=1)
    amount: Decimal = Field(ge=0, decimal_places=2)
    allocation_method: Literal["line_value", "weight", "manual"] | None = None
    description: str | None = Field(default=None, max_length=500)
    allocations: list[ManualAllocation] = Field(default_factory=list)


@router.get("/purchase-cost-categories", dependencies=[Depends(require_capability("purchase.view"))])
async def list_categories(uow: UnitOfWork = Depends(get_uow)) -> dict[str, Any]:
    rows = await uow.fetch_all(
        "SELECT id,name,capitalizable,financial_category_id,is_active,created_at,updated_at "
        "FROM purchase_cost_categories ORDER BY name,id"
    )
    default_method = await uow.first_scalar(
        "SELECT value FROM system_settings WHERE key='default_purchase_cost_allocation'"
    ) or "line_value"
    return {"data": rows, "default_allocation": str(default_method)}


@router.post("/purchase-cost-categories", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_capability("settings.manage"))])
async def create_category(
    payload: CategoryInput,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
) -> dict[str, Any]:
    if payload.financial_category_id is not None:
        category = await uow.first_row(
            "SELECT id FROM financial_categories WHERE id=:id AND entry_type='expense' AND is_active",
            {"id": payload.financial_category_id},
        )
        if category is None:
            raise ValidationFailed("Select an active financial category with entry_type=expense.")
    row = await uow.first_row(
        """INSERT INTO purchase_cost_categories
           (name,capitalizable,financial_category_id,is_active,created_by,updated_by)
           VALUES (:name,:capitalizable,:financial_category_id,:is_active,:uid,:uid)
           RETURNING id,name,capitalizable,financial_category_id,is_active,created_at,updated_at""",
        {**payload.model_dump(), "uid": principal.user_id},
    )
    await write_audit(uow, action="create", entity_type="purchase_cost_category", entity_id=int(row["id"]),
                      new_values=dict(row), ctx=AuditContext(user_id=principal.user_id))
    await uow.commit()
    return row or {}


@router.patch("/purchase-cost-categories/{category_id}", dependencies=[Depends(require_capability("settings.manage"))])
async def update_category(
    category_id: int,
    payload: CategoryInput,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
) -> dict[str, Any]:
    old = await uow.first_row("SELECT * FROM purchase_cost_categories WHERE id=:id", {"id": category_id})
    if old is None:
        raise NotFound("Purchase cost category not found.")
    if payload.financial_category_id is not None:
        category = await uow.first_row(
            "SELECT id FROM financial_categories WHERE id=:id AND entry_type='expense' AND is_active",
            {"id": payload.financial_category_id},
        )
        if category is None:
            raise ValidationFailed("Select an active financial category with entry_type=expense.")
    row = await uow.first_row(
        """UPDATE purchase_cost_categories SET name=:name,capitalizable=:capitalizable,
           financial_category_id=:financial_category_id,is_active=:is_active,updated_by=:uid
           WHERE id=:id RETURNING id,name,capitalizable,financial_category_id,is_active,created_at,updated_at""",
        {**payload.model_dump(), "uid": principal.user_id, "id": category_id},
    )
    await write_audit(uow, action="update", entity_type="purchase_cost_category", entity_id=category_id,
                      old_values=dict(old), new_values=dict(row or {}), ctx=AuditContext(user_id=principal.user_id))
    await uow.commit()
    return row or {}


@router.get("/purchases/{purchase_id}/costs", dependencies=[Depends(require_capability("purchase.view"))])
async def list_purchase_costs(purchase_id: int, uow: UnitOfWork = Depends(get_uow)) -> dict[str, Any]:
    rows = await uow.fetch_all(
        """SELECT pc.id,pc.purchase_id,pc.category_id,cc.name AS category_name,
                  pc.capitalizable_snapshot AS capitalizable,
                  pc.financial_category_id_snapshot AS financial_category_id,
                  pc.amount,pc.allocation_method,
                  pc.description,pc.posted_at
           FROM purchase_costs pc JOIN purchase_cost_categories cc ON cc.id=pc.category_id
           WHERE pc.purchase_id=:pid ORDER BY pc.id""", {"pid": purchase_id}
    )
    return {"data": rows}


@router.post("/purchases/{purchase_id}/costs", status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_capability("purchase.edit_own_draft"))])
async def add_purchase_cost(
    purchase_id: int,
    payload: PurchaseCostInput,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    if_match: str | None = Header(default=None, alias="If-Match"),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any]:
    purchase = await uow.first_row("SELECT lifecycle_status,updated_at,version FROM purchases WHERE id=:id FOR UPDATE", {"id": purchase_id})
    if purchase is None:
        raise NotFound(f"Purchase {purchase_id} not found.")
    idem_key = str(parse_idempotency_key(idempotency_key_header).value)
    store = IdempotencyStore(uow)
    fingerprint = compute_request_fingerprint(method="POST", path=f"/purchases/{purchase_id}/costs", body=payload.model_dump(mode="json"))
    try:
        idem = await store.start(key=idem_key, user_id=principal.user_id, endpoint="POST /purchases/{id}/costs", fingerprint=fingerprint)
    except IdempotencyViolationConflict as exc:
        raise IdempotencyViolation("Idempotency-Key has been used with a different request body.") from exc
    if idem.is_completed:
        await uow.commit()
        return idem.response_body  # type: ignore[return-value]
    if if_match is None:
        raise MissingHeader("If-Match header is required.")
    check_if_match(provided=if_match, current_etag=PurchaseService._etag_from_row(purchase)[0])
    if purchase["lifecycle_status"] != "draft":
        raise Conflict("Purchase costs can only be changed while the purchase is a draft.")
    category = await uow.first_row(
        "SELECT id,name,is_active,capitalizable,financial_category_id FROM purchase_cost_categories WHERE id=:id",
        {"id": payload.category_id},
    )
    if category is None or not category["is_active"]:
        raise NotFound("Active purchase cost category not found.")
    mode = payload.allocation_method
    if mode is None:
        mode = await uow.first_scalar("SELECT value FROM system_settings WHERE key='default_purchase_cost_allocation'") or "line_value"
    row = await uow.first_row(
        """INSERT INTO purchase_costs(purchase_id,category_id,amount,capitalizable_snapshot,
                  financial_category_id_snapshot,allocation_method,description,created_by)
           SELECT :pid,c.id,:amount,c.capitalizable,c.financial_category_id,:method,:description,:uid
           FROM purchase_cost_categories c WHERE c.id=:cid RETURNING id""",
        {"pid": purchase_id,"cid": payload.category_id,"amount": payload.amount,"method": mode,
         "description": payload.description,"uid": principal.user_id},
    )
    cost_id = int(row["id"])
    if mode == "manual":
        submitted = {item.purchase_line_id: item.amount for item in payload.allocations}
        sums = sum(submitted.values(), Decimal("0"))
        if sums != payload.amount:
            raise ValidationFailed("Manual allocations must equal the purchase cost amount.")
        lines = await uow.fetch_all("SELECT id FROM purchase_lines WHERE purchase_id=:pid", {"pid": purchase_id})
        allowed = {int(line["id"]) for line in lines}
        if not submitted or set(submitted) - allowed:
            raise ValidationFailed("Manual allocations must reference purchase lines on this purchase.")
        for line_id, amount in submitted.items():
            await uow.execute("INSERT INTO purchase_cost_allocations(purchase_cost_id,purchase_line_id,amount) VALUES (:cid,:lid,:amount)",
                              {"cid": cost_id,"lid": line_id,"amount": amount})
    try:
        await PurchaseService(uow)._recompute_purchase_cost_allocations(purchase_id)
    except Exception:
        await uow.rollback()
        raise
    await uow.execute("UPDATE purchases SET updated_at=NOW() WHERE id=:pid", {"pid": purchase_id})
    await write_audit(
        uow, action="create", entity_type="purchase_cost", entity_id=cost_id,
        new_values={"purchase_id": purchase_id, "category_id": payload.category_id,
                    "amount": str(payload.amount), "allocation_method": mode,
                    "allocations": {str(k): str(v) for k, v in submitted.items()} if mode == "manual" else None},
        ctx=AuditContext(user_id=principal.user_id),
    )
    response_body = {"id": cost_id, "purchase_id": purchase_id, "category_id": payload.category_id,
            "category_name": category["name"], "capitalizable": bool(category["capitalizable"]),
            "financial_category_id": category["financial_category_id"], "amount": float(payload.amount),
            "allocation_method": mode, "description": payload.description, "posted_at": None}
    await store.complete(record_id=idem.id, status=201, body=response_body, user_id=principal.user_id)
    await uow.commit()
    return response_body


@router.delete("/purchases/{purchase_id}/costs/{cost_id}", status_code=status.HTTP_204_NO_CONTENT,
               dependencies=[Depends(require_capability("purchase.edit_own_draft"))])
async def delete_purchase_cost(
    purchase_id: int, cost_id: int,
    principal: Principal = Depends(current_principal),
    uow: UnitOfWork = Depends(get_uow),
    if_match: str | None = Header(default=None, alias="If-Match"),
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Response:
    purchase = await uow.first_row("SELECT lifecycle_status,updated_at,version FROM purchases WHERE id=:id FOR UPDATE", {"id": purchase_id})
    if purchase is None:
        raise NotFound(f"Purchase {purchase_id} not found.")
    idem_key = str(parse_idempotency_key(idempotency_key_header).value)
    store = IdempotencyStore(uow)
    fingerprint = compute_request_fingerprint(method="DELETE", path=f"/purchases/{purchase_id}/costs/{cost_id}", body=None)
    try:
        idem = await store.start(key=idem_key, user_id=principal.user_id, endpoint="DELETE /purchases/{id}/costs/{cost_id}", fingerprint=fingerprint)
    except IdempotencyViolationConflict as exc:
        raise IdempotencyViolation("Idempotency-Key has been used with a different request body.") from exc
    if idem.is_completed:
        await uow.commit()
        return Response(status_code=204, headers={"Idempotent-Replay": "true"})
    if if_match is None:
        raise MissingHeader("If-Match header is required.")
    check_if_match(provided=if_match, current_etag=PurchaseService._etag_from_row(purchase)[0])
    if purchase["lifecycle_status"] != "draft":
        raise Conflict("Posted purchase costs cannot be changed.")
    old = await uow.first_row("SELECT * FROM purchase_costs WHERE id=:cid AND purchase_id=:pid", {"cid": cost_id,"pid": purchase_id})
    if old is None:
        raise NotFound("Purchase cost not found.")
    await uow.execute("DELETE FROM purchase_costs WHERE id=:cid AND purchase_id=:pid", {"cid": cost_id,"pid": purchase_id})
    await PurchaseService(uow)._recompute_purchase_cost_allocations(purchase_id)
    await uow.execute("UPDATE purchases SET updated_at=NOW() WHERE id=:pid", {"pid": purchase_id})
    await write_audit(uow, action="delete", entity_type="purchase_cost", entity_id=cost_id,
                      old_values=dict(old), ctx=AuditContext(user_id=principal.user_id))
    await store.complete(record_id=idem.id, status=204, body="{}", user_id=principal.user_id)
    await uow.commit()
    return Response(status_code=204)
