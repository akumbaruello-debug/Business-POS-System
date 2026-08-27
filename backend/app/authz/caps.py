"""Canonical capability catalog — frozen from the migration seed.

This is the authoritative registry of every capability code known to
the system. It is the server-side mirror of the ``capabilities``
table seeded by ``db/migrations/m0001__initial_schema_baseline.sql``
(§19.2, lines 2243--2321).

**Do not mutate this set at runtime.** Adding a capability is a
contract change that must be reflected in the migration seed + the
OpenAPI spec first. This module exists so that:

1. ``require_capability("sale.create")`` is checked against a known
   universe (catches typos at startup, not at request time).
2. Tests can assert the full catalog without hitting the DB.
3. The ``/auth/me`` response lists capabilities from a single source.
"""

from __future__ import annotations

from collections import OrderedDict
from enum import StrEnum
from typing import Final


class CapabilityCategory(StrEnum):
    """Top-level grouping for readability (mirrors API §3.1)."""

    AUTH = "auth"
    IDENTITY = "identity"
    CONFIG = "config"
    MASTER = "master"
    FINANCE = "finance"
    SALES = "sales"
    PURCHASE = "purchase"
    INVENTORY = "inventory"
    PRODUCTION = "production"
    MANUAL_ENTRY = "manual_entry"
    FINANCIAL_VIEWS = "financial_views"
    REPORTS = "reports"
    NOTIFICATIONS = "notifications"


