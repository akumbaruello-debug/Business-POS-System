"""F.1 — Inventory report service.

Thin orchestration layer between ``GET /reports/inventory`` and the
SQL in :mod:`app.reports.inventory_repo`. The endpoint is a
point-in-time snapshot — period params are accepted for OpenAPI
contract parity but are ignored (documented in the route docstring).
"""

from __future__ import annotations

from typing import Any

from app.db import UnitOfWork
from app.reports import inventory_repo

__all__ = ["InventoryReportService"]


class InventoryReportService:
    __slots__ = ("_uow",)

    def __init__(self, uow: UnitOfWork) -> None:
        self._uow = uow

    async def get_report(self) -> dict[str, Any]:
        """Return the canonical inventory snapshot for the response.

        The shape is exactly the ``InventoryReportData`` model in
        :mod:`app.reports.schemas`. No comparison block (per audit
        decision: the OpenAPI ``InventoryReportResponse`` schema has
        no ``comparison`` field; the inventory snapshot is a
        point-in-time view).
        """
        data = await inventory_repo.fetch_inventory_report(self._uow)
        return {"data": data}
