"""Audit-log write service.

Writes to the append-only ``audit_log`` table (see ``schema.sql`` §2.11).
Per ``Backend-Architecture-V1.0.md`` §18, audit entries carry:

- ``action``        — one of the values allowed by ``ck_audit_action``
                      (create, update, deactivate, delete, ...).
- ``entity_type``   — the resource table name (category, product, ...).
- ``entity_id``     — the affected row id (nullable only when not
                      applicable).
- ``old_values``    — JSONB snapshot of the row *before* the mutation, or
                      NULL on create.
- ``new_values``    — JSONB snapshot of the row *after* the mutation, or
                      NULL on delete.
- ``user_id``       — actor id (FK users, ON DELETE SET NULL).
- ``reason``        — optional human note (e.g. deactivate reason).
- ``request_id``    — correlation token from the request-id middleware.
- ``ip_address``    — optional, populated by the route layer.

Every write happens inside the caller's ``UnitOfWork`` transaction so
the audit row rolls back with the business mutation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db import UnitOfWork
from app.logging import get_logger
from app.validation.enums import AuditAction

logger = get_logger(__name__)

#: Entity-type labels used in audit_log.entity_type. We keep these as a
#: closed set aligned with the resource names in openapi.yaml.
ENTITY_CATEGORIES = "category"
ENTITY_UNITS = "unit"
ENTITY_PRODUCTS = "product"
ENTITY_PAYMENT_METHODS = "payment_method"
ENTITY_COST_TYPES = "cost_type"
ENTITY_FINANCIAL_CATEGORIES = "financial_category"
ENTITY_CONTACTS = "contact"
ENTITY_SALES = "sale"
ENTITY_SALE_LINES = "sale_line"
ENTITY_SALE_PAYMENTS = "sale_payment"
ENTITY_SALES_RETURNS = "sales_return"
ENTITY_PURCHASES = "purchase"
ENTITY_PURCHASE_LINES = "purchase_line"
ENTITY_PURCHASE_PAYMENTS = "purchase_payment"
ENTITY_PURCHASE_SHIPPING = "purchase_shipping"
ENTITY_PURCHASE_RETURNS = "purchase_return"
ENTITY_REFUNDS = "refund"
ENTITY_SUPPLIER_REPAYMENTS = "supplier_repayment"
# M5 / Phase E
ENTITY_PRODUCTION_RUNS = "production_run"
ENTITY_PRODUCTION_INPUTS = "production_input"
ENTITY_PRODUCTION_OUTPUTS = "production_output"
ENTITY_PRODUCTION_COST_LINES = "production_cost_line"


class AuditContext:
    """Bundle of audit metadata passed from the route layer.

    ``user_id``, ``request_id`` and ``ip_address`` are request-scoped and
    typically extracted by the route handler and forwarded here.
    """

    __slots__ = ("ip_address", "request_id", "user_id")

    def __init__(
        self,
        user_id: int | None = None,
        request_id: str | None = None,
        ip_address: str | None = None,
    ) -> None:
        self.user_id = user_id
        self.request_id = request_id
        self.ip_address = ip_address


def _to_jsonb(values: dict[str, Any] | None) -> str | None:
    """Render a row snapshot dict to the JSONB text format the DB stores."""
    if not values:
        return None
    import json

    return json.dumps(values, default=str, sort_keys=True)


async def write_audit(
    uow: UnitOfWork,
    *,
    action: str | AuditAction,
    entity_type: str,
    entity_id: int | None,
    old_values: dict[str, Any] | None = None,
    new_values: dict[str, Any] | None = None,
    reason: str | None = None,
    ctx: AuditContext | None = None,
    now: datetime | None = None,
) -> int:
    """Insert one audit_log row.

    Returns the new ``audit_log.id`` (useful for tracing). Always runs
    inside the supplied ``uow`` so it participates in the caller's
    transaction.

    ``action`` must be a value allowed by ``ck_audit_action``
    (validated by the DB CHECK; we pass strings through).
    """
    user_id = ctx.user_id if ctx is not None else None
    request_id = ctx.request_id if ctx is not None else None
    ip_address = ctx.ip_address if ctx is not None else None
    if now is None:
        now = datetime.now()

    result = await uow.execute(
        """
        INSERT INTO audit_log (
            event_time,
            user_id,
            action,
            entity_type,
            entity_id,
            old_values,
            new_values,
            reason,
            ip_address,
            request_id
        ) VALUES (
            :event_time,
            :user_id,
            :action,
            :entity_type,
            :entity_id,
            :old_values,
            :new_values,
            :reason,
            :ip_address,
            :request_id
        )
        RETURNING id
        """,
        {
            "event_time": now,
            "user_id": user_id,
            "action": str(action),
            "entity_type": entity_type,
            "entity_id": entity_id,
            "old_values": _to_jsonb(old_values),
            "new_values": _to_jsonb(new_values),
            "reason": reason,
            "ip_address": ip_address,
            "request_id": request_id,
        },
    )
    try:
        return int(result.scalar())
    except (TypeError, ValueError):
        return 0


__all__ = [
    "ENTITY_CATEGORIES",
    "ENTITY_CONTACTS",
    "ENTITY_COST_TYPES",
    "ENTITY_FINANCIAL_CATEGORIES",
    "ENTITY_PAYMENT_METHODS",
    "ENTITY_PRODUCTION_COST_LINES",
    "ENTITY_PRODUCTION_INPUTS",
    "ENTITY_PRODUCTION_OUTPUTS",
    "ENTITY_PRODUCTION_RUNS",
    "ENTITY_PRODUCTS",
    "ENTITY_PURCHASES",
    "ENTITY_PURCHASE_LINES",
    "ENTITY_PURCHASE_PAYMENTS",
    "ENTITY_PURCHASE_RETURNS",
    "ENTITY_PURCHASE_SHIPPING",
    "ENTITY_REFUNDS",
    "ENTITY_SALES",
    "ENTITY_SALES_RETURNS",
    "ENTITY_SALE_LINES",
    "ENTITY_SALE_PAYMENTS",
    "ENTITY_SUPPLIER_REPAYMENTS",
    "ENTITY_UNITS",
    "AuditAction",
    "AuditContext",
    "write_audit",
]