# An OrderedDict of code → (category, description).
# The ORDER matches the migration seed ordering exactly so the
# integer IDs implied by the seed (INSERT order) align.
_CANONICAL: Final[OrderedDict[str, tuple[CapabilityCategory, str]]] = OrderedDict(
    {
        # Auth
        "auth.login": (CapabilityCategory.AUTH, "Log in to the system."),
        "auth.password_reset_others": (CapabilityCategory.AUTH, "Reset other users' passwords."),
        # Identity
        "user.view": (CapabilityCategory.IDENTITY, "View users."),
        "user.manage": (CapabilityCategory.IDENTITY, "Create, update, deactivate users."),
        "user.grant_capability": (CapabilityCategory.IDENTITY, "Grant a capability to a user."),
        "user.revoke_capability": (CapabilityCategory.IDENTITY, "Revoke a capability from a user."),
        "role.view": (CapabilityCategory.IDENTITY, "View roles."),
        "role.manage": (CapabilityCategory.IDENTITY, "Create, update roles."),
        "audit.view": (CapabilityCategory.IDENTITY, "View the audit log."),
        # Config
        "settings.view": (CapabilityCategory.CONFIG, "View system settings."),
        "settings.manage": (CapabilityCategory.CONFIG, "Update system settings."),
        # Master
        "category.view": (CapabilityCategory.MASTER, "View product categories."),
        "category.manage": (CapabilityCategory.MASTER, "Manage product categories."),
        "unit.view": (CapabilityCategory.MASTER, "View units of measure."),
        "unit.manage": (CapabilityCategory.MASTER, "Manage units of measure."),
        "product.view": (CapabilityCategory.MASTER, "View products."),
        "product.create": (CapabilityCategory.MASTER, "Create products."),
        "product.edit": (CapabilityCategory.MASTER, "Edit products."),
        "product.deactivate": (CapabilityCategory.MASTER, "Deactivate products."),
        "contact.view": (CapabilityCategory.MASTER, "View contacts."),
        "contact.create": (CapabilityCategory.MASTER, "Create contacts."),
        "contact.edit": (CapabilityCategory.MASTER, "Edit contacts."),
        "payment_method.view": (CapabilityCategory.MASTER, "View payment methods."),
        "payment_method.manage": (CapabilityCategory.MASTER, "Manage payment methods."),
        "cost_type.view": (CapabilityCategory.MASTER, "View cost types."),
        "cost_type.manage": (CapabilityCategory.MASTER, "Manage cost types."),
        # Finance
        "financial_category.view": (CapabilityCategory.FINANCE, "View financial categories."),
        "financial_category.manage": (CapabilityCategory.FINANCE, "Manage financial categories."),
        # Sales
        "sale.view": (CapabilityCategory.SALES, "View sales."),
        "sale.create": (CapabilityCategory.SALES, "Create a sale (POS)."),
        "sale.edit_own_draft": (
            CapabilityCategory.SALES,
            "Edit a sale that the user created in draft status.",
        ),
        "sale.post": (CapabilityCategory.SALES, "Post (commit) a sale."),
        "sale.complete": (CapabilityCategory.SALES, "Complete a sale (auto on full payment)."),
        "sale.cancel": (CapabilityCategory.SALES, "Cancel a posted sale."),
        "sale.return": (CapabilityCategory.SALES, "Process a sales return."),
        "sale.price_override": (
            CapabilityCategory.SALES,
            "Override the selling price on a sale line.",
        ),
        "sale.discount": (CapabilityCategory.SALES, "Apply a discount on a sale."),
        "sale.refund": (CapabilityCategory.SALES, "Issue a customer refund."),
        # Purchase
        "purchase.view": (CapabilityCategory.PURCHASE, "View purchases."),
        "purchase.create": (CapabilityCategory.PURCHASE, "Create a purchase."),
        "purchase.edit_own_draft": (
            CapabilityCategory.PURCHASE,
            "Edit a purchase that the user created in draft status.",
        ),
        "purchase.post": (CapabilityCategory.PURCHASE, "Post (receive) a purchase."),
        "purchase.complete": (
            CapabilityCategory.PURCHASE,
            "Complete a purchase (auto on full payment).",
        ),
        "purchase.cancel": (CapabilityCategory.PURCHASE, "Cancel a posted purchase."),
        "purchase.return": (CapabilityCategory.PURCHASE, "Process a purchase return."),
        "purchase.refund": (CapabilityCategory.PURCHASE, "Disburse a supplier refund / repayment."),
        # Inventory
        "inventory.view": (CapabilityCategory.INVENTORY, "View inventory and stock movements."),
        "inventory.adjust": (
            CapabilityCategory.INVENTORY,
            "Manually adjust stock (positive or negative).",
        ),
        "inventory.transfer": (
            CapabilityCategory.INVENTORY,
            "Transfer stock between locations (V2).",
        ),
        # Production
        "production.view": (CapabilityCategory.PRODUCTION, "View production runs."),
        "production.create": (CapabilityCategory.PRODUCTION, "Create a production run (draft)."),
        "production.edit_own_draft": (
            CapabilityCategory.PRODUCTION,
            "Edit a production run that the user created in draft.",
        ),
        "production.post": (CapabilityCategory.PRODUCTION, "Post a production run."),
        "production.cancel": (CapabilityCategory.PRODUCTION, "Cancel a posted production run."),
        # Manual finance
        "manual_entry.view": (
            CapabilityCategory.MANUAL_ENTRY,
            "View manual income/expense entries.",
        ),
        "manual_entry.create_income": (
            CapabilityCategory.MANUAL_ENTRY,
            "Create a manual income entry.",
        ),
        "manual_entry.create_expense": (
            CapabilityCategory.MANUAL_ENTRY,
            "Create a manual expense entry.",
        ),
        "manual_entry.cancel": (CapabilityCategory.MANUAL_ENTRY, "Cancel a manual entry."),
        # Financial views
        "finance.view_profit": (CapabilityCategory.FINANCIAL_VIEWS, "View P&L."),
        "finance.view_cash": (
            CapabilityCategory.FINANCIAL_VIEWS,
            "View cash balance and cash movements.",
        ),
        "finance.view_payables_receivables": (
            CapabilityCategory.FINANCIAL_VIEWS,
            "View payables and receivables.",
        ),
        # Reports / exports
        "report.view": (CapabilityCategory.REPORTS, "View reports."),
        "export.data": (CapabilityCategory.REPORTS, "Export PDF/Excel."),
        # Notifications
        "notification.view": (CapabilityCategory.NOTIFICATIONS, "View notifications."),
        "notification.mark_read": (CapabilityCategory.NOTIFICATIONS, "Mark notifications as read."),
    }
)

# The frozen set of all codes (order-independent membership check).
CANONICAL_CAPABILITIES: Final[frozenset[str]] = frozenset(_CANONICAL.keys())

# Pre-computed: the Owner role gets every capability (per §19.3).
OWNER_CAPABILITIES: Final[frozenset[str]] = frozenset(_CANONICAL.keys())

# The Staff role's default-on capabilities (per §19.3).
_STAFF_DEFAULT_CODES: Final[tuple[str, ...]] = (
    "auth.login",
    "category.view",
    "unit.view",
    "product.view",
    "contact.view",
    "payment_method.view",
    "cost_type.view",
    "sale.view",
    "sale.create",
    "sale.edit_own_draft",
    "purchase.view",
    "purchase.create",
    "purchase.edit_own_draft",
    "inventory.view",
    "production.view",
    "notification.view",
    "notification.mark_read",
)
STAFF_DEFAULT_CAPABILITIES: Final[frozenset[str]] = frozenset(_STAFF_DEFAULT_CODES)


def is_capability(code: str) -> bool:
    """Return True iff ``code`` is a canonical capability code."""
    return code in CANONICAL_CAPABILITIES


__all__ = [
    "CANONICAL_CAPABILITIES",
    "OWNER_CAPABILITIES",
    "STAFF_DEFAULT_CAPABILITIES",
    "CapabilityCategory",
    "is_capability",
]
