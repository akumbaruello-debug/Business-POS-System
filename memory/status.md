# Business-POS-System - STATUS
**Last updated:** 2026-09-04
**Spec/phase:** Backend V1.9 (backend P0 blockers CLOSED) — **Contacts backend RECOVERED** ✅ — ready for frontend integration

## Done
- M1 foundation (auth, errors, health, logging, DB pool, rate-limit infra)
- M2+ routes scaffolded (30+ routers: sales, products, purchases, categories, etc.)
- G.2 OriginCheckMiddleware: installed conditionally (works if CORS origins set)
- G.3 Security headers: installed (X-Content-Type-Options, X-Frame-Options, etc.)
- G.6 Cron jobs: `cron.d/bpos-prune` exists, prune scripts + tests pass
- X.6 below-cost notification: implemented
- F.1–F.10: routes exist, but some are stubs
- G.1 Rate limiting: CLOSED ✅ — 13/13 rate-limit tests pass; login rate-limit `5/15minute` and user-limits wired correctly
- F.8 Notifications: routes exist + tests exist + pass
- F.5 Dashboard: routes implemented (GET /dashboard, GET /dashboard/inventory)
- F.6 Exports: CLOSED ✅ — `_generate()` now produces real PDF/XLSX via reportlab/openpyxl; MIME type + filename extension match format; tests verify magic bytes
- Users & Roles: CLOSED ✅ — new `/users` routes registered; `UserService` + schemas; authz with `user.view`/`user.manage`/`user.grant_capability`/`user.revoke_capability`; 19/19 user tests pass
- Test infrastructure: rate-limit test pollution CLOSED ✅ — `POS_ENV=test` + test-only skip in `check_limit()` keeps production limits unchanged
- **Contacts backend recovery: CLOSED ✅** — original Python source unrecoverable from git (reflog, stashes, branches, dangling commits, deleted-file history all empty); reconstructed against canonical contract using `categories` route pattern. 6 endpoints mounted (`listContacts`, `createContact`, `getContact`, `updateContact`, `deleteContact`, `deactivateContact`). Response shape matches `openapi.yaml` `Contact` schema exactly: `{id, type, name, phone, email, address, notes, is_active, created_at, updated_at, version, created_by}`. ETag/version derived from `updated_at`; If-Match required on PATCH; Idempotency-Key on POST/DELETE/deactivate; audit logging on every mutation. All 14 runtime HTTP smoke tests pass (200/201/204/400/401/403/404/412 paths).
- **Missing `contact.manage` capability restored ✅** — pre-existing gap: OpenAPI + frontend both required `contact.manage` for DELETE/deactivate, but it was never seeded in the canonical capability catalog or DB migration. Added to `_CANONICAL` in `app/authz/caps.py`, to `schema.sql`, and to `db/migrations/m0001__initial_schema_baseline.sql`. Owner role now gets it via the existing CROSS JOIN seed. `pos_dev` DB was manually patched (INSERT capability + role_capability).

## In Progress
- (none)

## Blocked / Pending

### P0 — MUST FIX BEFORE FRONTEND (backend)
- (none remaining)

### P0 — frontend
- No login/auth UI — backend auth works but no login page exists in any V0 zip or project.
- Contacts pytest suite not authored — smoke-tested via HTTP only. Recommend `tests/api/test_contacts.py` mirroring `test_categories.py` (auth, list, filter, create, get, patch with If-Match, deactivate, delete with FK-block).

### P1 — SHOULD FIX (does not block shell integration)
- G.4 Metrics: BLOCKED — `app/metrics.py` missing, `middleware/metrics.py` references undefined `Counter`, `/metrics` absent, no `BPOS_ENABLE_METRICS` setting. Needs architectural decision before implementation.
- Ruff/mypy production errors: 14 ruff, 5 mypy in app/ (pre-existing).
- Version string stale: `main.py` says 0.1.0 / M1.
- Idempotency-Key replay: `IdempotencyStore.start()` records fingerprint but the route layer does not implement full replay-on-duplicate-key logic (matches `categories.py` live pattern; deferred).

## Baseline (session-start checkpoint, 2026-09-04)

- Exports: 9/9 PASS
- Users & Roles: 19/19 PASS
- Rate limits: 12/12 PASS
- Contacts: HTTP smoke test PASS (14 scenarios on port 8006; all 6 endpoints verified)
- Full suite: not re-run
- Ruff: clean after fixing exports import sorting; remaining issues pre-existing
- Mypy: pre-existing errors in dashboard.py, production_runs.py, sales.py, metrics.py; no new errors from contacts/users/exports changes

## Source of truth (links only)

- Backend-Architecture-V1.0.md §2 (tech stack), §23 (security), §24 (observability), §12 (ETag/versioning)
- Backend-Implementation-Plan-V1.0.md §7 (G.1–G.7), §6 (F)
- openapi.yaml (§15.5 /dashboard, §15.6 /exports, §15.11-15.17 /users, §Contacts /customers+suppliers)
- app/middleware/metrics.py (BLOCKED)
- app/services/exports.py, app/api/v1/exports.py
- app/services/users.py, app/api/v1/users.py, app/validation/users_schemas.py
- app/services/contacts.py, app/api/v1/contacts_routes.py, app/validation/contacts_schemas.py, app/repositories/contacts.py
- app/authz/caps.py (`contact.manage` capability restored)
- schema.sql, db/migrations/m0001__initial_schema_baseline.sql (`contact.manage` seed restored)
- tests/api/test_exports_f6.py, tests/api/test_users.py
- backend/test_contacts_api.py (transient smoke-test script, not committed)
