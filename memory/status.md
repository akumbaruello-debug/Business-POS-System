# Business-POS-System - STATUS
**Last updated:** 2026-09-09
**Spec/phase:** Frontend Integration Phase — Products Management COMPLETE ✅ (Phase 2/18). Next: Inventory.

## Done
- Backend foundation: M1 auth/errors/health/DB/rate-limit; 30+ routers; OriginCheck; security headers; cron prune; F.5 dashboard routes; F.6 exports real PDF/XLSX; F.8 notifications; Users & Roles 19/19; Contacts recovery 34/34; test-infra pollution closed.
- Dashboard (Phase 1) — reference integration pattern in `app/(protected)/dashboard/page.tsx`.
- Sidebar UX: independent scroll ✅; old collapse button removed ✅; logo toggle works ✅.

### Audit-fix session (2026-09-07) — all 4 P0 + 3 P1 resolved
- **P0 Best-sellers 10×**: `dashboard_repo._BEST_SELLERS_SQL` used `SUM(sl.quantity * sl.line_total)`; `line_total` already = qty×unit_price. Now `SUM(sl.line_total)`. Independently verified: DB recompute == API (Ikan Asin Jambal 1,092,000 / Salmon Fillet Beku 1,015,000).
- **P0 Monetary formatting**: `formatIDR` stripped of `Rp ` prefix; id-ID thousands separators kept. Verified exact: `12.514.000`, `7.492.703`, `-992.141`, `489.975.703`.
- **P0 Negative delta**: `MetricCard` now takes numeric `deltaPct` (was pre-formatted string) and branches styling from the sign: positive green/↗, negative red/↘, zero neutral/→. Added `.metric-change.neutral` CSS.
- **P1 Inventory semantics**: both KPI cards relabeled `Inventory (at cost)` (cost-basis value kept — standard accounting).
- **P1 Dead nav**: removed 8 sidebar routes (sales/pos/returns/purchases/payments/reports/users/settings) + fabricated Sales "12" badge. Sidebar ships only live routes (dashboard/products/inventory/customers/suppliers).
- **P1 Dead quick-actions**: removed `/pos` (New sale) + `/payments` (Record payment); kept `/products`.
- **P1 Fake header**: removed fabricated global search, demo notifications, inert account-menu items (Profile/settings/preferences/language). Only real Sign out remains. `app-shell.tsx` cleaned of dead search/notification state + Cmd+K.

### Products Management (Phase 2) — COMPLETE 2026-09-09 ✅
- Backend: `POST /products/import` two-phase (validate → commit). `ProductImportReport`/`ProductImportResult` schemas. `validate_import_rows` + `commit_import` in `services/products.py`. Products export report in `reports/products_repo.py` + `exports.py` dispatch.
- Frontend: full CRUD page (`app/(protected)/products/page.tsx`), `_import-dialog.tsx`, `_export-dialog.tsx`, `_category-dialog.tsx`. Capability-gated (Owner vs Staff). If-Match handling. Category/unit lookups.
- Tests: `test_products.py` (36), `test_products_import.py` (14), `test_period_aliases.py` (14), `test_exports_f6.py::TestF6CreateAndGet` (8) — all PASS.
- Commit: `dcb8797` — 36 files, 4604 insertions, 903 deletions.

## In Progress
- (none — between phases)

## Blocked / Pending
### P0 — resolved (no longer open)
- Best-sellers revenue, Rp prefix, negative-delta styling: all FIXED.

### Environment conflict (needs owner decision)
- **WhatsApp bridge vs frontend port 3000**: Hermes gateway's `bridge.js` hardcodes `--port 3000` (no config.yaml/env override). Frontend must keep :3000 (backend Origin-check). Gateway was **left STOPPED**. To restore messaging, the bridge port must be moved off 3000 on the Hermes side.

### P1 — backend (pre-existing)
- G.4 Metrics BLOCKED (app/metrics.py missing, arch decision needed). Ruff/mypy pre-existing errors. Version string stale 0.1.0/M1.

### P2 — minor
- Dead CSS for removed header/search/notification popovers still in `globals.css` (harmless).
- Dashboard `period=custom` date-picker deferred (backend supports it; UI not built).
- Customers/Suppliers pages: list/view/deactivate wired; create/edit not yet wired in UI (backend endpoints exist).

## Verification performed
- `pnpm typecheck` PASS (0 errors).
- 84 targeted tests PASS (0 failures).
- `test_categories.py` (38) PASS.
- Stale If-Match test (`test_patch_with_stale_if_match_returns_412`) PASS.
- Full suite not re-run (timeout); no evidence of new regressions.

## Live dev state
- Backend uvicorn :8000 (PID 831), Postgres :5433, frontend :3000 — all running.
- Dev-DB creds: owner/OwnerPass123!.
- Auth: POST /api/v1/auth/login requires Idempotency-Key (UUID) + Origin: localhost:3000. Login rate limit 5/15min.
- Demo seed: `backend/scripts/seed_demo_comprehensive.py` (deterministic, idempotent).

## Next session
- Phase 3/18: Inventory frontend integration (stock adjustments, movements, valuation).
- Customers: wire create/edit to backend.
- Suppliers: wire create/edit to backend.
- Remaining backend domains (sales, purchases, returns, reports, users, settings) still lack frontend UI.

## Source of truth (links only)
- Backend-Architecture-V1.0.md, openapi.yaml §15.5 (/dashboard), §15.7 (/products)
- backend/app/reports/dashboard_repo.py, dashboard.py, period.py, products_repo.py
- backend/app/services/products.py, exports.py
- frontend/app/(protected)/products/page.tsx (ProductFormDialog, Import/Export/Category dialogs)
- frontend/lib/format.ts (formatIDR), frontend/lib/dashboard-types.ts, frontend/lib/session.tsx
- V0 design source: V0-design-zip-file/new1.zip (canonical shell)