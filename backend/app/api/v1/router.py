from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    auth,
    cash_movements,
    categories,
    contacts_routes,
    cost_types,
    dashboard,
    exports,
    financial_categories,
    inventory,
    manual_entries,
    notifications,
    payment_methods,
    production_cost_lines,
    production_inputs,
    production_runs,
    products,
    purchase_lines,
    purchase_returns,
    purchase_shipping,
    purchases,
    refunds,
    reports,
    sales,
    settings,
    stock_movements,
    supplier_repayments,
    units,
)

router = APIRouter()
router.include_router(auth.router)
router.include_router(payment_methods.router)
router.include_router(financial_categories.router)
router.include_router(units.router)
router.include_router(categories.router)
router.include_router(contacts_routes.router)
router.include_router(cost_types.router)
router.include_router(products.router)
router.include_router(sales.router)
router.include_router(sales.returns_router)
router.include_router(purchases.router)
router.include_router(purchase_lines.router)
router.include_router(purchase_shipping.router)
router.include_router(purchase_returns.sub_router)
router.include_router(purchase_returns.top_router)
router.include_router(production_runs.router)
router.include_router(production_inputs.router)
router.include_router(production_cost_lines.router)
router.include_router(inventory.router)
router.include_router(refunds.router)
router.include_router(supplier_repayments.router)
# E.7 — stock-movements read endpoints
router.include_router(stock_movements.router)
# E.8 — manual entries
router.include_router(manual_entries.router)
# E.9 — cash-movements read endpoints
router.include_router(cash_movements.router)
# F.1 — reports (sales, purchases, inventory, inventory-movements)
router.include_router(reports.router)
# F.5 - dashboard (KPIs + inventory)
router.include_router(dashboard.router)
# F.6 - exports (PDF/Excel jobs)
router.include_router(exports.router)
# F.8 - notifications (read API)
router.include_router(notifications.router)
# F.9 - settings (read + patch)
router.include_router(settings.router)

__all__ = ["router"]
