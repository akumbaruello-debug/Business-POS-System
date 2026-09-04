"""Derived-accounting category blacklist guard (BR-FIN-003).

Single source of truth for which financial-category ``name`` values are
forbidden because they would duplicate a *derived* accounting category.

Authoritative definition: ``API-Architecture-V1.0.md`` §15.7.5:

    name is validated (case-insensitive) against the blacklist:
    "Sales", "COGS", "Production", "Purchase Shipping", "Refund",
    "Purchase", "Sales Revenue", "Cost of Goods Sold", "Other Income",
    "Operating Expenses", and case-insensitive variants.
    Server returns 400 manual_entry_duplicate_of_derived on violation.

Design
------
The blacklist is a *frozenset* of upper-cased names. Every mutation path
that touches ``financial_categories.name`` — create, update (rename), and
any future import/bulk path — **must** call :func:`assert_not_blacklisted`
before the write. There is no other definition of the list in the
codebase, so enforcement cannot be bypassed by calling a different M2
endpoint: the routes and the service both route through this function.

The list is matched on the ``name`` column only (``code`` is the user's
own slug and is unrestricted). Matching is case-insensitive via
``str.upper()``; whitespace is stripped first so ``  Sales  `` matches.
"""

from __future__ import annotations

import unicodedata
from typing import Final

from app.errors import ManualEntryDuplicateOfDerived

#: The complete, frozen blacklist. Order is irrelevant (set lookup is O(1)).
_DERIVED_CATEGORY_NAMES: Final[frozenset[str]] = frozenset(
    {
        "SALES",
        "COGS",
        "PRODUCTION",
        "PURCHASE SHIPPING",
        "REFUND",
        "PURCHASE",
        "SALES REVENUE",
        "COST OF GOODS SOLD",
        "OTHER INCOME",
        "OPERATING EXPENSES",
    }
)

#: Public read-only view of the raw spellings (for tests + docs).
DERIVED_CATEGORY_BLACKLIST: Final[tuple[str, ...]] = (
    "Sales",
    "COGS",
    "Production",
    "Purchase Shipping",
    "Refund",
    "Purchase",
    "Sales Revenue",
    "Cost of Goods Sold",
    "Other Income",
    "Operating Expenses",
)


def is_blacklisted(name: str) -> bool:
    """Return True if ``name`` matches a derived-category name.

    Matching is case-insensitive and whitespace-normalized (surrounding
    whitespace trimmed, internal runs of whitespace collapsed). Diacritics
    are *not* folded — only ASCII case folding is applied, exactly as the
    spec's "case-insensitive variants" implies.
    """
    normalized = unicodedata.normalize("NFKC", name or "")
    normalized = " ".join(normalized.upper().split())
    return normalized in _DERIVED_CATEGORY_NAMES


def assert_not_blacklisted(name: str, *, field: str = "name") -> None:
    """Raise :class:`ManualEntryDuplicateOfDerived` if ``name`` is blacklisted.

    Called by **every** mutation path that can set ``financial_categories.name``
    (create + update/rename). Raising here guarantees no alternate route can
    bypass the rule — a rename through PATCH is just as blocked as a fresh
    create, because both flow through this single assertion.
    """
    if is_blacklisted(name):
        raise ManualEntryDuplicateOfDerived(
            f"'{name}' is a derived accounting category and may not be used as "
            f"a manual financial category name.",
            details={"field": field, "value": name, "rule": "derived_category_blacklist"},
        )


__all__ = [
    "DERIVED_CATEGORY_BLACKLIST",
    "assert_not_blacklisted",
    "is_blacklisted",
]
