"""Capabilities read-only service.

The capability catalog lives in ``app.authz.caps`` (the canonical,
ordered definition of every capability code) and in the
``capabilities`` table (seeded from ``schema.sql``).

The OpenAPI response requires derived fields that the DB does not
store:

* ``domain``        → ``CapabilityCategory`` from the catalog.
* ``description``   → catalog description (falls back to DB).
* ``default_owner`` → Owner role receives every capability.
* ``default_staff`` → Staff role's default set from ``STAFF_DEFAULT_CAPABILITIES``.

This service reads the DB rows to obtain stable ``id`` values and
description fallbacks, then layers on the catalog/seed metadata. This
keeps the canonical catalog in one place and avoids any DDL change.
"""

from __future__ import annotations

from typing import Any

from app.authz.caps import (
    STAFF_DEFAULT_CAPABILITIES,
    CapabilityCategory,
)
from app.db import UnitOfWork


class CapabilityService:
    """Read-only service for the capability catalog."""

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def list_capabilities(
        self,
        *,
        page: int,
        per_page: int,
        q: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return a paginated list of capabilities with derived metadata.

        Returns ``(page_rows, total_count)``.
        """
        # Pull the full catalog from DB so the stable id is available.
        rows = await self._uow.fetch_all(
            """
            SELECT id, code, description
            FROM capabilities
            ORDER BY id
            """,
            None,
        )

        # Layer on catalog-derived fields; drop any DB rows whose code is
        # not in the frozen catalog (defensive: should never happen).
        catalog = _catalog_lookup()
        items: list[dict[str, Any]] = []
        for r in rows:
            code = r["code"]
            meta = catalog.get(code)
            if meta is None:
                continue
            items.append(
                {
                    "id": int(r["id"]),
                    "code": code,
                    "domain": meta["domain"],
                    "description": r["description"] or meta["description"],
                    "default_owner": True,
                    "default_staff": code in STAFF_DEFAULT_CAPABILITIES,
                }
            )

        # Optional search filter (case-insensitive substring on code/description).
        if q:
            needle = q.strip().lower()
            items = [
                it
                for it in items
                if needle in it["code"].lower()
                or (it["description"] is not None and needle in it["description"].lower())
            ]

        total = len(items)
        per_page = max(1, min(per_page, 500))
        page = max(1, page)
        start = (page - 1) * per_page
        end = start + per_page
        return items[start:end], total


def _catalog_lookup() -> dict[str, dict[str, Any]]:
    """Build a code -> metadata lookup from the frozen catalog.

    ``app.authz.caps`` keeps the canonical ``OrderedDict`` in module
    private ``_CANONICAL``. We import it by name from the module to
    preserve the existing API surface while still accessing the
    ordering/category/description metadata.
    """
    from app.authz import caps as caps_module

    canonical = caps_module._CANONICAL
    return {
        code: {
            "domain": category.value if isinstance(category, CapabilityCategory) else str(category),
            "description": description,
        }
        for code, (category, description) in canonical.items()
    }
