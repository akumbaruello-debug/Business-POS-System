"""Pydantic validation infrastructure.

Centralizes the canonical OpenAPI enums so both request parsing and
response rendering validate against the same closed sets.

The OpenAPI spec uses ``VARCHAR`` + ``CHECK`` constraints in the DB and
validates against closed sets in the server (see Database-Design §2.1
and the OpenAPI ``enum_value_invalid`` pattern). The enums here are the
server-side mirror of the DB CHECK constraints — the single place that
defines the valid string set for each enum-ish column.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "AuditAction",
    "ContactType",
    "FinancialEntryType",
    "LifecycleStatus",
    "NotificationCategory",
    "NotificationSeverity",
    "SettingValueType",
]


class LifecycleStatus(StrEnum):
    """Closed lifecycle set per Database-Design §2.4.1 (sales/purchases).

    Draft → posted → completed / partially_returned / returned / cancelled.
    """

    DRAFT = "draft"
    POSTED = "posted"
    COMPLETED = "completed"
    PARTIALLY_RETURNED = "partially_returned"
    RETURNED = "returned"
    CANCELLED = "cancelled"


class ContactType(StrEnum):
    """``contacts.type`` — DB spec §2.5.3."""

    CUSTOMER = "customer"
    SUPPLIER = "supplier"
    BOTH = "both"


class NotificationCategory(StrEnum):
    """``notifications.category`` — DB spec §2.12 / Notification Architecture §21.

    PRD §27 lists 5 categories.
    """

    LOW_STOCK = "low_stock"
    INSUFFICIENT_STOCK = "insufficient_stock"
    BELOW_COST = "below_cost"
    SYSTEM = "system"
    ACTION_CONFIRMATION = "action_confirmation"


class NotificationSeverity(StrEnum):
    """``notifications.severity`` — DB spec §2.12."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class AuditAction(StrEnum):
    """``audit_log.action`` — DB spec §2.11.

    Per BR-AUDIT-001/002/003 + the canonical CHECK on the column.
    """

    CREATE = "create"
    POST = "post"
    CANCEL = "cancel"
    COMPLETE = "complete"
    RETURN = "return"
    ADJUST = "adjust"
    MOVEMENT = "movement"
    PRICE_OVERRIDE = "price_override"
    PAYMENT = "payment"
    REFUND = "refund"
    PERMISSION_GRANT = "permission_grant"
    PERMISSION_REVOKE = "permission_revoke"
    SETTINGS_CHANGE = "settings_change"
    DEACTIVATE = "deactivate"
    UPDATE = "update"


class FinancialEntryType(StrEnum):
    """``financial_categories.entry_type`` — DB spec §2.13.

    Note: ``manual_finance_entries`` reuses these as ``debit``/``credit``
    direction markers (income → debit-positive; see V1.9 Event 21).
    """

    INCOME = "income"
    EXPENSE = "expense"


class SettingValueType(StrEnum):
    """``system_settings.value_type`` — DB spec §2.9."""

    STRING = "string"
    NUMBER = "number"
    BOOLEAN = "boolean"
    JSON = "json"
