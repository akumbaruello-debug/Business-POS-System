"""Audit-log read route — GET /audit (listAudit).

Matches ``openapi.yaml`` ``/audit`` GET operation exactly (operationId
``listAudit``, capability ``audit.view``).

The route is thin: authenticate, enforce capability, parse the standard
list/query parameters, delegate to ``app.audit.query.query_audit_log``,
and return the contract envelope ``{ data, pagination }``.

Query parameters (mirrors the OpenAPI ``parameters`` block):

* ``Page`` — 1-based; default 1; min 1.
* ``PerPage`` — page size; default 50; min 1, max 500.
* ``Sort`` — comma-separated, prefix ``-`` for desc. Accepted:
  ``event_time``, ``-event_time``, ``id``, ``-id``, ``action``,
  ``-action``, ``entity_type``, ``-entity_type``. Unknown keys fall
  back to ``-event_time`` (the query default).
* ``Q`` — accepted for contract parity; the audit_log has no free-text
  search column, so ``q`` is ignored on the SQL side (per the
  contract-authority-audit pattern: we honour the authoritative OpenAPI
  parameter list, not the Q semantics).
* ``From`` / ``To`` — ISO 8601; filter ``event_time`` inclusive.
* ``filter[user_id]``  — integer; optional.
* ``filter[action]``   — string; optional.
* ``filter[entity_type]`` — string; optional.
* ``filter[entity_id]`` — integer; optional.

The ``query_audit_log`` helper already supports ``entity_type``,
``entity_id``, ``action``, ``user_id``, ``page``, ``per_page``. The
route performs the ``sort`` whitelist + ``from``/``to`` / ``q`` mapping
here so the helper stays DB-focused.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status

from app.api.deps import get_uow, require_capability
from app.audit.query import query_audit_log
from app.audit.service import (
    ENTITY_CATEGORIES,
    ENTITY_CONTACTS,
    ENTITY_COST_TYPES,
    ENTITY_FINANCIAL_CATEGORIES,
    ENTITY_PAYMENT_METHODS,
    ENTITY_PRODUCTION_COST_LINES,
    ENTITY_PRODUCTION_INPUTS,
    ENTITY_PRODUCTION_OUTPUTS,
    ENTITY_PRODUCTION_RUNS,
    ENTITY_PRODUCTS,
    ENTITY_PURCHASE_LINES,
    ENTITY_PURCHASE_PAYMENTS,
    ENTITY_PURCHASE_RETURNS,
    ENTITY_PURCHASE_SHIPPING,
    ENTITY_PURCHASES,
    ENTITY_REFUNDS,
    ENTITY_SALE_LINES,
    ENTITY_SALE_PAYMENTS,
    ENTITY_SALES,
    ENTITY_SALES_RETURNS,
    ENTITY_SUPPLIER_REPAYMENTS,
    ENTITY_UNITS,
)
from app.db import UnitOfWork
from app.errors import ValidationFailed
from app.validation.pagination import MetaEnvelope, make_pagination

RequireAuditView = require_capability("audit.view")

#: Closed ``entity_type`` vocabulary (``app.audit.service`` ENTITY_*).
ALLOWED_ENTITY_TYPES: frozenset[str] = frozenset(
    {
        ENTITY_CATEGORIES,
        ENTITY_UNITS,
        ENTITY_PRODUCTS,
        ENTITY_PAYMENT_METHODS,
        ENTITY_COST_TYPES,
        ENTITY_FINANCIAL_CATEGORIES,
        ENTITY_CONTACTS,
        ENTITY_SALES,
        ENTITY_SALE_LINES,
        ENTITY_SALE_PAYMENTS,
        ENTITY_SALES_RETURNS,
        ENTITY_PURCHASES,
        ENTITY_PURCHASE_LINES,
        ENTITY_PURCHASE_PAYMENTS,
        ENTITY_PURCHASE_SHIPPING,
        ENTITY_PURCHASE_RETURNS,
        ENTITY_REFUNDS,
        ENTITY_SUPPLIER_REPAYMENTS,
        ENTITY_PRODUCTION_RUNS,
        ENTITY_PRODUCTION_INPUTS,
        ENTITY_PRODUCTION_OUTPUTS,
        ENTITY_PRODUCTION_COST_LINES,
    }
)

router = APIRouter(tags=["Audit"])

#: Whitelisted sort keys (column name → SQL expression).
_SORT_MAP: dict[str, str] = {
    "event_time": "event_time ASC, id ASC",
    "-event_time": "event_time DESC, id DESC",
    "id": "id ASC",
    "-id": "id DESC",
    "action": "action ASC, id ASC",
    "-action": "action DESC, id DESC",
    "entity_type": "entity_type ASC, id ASC",
    "-entity_type": "entity_type DESC, id DESC",
}
_DEFAULT_SORT_SQL = "event_time DESC, id DESC"


def _resolve_sort(sort: str | None) -> str:
    """Map the OpenAPI ``Sort`` param to a SQL ORDER BY fragment.

    Falls back to ``event_time DESC, id DESC`` (the audit log's
    ``query_audit_log`` default) for unknown keys.
    """
    if not sort:
        return _DEFAULT_SORT_SQL
    # OpenAPI Sort is comma-separated; we honour the first key only
    # (audit_log has no multi-column sort contract).
    key = sort.split(",")[0].strip()
    return _SORT_MAP.get(key, _DEFAULT_SORT_SQL)


def _row_to_response(row: dict[str, Any]) -> dict[str, Any]:
    """Coerce a raw ``audit_log`` row dict into the OpenAPI ``AuditEntry`` shape.

    ``query_audit_log`` returns columns: id, event_time, user_id, action,
    entity_type, entity_id, old_values, new_values, reason, ip_address.
    JSONB columns come back as ``str`` (asyncpg) or ``dict`` depending on
    the driver; we normalise to ``dict | None`` and datetime to ISO string.
    """

    def _iso(v: Any) -> str | None:
        if v is None:
            return None
        if hasattr(v, "isoformat"):
            return str(v.isoformat())
        return str(v)

    def _jsonb(v: Any) -> dict[str, Any] | None:
        if v is None:
            return None
        if isinstance(v, dict):
            return v
        # asyncpg may return JSONB as a string for some query paths.
        if isinstance(v, str):
            import json

            try:
                parsed = json.loads(v)
                return parsed if isinstance(parsed, dict) else None
            except (ValueError, TypeError):
                return None
        return None

    def _int(v: Any) -> int | None:
        return int(v) if v is not None else None

    return {
        "id": int(row["id"]),
        "event_time": _iso(row.get("event_time")) or "",
        "user_id": _int(row.get("user_id")),
        "action": str(row["action"]),
        "entity_type": str(row["entity_type"]),
        "entity_id": _int(row.get("entity_id")),
        "old_values": _jsonb(row.get("old_values")),
        "new_values": _jsonb(row.get("new_values")),
        "reason": str(row["reason"]) if row.get("reason") is not None else None,
        "ip_address": (
            str(row["ip_address"]) if row.get("ip_address") is not None else None
        ),
    }


# ---------------------------------------------------------------------------
# GET /audit  —  listAudit
# ---------------------------------------------------------------------------


@router.get(
    "/audit",
    operation_id="listAudit",
    summary="List audit entries. Read-only.",
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(RequireAuditView)],
)
async def list_audit(
    request: Request,
    uow: UnitOfWork = Depends(get_uow),
    page: int = Query(default=1, ge=1, le=1000),
    per_page: int = Query(default=50, ge=1, le=500),
    sort: str = Query(default=""),
    q: str | None = Query(default=None, max_length=200),
    from_iso: str | None = Query(default=None, alias="from"),
    to_iso: str | None = Query(default=None, alias="to"),
    filter_user_id: int | None = Query(default=None, alias="filter[user_id]"),
    filter_action: str | None = Query(default=None, alias="filter[action]"),
    filter_entity_type: str | None = Query(
        default=None, alias="filter[entity_type]"
    ),
    filter_entity_id: int | None = Query(
        default=None, alias="filter[entity_id]"
    ),
) -> MetaEnvelope[dict[str, Any]]:
    """``listAudit`` — GET /audit.

    Returns the contract envelope ``{ data, pagination, links }`` over the
    append-only ``audit_log`` ledger. Filters are optional and
    combinable. ``sort`` is whitelisted; ``q`` is accepted for contract
    parity but has no search column on the table. ``from``/``to`` filter
    ``event_time`` (inclusive, ISO 8601).
    """
    # ``q`` is accepted for OpenAPI parity (the contract lists the Q
    # parameter) but the audit_log table has no free-text search column,
    # so we intentionally ignore it — same pattern as stock_movements.
    if filter_entity_type is not None and filter_entity_type not in ALLOWED_ENTITY_TYPES:
        raise ValidationFailed(
            f"Unknown entity_type '{filter_entity_type}'.",
            errors=[
                {
                    "field": "filter[entity_type]",
                    "code": "invalid",
                    "message": f"Unknown entity_type '{filter_entity_type}'.",
                }
            ],
        )
    sort_sql = _resolve_sort(sort)

    rows, total = await query_audit_log(
        uow,
        entity_type=filter_entity_type,
        entity_id=filter_entity_id,
        action=filter_action,
        user_id=filter_user_id,
        from_iso=from_iso,
        to_iso=to_iso,
        sort_sql=sort_sql,
        page=page,
        per_page=per_page,
    )
    data = [_row_to_response(r) for r in rows]
    pag = make_pagination(page=page, per_page=per_page, total=total)
    return MetaEnvelope[dict[str, Any]](
        data=data,
        pagination=pag,
    )


__all__ = ["ALLOWED_ENTITY_TYPES", "router"]
