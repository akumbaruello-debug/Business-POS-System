# Business-POS-System - STATUS
**Last updated:** 2026-09-07
**Spec/phase:** Frontend Integration Phase — Dashboard audit-fix session COMPLETE (Phase 1/18). Next: Products Management.

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

## In Progress
- (none — between phases)

## Blocked / Pending
### P0 — resolved this session (no longer open)
- Best-sellers revenue, Rp prefix, negative-delta styling: all FIXED.

### Environment conflict (needs owner decision)
- **WhatsApp bridge vs frontend port 3000**: Hermes gateway's `bridge.js` hardcodes `--port 3000` (no config.yaml/env override). Frontend must keep :3000 (backend Origin-check). Gateway was **left STOPPED**. To restore messaging, the bridge port must be moved off 3000 on the Hermes side.

### P1 — backend (pre-existing)
- G.4 Metrics BLOCKED (app/metrics.py missing, arch decision needed). Ruff/mypy pre-existing errors. Version string stale 0.1.0/M1.

### P2 — minor
- Dead CSS for removed header/search/notification popovers still in `globals.css` (harmless).
- Positive-delta card styling not exercised against live data (seed yields negative/zero deltas this window).
- Dashboard `period=custom` date-picker deferred (backend supports it; UI not built).

## Verification performed this session
- Independent `psql` recompute of per-product revenue == API best-sellers (exact).
- Live API: this_month (sales 15,399,000 / purchases 7,492,703 / net 5,020,472.88), this_year (sales 217,016,000).
- Sales Trend Σ = KPI exactly for this_month and this_year.
- Delta sign path confirmed from real comparison data (-0.799 → -79.9%).
- `npm run typecheck` PASS; `npm run build` PASS.

## Browser verification limitation (honest)
- Chrome remote-debugging permission denied → `browser_exec` returns empty (no JS/DOM/network).
- In-app preview reached the login page, but the drive element collector mis-mapped `#username`/`#password`, and the sign-in click produced **no `POST /auth/login`** (native `required` validation or focus error) — not an app defect; **no code changed** to work around it.
- **Live browser-rendered values (no-Rp strings, red/↘ delta, best-seller rows, chart) NOT visually confirmed.** DB/API + build verification PASS.

## Live dev state
- Backend uvicorn :8000 (PID 15852), Postgres :5433 (PID 14964), frontend :3000 — all running.
- Dev-DB creds: owner/OwnerPass123! (reset this session; account had been locked + wrong password).
- Auth: POST /api/v1/auth/login requires Idempotency-Key (UUID) + Origin: localhost:3000. Login rate limit 5/15min.
- Demo seed: `backend/scripts/seed_demo_comprehensive.py` (deterministic, idempotent) — 288 sales, 731 sale_lines.

## Next session
- Phase 2/18: Products Management (frontend integration for `/products`).
- Resolve WhatsApp-bridge/frontend port-3000 conflict before restarting gateway.

## Source of truth (links only)
- Backend-Architecture-V1.0.md, openapi.yaml §15.5 (/dashboard)
- backend/app/reports/dashboard_repo.py, dashboard.py, period.py
- frontend/app/(protected)/dashboard/page.tsx (MetricCard, SalesTrendChart, ComparisonBadge)
- frontend/lib/format.ts (formatIDR), frontend/lib/dashboard-types.ts
- V0 design source: V0-design-zip-file/new1.zip (canonical shell)
