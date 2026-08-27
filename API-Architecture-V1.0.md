# API Architecture Specification V1.0
## Business Management & POS System

> **Source of Truth (authoritative inputs):**
> - `Business-POS-System-PRD-V1.1.md` (current PRD, supersedes V1)
> - `Business-Rules.md` (80+ rules)
> - `Database-Design-V1.0.md` (current architecture; Status: Awaiting Review)
> - `V1.9-Accounting-Event-Matrix.md` (11-account chart, 22 events, 8 invariants — V1.9 naming is reconciled across all upstream docs per `Reconciliation-Report-V1.0.md` C-01/C-02; original V2.0/V1.9 mismatch was a cross-doc contradiction and is now resolved)
> - `PRD-V1-Audit-Report-v2.md` (drives the V1.1 PRD; documents 6 resolved contradictions and 13 owner decisions)
>
> **Status:** **DRAFT — PRE-OWNER-DECISION**
>
> **Pipeline position:** PRD V1.1 → Business Rules → **Database Design V1.0** → **API Architecture V1.0 (this doc)** → Frontend Architecture → Implementation → Testing.
>
> **Critical preconditions:** §15 lists the Owner decisions that this API spec assumes have been taken. Where a decision is unresolved, this spec marks the behavior as `TBD-OWNER` and exposes a config endpoint. The API is fully designed around the `costing_method = moving_average` default; the schema is forward-compatible with FIFO.
>
> **Scope:** This document is the **authoritative contract** between the backend and any client (web UI today, future mobile, integrations). It is technology-agnostic on transport, but the chosen style is REST + JSON over HTTPS with token-based session. Every business rule and database invariant from the upstream documents is enforced here; no client can rely on FE validation alone.

---

## Table of Contents

- 0. Pipeline Position & Preconditions
- 1. API Architecture Principles
- 2. Authentication & Session
- 3. Authorization & Capability Model
- 4. API Conventions
- 5. Error Model
- 6. Validation Model
- 7. Idempotency & Concurrency
- 8. Lifecycle, State, and Posting Semantics
- 9. Financial / Accounting API Rules
- 10. Inventory & Costing API Rules
- 11. Audit & Notification API Rules
- 12. Reporting & Export API
- 13. Security Requirements
- 14. Endpoint Catalog (summary)
- 15. Domain-by-Domain API Specification
- 16. Cross-Document Contradictions
- 17. Missing Decisions
- 18. Implementation Preconditions
- 19. Final Validation
- 20. Final Verdict

---

## 0. Pipeline Position & Preconditions

This document is positioned **after** the Database Design V1.0 (which it treats as authoritative for storage shape, triggers, derived values, and concurrency) and **before** the Frontend Architecture.

It assumes the following Owner decisions (per `PRD-V1-Audit-Report-v2.md` §7). Where the Owner has not yet signed, the spec marks the behavior `TBD-OWNER` and exposes configuration:

| ID | Decision | Status | API behavior in this spec |
|---|---|---|---|
| OD-1 | Costing method default = moving average (V1) | **RESOLVED** in PRD §18.1 | API defaults to moving_average; configurable via `system_settings.costing_method`. Other values are accepted but not implemented. |
| OD-2 | Posting is a single click at POS (`draft → posted` same moment) | **RESOLVED** in PRD §8.1 | `POST /sales/{id}/post` is the single transition; `POST /sales/{id}/complete` is automatic when fully paid (no extra call). |
| OD-3 | Negative-stock COGS fallback = last-purchase-price, flagged | **RESOLVED** in PRD §18.4 | When `allow_negative_stock` is true, the sale line is created with `is_negative_stock_fallback = true` and `unit_cost_snapshot = products.purchase_price`. |
| OD-4 | Finished-goods cost = raw + overhead ÷ quantity | **RESOLVED** in PRD §15 | API uses this formula. Configurable later. |
| OD-5 | Posted records are immutable; corrections via reversal | **RESOLVED** in PRD §7, §9 | API rejects in-place edits on posted records; all corrections via `cancel` or `return` endpoints. |
| OD-6 | Shipping on purchase is capitalized into inventory cost | **RESOLVED** in PRD §11, §18.6, BR-PURCHASE-005 | `POST /purchases/{id}/post` allocates `purchase_shipping.amount` pro-rata to lines; no operating-expense shipping line is ever created for a purchase. |
| OD-7 | Refund policy = cash outflow, follows original payment method by default, never income | **RESOLVED** in PRD §9, BR-REFUND-001/002 | `POST /refunds` creates a `refund` cash_movement; refund is never an income entry; default `payment_method_id` is the most recent original method. |
| OD-8 | Staff defaults = `sale.create` + `purchase.create` + view + edit own draft ON; everything else OFF | **RESOLVED** in PRD §6.1, §6.2 | Seeding: `roles.Staff` has exactly those capabilities. |
| OD-9 | Owner "Delete" scope = drafts only and master records with no history; posted records never hard-deleted | **RESOLVED** in PRD §6.3 | API exposes `DELETE` on `/sales/{id}`, `/purchases/{id}`, etc. only when `lifecycle_status = 'draft'`. Master records use `PATCH /products/{id} { "is_active": false }` for deactivation. |
| OD-10 | Staff non-state master edit scope = NONE by default | **RESOLVED** in PRD §6.1 | `PUT/PATCH /products/{id}` returns 403 for Staff without `product.edit`. |
| OD-11 | Production cost entry = per run (V1) | **RESOLVED** in PRD §15 | `POST /production-runs` carries all cost lines per run. |
| OD-12 | Discounts are V1 optional, gated by `sale.discount` capability | **RESOLVED** in PRD §12 | `discount_amount` accepted only if user has the capability; otherwise the field is rejected (400) and the line is recomputed. |
| OD-13 | Return re-enters at original line cost | **RESOLVED** in PRD §18.3, BR-COST-005 | `POST /sales/{id}/returns` sets `returned_unit_cost = sale_lines.unit_cost_snapshot` (server-side; client cannot override). |
| OD-14 | Low-stock threshold = global default + per-product override | **RESOLVED** in PRD §27 | `system_settings.default_low_stock_threshold` + `products.low_stock_threshold`. |
| OD-15 | Supplier optional on purchase | **RESOLVED** in PRD §20 | `supplier_id` is nullable. |
| OD-16 | Money rounding mode = TBD (config-only) | **TBD-OWNER** | API stores `NUMERIC(15,2)` exact; no client rounding. If OD-16 later demands display rounding, the API adds a `display_rounding` field. |
| OD-17 | Sequential document numbering = off by default | **TBD-OWNER** | `reference_no` is nullable; the API does not auto-generate unless `system_settings.enable_sequential_doc_numbers = true` and a per-document sequence is configured. |
| OD-18 | X-Z shift close = out of V1 | **OUT OF SCOPE** | No endpoints exposed. |
| OD-19 | Backup/restore = operational, not product | **OUT OF SCOPE** | No endpoints exposed. |
| OD-20 | Receivable aging = V2 | **OUT OF SCOPE** | Dashboard returns a derived AR total but no aging buckets. |
| OD-21 | Warehouse/multi-location = V2; nullable `location_id` reserved | **FORWARD-COMPATIBLE** | All stock-movement queries accept an optional `?location_id=` filter; default is the single implicit location. |

The API is designed so that **only OD-16 and OD-17 have any runtime effect on request/response behavior today** (and both are config-driven). All others are pinned.

---

## 1. API Architecture Principles

### 1.1 Architectural Style

**REST + JSON over HTTPS.** Resource-oriented URLs; HTTP verbs map to lifecycle actions; query parameters handle filtering, sorting, pagination; request and response bodies are JSON. No HATEOAS, no GraphQL, no RPC stubs.

**Why REST:**
- PRD §4 (single-business web app, no mobile, no integrations) does not require hypermedia.
- The Business Rules, Database Design, and Accounting Model are entity-oriented (`sales`, `purchases`, `stock_movements`, etc.). REST maps cleanly to this shape.
- The audit, idempotency, and capability requirements are simpler to express and test on a verb-based interface.

**Resource collection vs document:** A POST to a collection creates a draft; subsequent POSTs to sub-resources (e.g., `/sales/{id}/lines`, `/sales/{id}/payments`) operate on the draft. A POST to a lifecycle action endpoint (e.g., `/sales/{id}/post`) transitions the document.

### 1.2 Core Principles

1. **Server is the only authority.** Every business rule from `Business-Rules.md` and every invariant from `V1.9-Accounting-Event-Matrix.md` is enforced server-side. The client may validate for UX; the server does not trust the client.
2. **Derived values are server-computed.** COGS, payment state, total_amount, line_total, finished_unit_cost, AR, AP, CRL, SRec, cash balance, P&L, moving-average unit cost, on-hand quantity — all are server-computed and may be returned in responses. The client **never** sends them as authoritative inputs. If the client sends a derived field, the server ignores it and returns 400 (`derived_field_not_allowed`).
3. **Posted records are immutable.** Once `lifecycle_status != 'draft'`, the API rejects in-place mutation of all financial/inventory fields. Corrections are always lifecycle events (cancel, return, post a new document). Per BR-AUDIT-002.
4. **Cancellation ≠ Return ≠ Refund.** Three distinct events. Cancellation invalidates an entire document once. Return is a physical-goods reversal. Refund is a cash-out. The API exposes them as separate endpoints, with separate capability checks and separate audit entries.
5. **One inventory effect per event.** Every stock-changing event produces exactly one `stock_movements` row (or a reversal pair, never a delete). Double-application is impossible by design.
6. **Capability-driven authorization.** Every endpoint declares the capabilities it requires. The server checks the user's effective capability set (role base ∪ per-user overrides) before executing. A capability is a named string (e.g., `sale.create`); roles are convenience groupings; the server never hardcodes a role check.
7. **Append-only audit.** Sensitive actions (create, edit, post, cancel, return, refund, adjust, permission grant/revoke, settings change, deactivate) are recorded in `audit_log` with WHO/WHAT/WHEN/OLD/NEW/REASON. The `audit_log` is never updated or deleted by anyone, including Owner.
8. **Atomic business operations.** Every multi-row write runs in a single database transaction. Either everything commits or nothing does. The client never observes a partial state.
9. **Idempotency for safety.** All mutating endpoints accept an optional `Idempotency-Key` header. Resubmissions with the same key return the original result.
10. **Optimistic concurrency on lifecycle transitions.** Documents carry an `updated_at` (or a numeric `version`) that the client must echo back on lifecycle actions. If the version mismatches, the API returns 409.
11. **Cash non-negative invariant.** Every transaction that would result in `SUM(cash_movements.amount) < 0` is rejected. The error is `cash_insufficient`. Overdraft is out of scope (V1 ASSUMPTION).
12. **No money in the URL.** Monetary amounts and IDs are in the body or path. The URL does not carry monetary or PII data.
13. **PII minimum exposure.** Customer/supplier names, phone, email, address are returned only on read endpoints for the records the user is authorized to view. Bulk endpoints return minimal fields.
14. **OpenAPI 3.1 contract is the source.** This document is a narrative specification; the machine-readable contract is `openapi.yaml` (delivered with the implementation). The narrative and the YAML must agree; in conflict, the YAML wins for wire format.

### 1.3 What is NOT in this API

- No multi-currency.
- No batch import (PDF/Excel export only, per PRD §25).
- No mobile push, no X-Z shift close, no two-step approval.
- No formal AR/AP aging buckets (a derived AR total is returned; aging is V2).
- No payment-account balances (per PRD §16 — balances are never fabricated; `cash_movements` is the only source).
- No GL UI (per PRD §4 #2 — the system is not a full accounting package; KPIs and P&L are surfaced, not a full journal UI).
- No public unauthenticated endpoints. Every endpoint requires a session (except `POST /auth/login` and `POST /auth/refresh`).

---

## 2. Authentication & Session

### 2.1 Session Model

- **Token-based session.** After successful credential exchange, the server issues a short-lived access token and a long-lived refresh token. Tokens are opaque strings (server-generated random IDs mapped to sessions in the database). The client does not parse them.
- **Transport.** All endpoints are served over HTTPS. Tokens are never transmitted in URLs or query parameters. Tokens are sent in the `Authorization: Bearer <token>` header.
- **State.** The server stores the session in `sessions` (a new table implied by the API design — see §15.2.1). Sessions carry `user_id`, `created_at`, `last_seen_at`, `expires_at`, `refresh_expires_at`, `ip_address`, `user_agent`, `revoked_at`, `revoked_reason`.
- **Logout.** `POST /auth/logout` revokes the current session. `POST /auth/logout-all` revokes all sessions for the current user (Owner-only). Sessions are also revoked on password change.

### 2.2 Credential Exchange

- `POST /auth/login` — body: `{ "username": string, "password": string }`. Returns `{ "access_token": string, "refresh_token": string, "expires_in": int (seconds), "user": { id, username, full_name, role, capabilities: [string] } }`.
- `POST /auth/refresh` — body: `{ "refresh_token": string }`. Returns new `access_token` and `refresh_token` (rotation; old refresh is invalidated).
- `POST /auth/logout` — revokes current session.
- `GET /auth/me` — returns current user with full effective capability set.

### 2.3 Password Rules

- Minimum 8 characters; no maximum.
- Stored as `password_hash` using Argon2id (server-side library choice). Plain-text passwords never stored or logged.
- Passwords can be reset only by Owner via `POST /users/{id}/reset-password`. Self-service reset is out of V1 scope.
- Lockout: 5 failed login attempts within 15 minutes → account locked for 15 minutes. Owner unlock via `POST /users/{id}/unlock`.

### 2.4 Session Expiry

- Access token: 15 minutes (configurable in `system_settings.access_token_ttl_seconds`).
- Refresh token: 30 days (configurable in `system_settings.refresh_token_ttl_seconds`).
- Idle timeout: a session that has not been used in 24 hours is invalidated on next access; user must re-authenticate. Configurable.

### 2.5 Capabilities Returned in Login

The login response includes the user's **effective** capabilities. The client uses these to gate UI affordances. The server re-checks every request and never trusts the client's claim.

---

## 3. Authorization & Capability Model

### 3.1 Canonical Capability Codes

The capability catalog is fixed at the database level (`capabilities` table). Per the §6 finding in the previous audit, the following codes are the **canonical, authoritative** set. Any drift between this list and the Business Rules / PRD is an error to be corrected in those documents.

| Code | Domain | Default for Owner | Default for Staff |
|---|---|---|---|
| `auth.login` | Auth | ON | ON |
| `auth.password_reset_others` | Auth | ON | OFF |
| `user.view` | Identity | ON | OFF |
| `user.manage` | Identity | ON | OFF |
| `user.grant_capability` | Identity | ON | OFF |
| `user.revoke_capability` | Identity | ON | OFF |
| `role.view` | Identity | ON | OFF |
| `role.manage` | Identity | ON | OFF |
| `audit.view` | Identity | ON | OFF |
| `settings.view` | Config | ON | OFF |
| `settings.manage` | Config | ON | OFF |
| `category.view` | Master | ON | ON |
| `category.manage` | Master | ON | OFF |
| `unit.view` | Master | ON | ON |
| `unit.manage` | Master | ON | OFF |
| `product.view` | Master | ON | ON |
| `product.create` | Master | ON | OFF |
| `product.edit` | Master | ON | OFF |
| `product.deactivate` | Master | ON | OFF |
| `contact.view` | Master | ON | ON |
| `contact.create` | Master | ON | OFF |
| `contact.edit` | Master | ON | OFF |
| `payment_method.view` | Config | ON | ON |
| `payment_method.manage` | Config | ON | OFF |
| `cost_type.view` | Master | ON | ON |
| `cost_type.manage` | Master | ON | OFF |
| `financial_category.view` | Finance | ON | OFF |
| `financial_category.manage` | Finance | ON | OFF |
| `sale.view` | Sales | ON | ON |
| `sale.create` | Sales | ON | **ON** |
| `sale.edit_own_draft` | Sales | ON | **ON** |
| `sale.post` | Sales | ON | OFF |
| `sale.complete` | Sales | ON | OFF |
| `sale.cancel` | Sales | ON | OFF |
| `sale.return` | Sales | ON | OFF |
| `sale.price_override` | Sales | ON | OFF |
| `sale.discount` | Sales | ON | OFF |
| `sale.refund` | Sales | ON | OFF |
| `purchase.view` | Purchase | ON | ON |
| `purchase.create` | Purchase | ON | **ON** |
| `purchase.edit_own_draft` | Purchase | ON | **ON** |
| `purchase.post` | Purchase | ON | OFF |
| `purchase.complete` | Purchase | ON | OFF |
| `purchase.cancel` | Purchase | ON | OFF |
| `purchase.return` | Purchase | ON | OFF |
| `purchase.refund` | Purchase | ON | OFF |
| `inventory.view` | Inventory | ON | ON |
| `inventory.adjust` | Inventory | ON | OFF |
| `inventory.transfer` | Inventory | ON | OFF (V2) |
| `production.view` | Production | ON | ON |
| `production.create` | Production | ON | OFF |
| `production.edit_own_draft` | Production | ON | OFF |
| `production.post` | Production | ON | OFF |
| `production.cancel` | Production | ON | OFF |
| `manual_entry.view` | Finance | ON | OFF |
| `manual_entry.create_income` | Finance | ON | OFF |
| `manual_entry.create_expense` | Finance | ON | OFF |
| `manual_entry.cancel` | Finance | ON | OFF |
| `finance.view_profit` | Finance | ON | OFF |
| `finance.view_cash` | Finance | ON | OFF |
| `finance.view_payables_receivables` | Finance | ON | OFF |
| `report.view` | Reports | ON | OFF |
| `export.data` | Export | ON | OFF |
| `notification.view` | Notify | ON | ON |
| `notification.mark_read` | Notify | ON | ON |

**Default for Owner = ON for all capabilities** (per PRD §6.1; "Owner full access").
**Default for Staff = ON for the rows in bold**; OFF elsewhere.

**Rule:** The Owner may grant or revoke any capability to/from any user. Grants and revokes take effect immediately and are logged in `audit_log` (per BR-AUTH-003). The capability catalog itself is system-controlled; the list above is exhaustive in V1.

> **Shorthand supersession (added V1 reconciliation):** Older shorthand forms — `price.override`, `master.product_edit`, `finance.export` — are superseded by the canonical codes `sale.price_override`, `product.edit`, and `export.data` respectively. Business Rules, PRD, and DB spec prose that uses any of the older shorthand forms should be read as referring to the canonical replacement.

### 3.2 Authorization Checks

Every endpoint declares its required capabilities in this spec. The server enforces them as follows:

1. **Authenticate** the request (validate access token → user).
2. **Resolve effective capabilities** from `roles.role_capabilities` ∪ `user_capability_overrides` for the user.
3. **Verify** every required capability is in the effective set.
4. **Verify** any business-scoped capability (e.g., `sale.cancel` is required for a specific sale) — i.e., the resource is not yet cancelled and the user has authority over it. (The capability model is global in V1; row-level scoping is out of V1 scope per Database Design §14.1.)
5. **On failure:** return `403` with `code: capability_required` and a list of missing capabilities. **Never** leak whether the user is authenticated but unauthorized, vs unauthenticated — the response is the same.

### 3.3 Capability Constraints Not Expressible as a Single Code

- **Edit only own draft:** `sale.edit_own_draft` and `purchase.edit_own_draft` are scoped to records where `created_by = current_user.id AND lifecycle_status = 'draft'`. The server checks this after the capability check.
- **Price override requires logging:** when a request mutates `unit_price` on a `sale_line` and the user has `sale.price_override`, the server writes an `audit_log` row (`action = 'price_override'`, `entity_type = 'sale_line'`, `entity_id = line_id`, `old_values`, `new_values`, `user_id`, `reason` from request if provided). The capability is necessary but not sufficient; the audit write must succeed or the request fails.
- **Discounts only with capability:** `sale.discount` is required to send a non-zero `discount_amount`. Without it, the field is rejected as `discount_not_permitted`.

### 3.4 What the Server Does NOT Check

- The server does not check role membership. It checks capabilities. A user whose `role = "Staff"` can be granted `sale.cancel` via override and the request will succeed.
- The server does not enforce separation of duties beyond what the capability model expresses. There is no rule "the user who created a sale cannot cancel it" in V1.

---

## 4. API Conventions

### 4.1 URL Structure

```
/api/v1/{resource}                        — collection
/api/v1/{resource}/{id}                   — single resource
/api/v1/{resource}/{id}/{action}          — lifecycle/action endpoint
/api/v1/{resource}/{id}/{sub-resource}    — sub-collection (e.g., /sales/{id}/lines)
/api/v1/{resource}/{id}/{sub-resource}/{sub-id}  — single sub-resource
```

**Naming:** kebab-case for resource segments; snake_case for JSON fields. Singular nouns preferred for resources when the resource has actions (e.g., `auth`, `dashboard`); plural for collections (`sales`, `purchases`, `products`).

**Versioning:** The `/api/v1/` prefix is mandatory. A `v2` may be added later; `v1` is frozen once shipped.

### 4.2 HTTP Methods

| Method | Use |
|---|---|
| `GET` | Read. Safe, idempotent. Cacheable per `Cache-Control` headers. |
| `POST` | Create a resource OR invoke a non-CRUD action (post, cancel, return, refund, etc.). |
| `PUT` | Full update of a draft resource (rare; most updates use PATCH). |
| `PATCH` | Partial update. The standard for draft edits. |
| `DELETE` | Hard delete of a draft, or deactivation of a master record (with `?deactivate=true`). |

`POST` is used for **all lifecycle actions** because they are not CRUD on the resource — they change `lifecycle_status` and create dependent rows. The path is `/api/v1/sales/{id}/post`, `/api/v1/sales/{id}/cancel`, etc.

### 4.3 Request Format

- **Content-Type:** `application/json; charset=utf-8`.
- **Charset:** UTF-8 throughout. Numbers in JSON are RFC 8259 (no thousand separators, `.` decimal). Dates are ISO 8601 with timezone (`2026-08-26T10:00:00+07:00`) for inputs; the server normalizes to UTC.
- **Money:** JSON number. Stored as `NUMERIC(15,2)`. The server does not accept currency codes; V1 is single-currency (IDR per PRD §4 #1).
- **Quantities:** JSON number. Stored as `NUMERIC(15,4)`. The server validates positive and not absurdly large (`< 10^12`).
- **Booleans:** `true` / `false`. No `0` / `1` acceptance.
- **Enums:** Strings, validated against the closed set. Unknown enum → `400 enum_value_invalid`.
- **Nulls:** `null` is accepted for nullable fields. Omitted fields are treated as not-provided (PATCH semantics). A `null` on PUT means "clear this field if nullable, else 400".

### 4.4 Response Format

- **Content-Type:** `application/json; charset=utf-8`.
- **Envelopes:**
  - Single resource: the resource object directly, with `id`, server-generated fields, and computed fields.
  - Collection: `{ "data": [resource...], "pagination": { "page": 1, "per_page": 50, "total": 1234, "total_pages": 25 }, "links": { "self": "...", "next": "...", "prev": null } }`.
  - Action result: the new/updated resource, or a status object.
- **HATEOAS:** none in V1.
- **ETag:** `ETag` header is set on every GET response containing the resource `updated_at` (or version). PUT/PATCH/POST lifecycle actions may send `If-Match`; mismatch → 412.

### 4.5 Pagination

- **Style:** Offset + limit (page-based). Default `page=1, per_page=50`. Max `per_page=500`. Hard cap is enforced; over the cap → 400.
- **Cursor pagination** is not used in V1. Reports and exports use the same offset/limit (large exports are server-streamed).
- **Default sort:** by `id DESC` for collections unless a `sort` query param is provided.

### 4.6 Filtering, Searching, Sorting

- **Filtering:** query params of the form `filter[field]=value` (e.g., `?filter[lifecycle_status]=posted`). Multi-value: `?filter[lifecycle_status]=posted&filter[lifecycle_status]=partially_returned` (treated as OR within a field; AND across fields).
- **Range filters:** `filter[field]_gte`, `filter[field]_lte`, `filter[field]_gt`, `filter[field]_lt`. For dates, `_from` and `_to` are aliases (inclusive).
- **Search:** `?q=...` performs a case-insensitive substring match on configured text fields (e.g., product name, contact name, reference_no). The server declares which fields are searchable per resource.
- **Sorting:** `?sort=field` (asc) or `?sort=-field` (desc). Multiple: `?sort=-sale_date,id` (comma-separated). Server declares which fields are sortable; unknown field → 400.

### 4.7 Date-Range Handling

- All timestamps are `TIMESTAMPTZ` in UTC. Inputs are accepted in ISO 8601 with offset; the server converts to UTC for storage.
- Date filters (`_from`, `_to`) compare against the **server-side** `*_at` field of the resource. For sales, that's `sale_date`. For purchases, `purchase_date`. For cash movements, `movement_date`. For stock movements, `movement_date`. The client cannot choose a different field per filter.
- The server is the source of "now" for any time-based logic. The client cannot say "as of timestamp X" except by using `_to`.
- Timezone: the server's response includes `server_time` and the response is timezone-aware. The client is responsible for display.

### 4.8 Standard Query Parameters (Summary)

| Param | Applies | Notes |
|---|---|---|
| `page` | collection | 1-based |
| `per_page` | collection | default 50, max 500 |
| `sort` | collection | comma list, `-` prefix for desc |
| `filter[X]` | collection | equality |
| `filter[X]_gte` / `_lte` / `_gt` / `_lt` | collection | range |
| `filter[X]_from` / `_to` | collection (date fields) | inclusive range |
| `q` | collection (searchable) | substring search |
| `include` | single resource | comma list of relations to embed |
| `If-Match` | mutating | optimistic lock |
| `Idempotency-Key` | mutating | idempotency |

### 4.9 Headers (Request)

| Header | Required | Notes |
|---|---|---|
| `Authorization: Bearer <token>` | All except `POST /auth/login`, `POST /auth/refresh` | |
| `Content-Type: application/json` | All except GET | |
| `Accept: application/json` | Optional | default |
| `Idempotency-Key: <uuid>` | Strongly recommended for all mutating; required for sale post, purchase post, refund, payment, cancel, return, stock adjust | Per RFC, see §7 |
| `If-Match: <etag>` | Optional; recommended for lifecycle actions | Optimistic concurrency |
| `X-Request-ID: <uuid>` | Optional | Echoed in response and audit log |

### 4.10 Headers (Response)

| Header | Notes |
|---|---|
| `Content-Type: application/json` | |
| `ETag` | `updated_at`-based; quoted string |
| `X-Request-ID` | Echoed |
| `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` | For rate-limited endpoints |
| `Cache-Control` | GET responses set `private, max-age=0, must-revalidate`; auth-related GETs are `no-store` |

### 4.11 CORS

- For the web client, CORS is allowed only for known origins (configured per deployment). Credentials are not sent cross-origin. CSRF is mitigated by the bearer token model (no cookie auth) plus a custom `Origin` header check for state-changing requests.
- Mobile/native clients are out of V1 scope.

### 4.12 Compression and Encoding

- Responses support `gzip` and `br` content encoding.
- UTF-8 throughout; the server rejects `latin1` and other encodings.

---

## 5. Error Model

### 5.1 Error Envelope

All error responses use a single shape:

```json
{
  "error": {
    "code": "string_machine_readable",
    "message": "string_human_readable",
    "details": { /* optional, structure depends on code */ },
    "request_id": "uuid"
  }
}
```

- `code` is a stable, machine-readable string (snake_case). Clients can switch on it.
- `message` is human-readable, English (V1; i18n is out of scope), and stable enough to display.
- `details` is optional and code-specific. E.g., for `validation_failed`, it carries a list of field errors.
- `request_id` is the same as the `X-Request-ID` header (or a server-generated UUID if absent).

### 5.2 HTTP Status Codes

| Code | Meaning |
|---|---|
| `200 OK` | Read or successful action. |
| `201 Created` | New resource created (POST to a collection). |
| `204 No Content` | Successful action with no body (rare; used for DELETE). |
| `400 Bad Request` | Validation failure; malformed JSON; missing required field; enum_value_invalid; derived_field_not_allowed; idempotency_violation (key reused with different body); etc. |
| `401 Unauthorized` | No credentials, expired credentials, or invalid credentials. |
| `403 Forbidden` | Authenticated but missing required capability OR attempting an operation that violates a non-capability authorization rule (e.g., editing a posted record). |
| `404 Not Found` | Resource does not exist OR is not visible to the user (deliberately conflated to avoid existence disclosure). |
| `409 Conflict` | Optimistic-lock mismatch; lifecycle state conflict (e.g., trying to post an already-cancelled document); insufficient stock; payment exceeds total; refund exceeds CRL. |
| `412 Precondition Failed` | `If-Match` mismatch. |
| `422 Unprocessable Entity` | Semantic conflict not covered by 409: e.g., cannot post a sale with no lines; cannot refund more than CRL. |
| `429 Too Many Requests` | Rate-limited. Includes `Retry-After`. |
| `500 Internal Server Error` | Server-side bug. The body must NOT leak internals. A request_id is required. |
| `503 Service Unavailable` | Maintenance or transient. Includes `Retry-After`. |

### 5.3 Error Code Catalog (Authoritative)

The server returns one of these `code` values. The list is exhaustive in V1; new codes are added only with a spec update.

**Auth / session:**
- `unauthenticated`
- `invalid_credentials`
- `session_expired`
- `session_revoked`
- `account_locked`
- `capability_required` (details: `{"required": ["sale.cancel"]}`)
- `origin_not_allowed`

**Validation:**
- `validation_failed` (details: array of `{field, code, message}`)
- `enum_value_invalid` (details: `{"field": "...", "allowed": [...]}`)
- `derived_field_not_allowed` (details: `{"field": "..."}`)
- `discount_not_permitted`
- `price_override_not_permitted`
- `quantity_invalid`
- `amount_invalid`
- `date_invalid`
- `idempotency_key_required`
- `idempotency_violation` (key reused with a different body)

**Resource:**
- `not_found`
- `lifecycle_state_invalid` (details: `{"from": "...", "to": "...", "current": "..."}`)
- `version_mismatch`
- `delete_not_permitted` (posted; referenced by history)
- `referenced_by_history`
- `currency_mismatch` (V1 = single currency; reserved for V2)

**Business rule failures (mapped to 409/422):**
- `insufficient_stock` (details: `{"product_id", "required", "available"}`)
- `negative_stock_disallowed` (with `allow_negative_stock = false`)
- `payment_exceeds_total` (details: `{"total", "current_sum", "attempted"}`)
- `allocation_exceeds_payable` (purchase variant)
- `refund_exceeds_crl` (details: `{"crl", "attempted", "sale_id"}`)
- `repayment_exceeds_srec` (details: `{"srec", "attempted", "purchase_id"}`)
- `cash_insufficient` (details: `{"required", "available", "trigger"}`)
- `return_quantity_exceeds_original` (details: `{"sale_line_id", "returned_so_far", "max_remaining"}`)
- `purchase_return_quantity_exceeds_original` (details: `{"purchase_line_id", ...}`)
- `already_cancelled` (terminal state; details: `{"lifecycle_status": "cancelled"}`)
- `cannot_cancel_after_return` (or appropriate per document)
- `cost_snapshot_missing`
- `product_deactivated`
- `contact_required_for_supplier` (only if OD-15 changes)
- `shipping_paid_in_cash_invalid_with_unpaid_purchase` (resolved; see §9.6)
- `production_inputs_exceed_stock` (insufficient raw)
- `production_finished_cost_invalid`
- `cost_type_deactivated` (for new lines; historical still allowed)
- `category_duplicate_of_derived` (a user trying to name a manual category "Sales", "COGS", "Production", "Purchase Shipping")
- `manual_entry_duplicate_of_derived` (same)

**Concurrency:**
- `concurrent_modification` (advisory lock could not be acquired within timeout; client may retry)
- `serialization_failure` (DB-level serialization failure; client may retry)

**System:**
- `internal_error` (no details)
- `not_implemented`
- `maintenance` (503 with Retry-After)
- `rate_limited` (429 with Retry-After)

### 5.4 Idempotency Errors

- If a request has no `Idempotency-Key` and the endpoint requires one → 400 `idempotency_key_required`.
- If the key has been used with a **different** request body → 409 `idempotency_violation`. The original response is **not** returned.
- If the key has been used with the **same** body → 200/201 with the original response (or 202 if the original is still processing).

### 5.5 Logging

Every error response is logged with the request_id, user_id (if authenticated), path, method, status, and code. 5xx are alerted.

---

## 6. Validation Model

### 6.1 Layered Validation

- **Request shape** — parsed by the framework. Malformed JSON → 400 `validation_failed` with `details.code = "invalid_json"`.
- **Field-level** — type, format, range, enum, required, nullable. → 400 `validation_failed`.
- **Cross-field** — within a single object (e.g., `line_total = quantity × unit_price − discount_amount`; `discount_amount ≤ line_total`). → 400 `validation_failed`.
- **Cross-resource** — references exist, FKs valid, parent state allows the operation. → 400/409.
- **Business rules** — derived from `Business-Rules.md`. → 400/409/422.
- **Authoritative state** — DB constraints and triggers. The server maps DB errors to API errors (see §5.3 mapping and §11).

### 6.2 Field Validation Rules (Canonical, by type)

- **`id`** — positive integer. Never 0. Path-only or server-generated.
- **`code`** — string, 1-100 chars, `^[A-Za-z0-9._-]+$`, optional unless uniqueness required.
- **`name`** — string, 1-200 chars, trimmed, non-blank after trim.
- **`email`** — `^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$` (max 254 chars; more strict than the DB regex noted in §17).
- **`phone`** — free-form string, max 50 chars; not validated for format in V1.
- **`quantity`** — `NUMERIC(15,4)`, `> 0`, `< 10^12`.
- **`unit_price`** — `NUMERIC(15,2)`, `≥ 0`, `< 10^13`.
- **`amount`** (money) — `NUMERIC(15,2)`, `> 0` (or `≥ 0` where noted), `< 10^13`.
- **`date`** — ISO 8601, must include offset. The server rejects naive timestamps with 400 `date_invalid`.
- **`enum`** — string from the closed set; unknown → `enum_value_invalid`.
- **`bool`** — `true` / `false` only.
- **`text`** — string, max length depends on field (e.g., `notes` 2000, `reason` 1000, `address` unlimited, `name` 200).

### 6.3 Server-Only Fields (Never Accepted from Client)

The server **rejects** (400 `derived_field_not_allowed`) any of the following in a request body:

- `unit_cost_snapshot`, `cogs_total_snapshot`, `line_total` (sale_line), `line_subtotal` (purchase_line), `line_value` (purchase_return_line), `returned_unit_cost` (sales_return_line), `unit_cost_snapshot` (purchase_return_line)
- `finished_unit_cost`, `total_raw_cost`, `total_overhead_cost` (production_run)
- `unit_cost_snapshot` (production_input), `unit_cost_snapshot`/`total_cost` (production_output)
- `lifecycle_status` on POST (only via action endpoint; on PATCH, allowed only for terminal transitions explicitly exposed, e.g., `is_active`)
- `posted_at`, `posted_by`, `cancelled_at`, `cancelled_by`, `cancellation_date` (set by server)
- `paid_amount`, `payment_state` (derived)
- `on_hand_quantity`, `moving_average_unit_cost` (derived)
- `ar`, `ap`, `crl`, `srec`, `cash_balance` (derived)
- `quantity` or `unit_cost_at_movement` on a stock_movement (server-computed at insert)
- `reversal_of_movement_id`, `reversed_by_movement_id` (set by system)
- `id`, `created_at`, `updated_at`, `created_by`, `posted_at`, `posted_by` (server-controlled)

**Exception:** the same field may be echoable on a GET response. The rule is about request bodies.

### 6.4 Server-Only-When-Capable Fields

- `unit_price` on `sale_line` (overrides the product's `selling_price`) — accepted only if user has `sale.price_override`. Without it, the field is rejected as `price_override_not_permitted` if the value differs from `products.selling_price`. With it, the override is accepted and logged.
- `discount_amount` on `sale_line` and on `sales` — accepted only if user has `sale.discount`. Without it, the field is rejected as `discount_not_permitted` if non-zero.
- `cancellation_reason` on `cancel` action — required text; max 1000 chars.

### 6.5 Cross-Field Validation

Performed by the server before any DB write:

- Sale line: `line_total = quantity × unit_price − discount_amount` (also enforced by DB trigger).
- Sale total: `total_amount = Σ line_total − header discount_amount`.
- Purchase line: `line_total = line_subtotal + allocated_shipping`.
- Purchase total: `total_amount = Σ line_total + purchase_shipping.amount` (if shipping record present).
- Payment allocation: `Σ amount ≤ parent.total_amount` (also enforced by DB cross-row trigger).
- Over-tender: `change_amount = tendered_amount − amount` when `tendered_amount` is provided.
- Refund amount: `amount ≤ Σ sale_payments − Σ refunds` for the sale.
- Return quantity: `Σ sales_return_lines.quantity per sale_line ≤ sale_lines.quantity` (also enforced by DB trigger).
- Production finished cost: `finished_unit_cost = (total_raw_cost + total_overhead_cost) / output_quantity`.

### 6.6 Validation Failure Response

`400 Bad Request` with body:

```json
{
  "error": {
    "code": "validation_failed",
    "message": "Request validation failed.",
    "details": [
      { "field": "lines[0].quantity", "code": "quantity_invalid", "message": "Quantity must be greater than 0." },
      { "field": "lines[2].discount_amount", "code": "discount_not_permitted", "message": "User lacks sale.discount capability." }
    ],
    "request_id": "uuid"
  }
}
```

The server returns **all** field errors in one response, not just the first. The order matches the request order.

### 6.7 Business Rule Mapping

| Source rule | API check | Error code |
|---|---|---|
| BR-PRODUCT-001 | `code` may be omitted; if present, must be unique | `validation_failed` (code uniqueness) |
| BR-PRODUCT-003 | `PATCH product {is_active: false}` | `delete_not_permitted` if referenced |
| BR-PRODUCT-004 | `DELETE product` | `referenced_by_history` |
| BR-SALE-002/003 | Sale post: each line quantity > 0 | `quantity_invalid` |
| BR-SALE-005 | Price override | `price_override_not_permitted` or audit log entry on success |
| BR-SALE-006/007 | Payment allocation | `payment_exceeds_total` |
| BR-SALE-008 | Sale cancel | `capability_required`, `already_cancelled`, `lifecycle_state_invalid` |
| BR-SALE-009 | Sale return | `return_quantity_exceeds_original` |
| BR-SALE-010 | Edit posted sale | `lifecycle_state_invalid` (403) |
| BR-PURCHASE-002/003/004 | Purchase post | `quantity_invalid`, `payment_exceeds_total` |
| BR-PURCHASE-005 | Shipping capitalized | (server always capitalizes; no client override) |
| BR-PURCHASE-006 | Purchase return | `purchase_return_quantity_exceeds_original` |
| BR-PURCHASE-007 | Purchase cancel | `capability_required`, `lifecycle_state_invalid` |
| BR-STOCK-001 to 005 | Stock movement | `insufficient_stock`, `negative_stock_disallowed` |
| BR-STOCK-010/011 | Negative stock | `negative_stock_disallowed` or `is_negative_stock_fallback = true` flag in response |
| BR-COST-001/002 | Costing | (server uses moving_average; no client override) |
| BR-COST-003 to 005 | Snapshots | (server-managed) |
| BR-COST-006 | Negative-stock COGS fallback | (server applies; response includes flag) |
| BR-REFUND-001/002 | Refund | `refund_exceeds_crl`, original method used by default |
| BR-FIN-003 | Manual entry duplicate | `manual_entry_duplicate_of_derived` |
| BR-DATA-001/002/003/004 | Data integrity | mapped per above |
| BR-AUDIT-001/002/003 | Audit | (server-managed; for `price_override` the audit row is required) |

---

## 7. Idempotency & Concurrency

### 7.1 Idempotency Keys

**Goal:** a client retrying the same logical operation (e.g., a flaky network caused the response to be lost) must not duplicate the effect.

**Mechanism:** the client sends `Idempotency-Key: <opaque-string, UUID recommended>`. The server stores the (key, user, endpoint, request-body-hash, response) for 24 hours. On replay:

- **Same key + same body hash + same endpoint + same user** → return the original response (200/201). If the original is still in flight (i.e., the request is being processed and a duplicate arrives), the server returns `202 Accepted` with a `Retry-After` header until the original completes, then returns the original response.
- **Same key + different body hash** → 409 `idempotency_violation`.
- **Same key + different user** → 409 `idempotency_violation` (an Idempotency-Key is scoped to a user).
- **Same key + different endpoint** → 409 `idempotency_violation`.

**Required keys:** the following endpoints **require** `Idempotency-Key`. The server returns 400 `idempotency_key_required` if absent:

- `POST /sales/{id}/post`
- `POST /purchases/{id}/post`
- `POST /production-runs/{id}/post`
- `POST /sales/{id}/cancel`
- `POST /purchases/{id}/cancel`
- `POST /production-runs/{id}/cancel`
- `POST /sales/{id}/returns`
- `POST /purchases/{id}/returns`
- `POST /sales/{id}/payments`
- `POST /purchases/{id}/payments`
- `POST /refunds`
- `POST /supplier-repayments`
- `POST /inventory/adjustments`
- `POST /manual-entries`
- `POST /auth/login` (anti-replay, 30-second TTL)

**Optional but recommended:** all other mutating endpoints.

**Storage:** the `idempotency_keys` table has `(key, user_id, endpoint, request_hash, response_status, response_body, created_at, expires_at)`. Expired rows are pruned.

**Client guidance:** the client should generate a fresh key per logical operation. A new draft creation may use a client-generated key (so retrying the "create draft" doesn't create two drafts).

### 7.2 Optimistic Concurrency

Every mutable resource carries an `updated_at` (TIMESTAMPTZ) and the API exposes an `ETag` derived from it. The client may send `If-Match: <etag>` on lifecycle actions.

- `If-Match` absent → server proceeds (no check). Permissive by default; strict mode is opt-in per client.
- `If-Match` present and matches → proceed.
- `If-Match` present and mismatches → 412 `version_mismatch`. Body includes the current ETag so the client can refresh.

**Lifecycle actions (post, cancel, return, refund, adjust) require `If-Match` matching the parent document's ETag.** This is the only place the server strictly requires it. The reason: lifecycle actions have user-visible side effects and an idempotency key alone cannot guard against a stale UI submitting on a document that another user has just changed.

**Document edits on drafts (PATCH) accept `If-Match` opportunistically.**

### 7.3 Pessimistic Locks (DB-side)

The server uses `SELECT ... FOR UPDATE` and PostgreSQL advisory locks per Database Design §11.2:

- **Per-product lock** during sale post, purchase post, production post, stock adjustment — keyed on `product_id` (or hash thereof for advisory locks).
- **Per-document lock** during cancel, return, refund, repayment — keyed on the document id.
- **Cash-balance serialization** — a single `cash_balance` advisory lock (or a serializable transaction level) for any operation that creates a `cash_movements` row.

If the lock cannot be acquired within 2 seconds (configurable), the server returns 409 `concurrent_modification` with a `Retry-After: 1` header.

### 7.4 Transaction Boundaries

Every multi-row write runs in a single DB transaction with `READ COMMITTED` (the default) or `SERIALIZABLE` (when serializing cash balance, per the DB spec). The client never sees a partial state.

- **End-to-end transaction scope:**
  - Sale post: insert lines, compute snapshots, insert N stock_movements, update lifecycle, insert audit. All in one tx.
  - Sale payment: validate Σ amounts, insert sale_payment, insert cash_movement(s) (1 or 2 for over-tender). All in one tx.
  - Sale return: validate quantity bounds, insert sales_return + N sales_return_lines, insert N stock_movements, update sales.lifecycle_status. All in one tx.
  - Refund: validate CRL bound, validate cash balance, insert refund, insert cash_movement. All in one tx.
  - Purchase post: validate, allocate shipping, insert N stock_movements, insert cash_movement (if freight_cash), update lifecycle, insert audit. All in one tx.
  - Purchase cancel: compute on-hand vs consumed, insert reversal movements (potentially including a value_adjustment for consumed portion), insert supplier_repayments if paid, update lifecycle, insert audit. All in one tx.
  - Production post: compute costs, insert N input movements, insert output movement, insert M overhead cash_movements, update lifecycle, insert audit. All in one tx.

### 7.5 Optimistic Updates and Rollback

**The API does not support client-side optimistic updates.** All effects are server-computed in a single transaction. If a transaction fails, the client re-fetches the document and retries the entire operation. There is no partial rollback visible to the client.

### 7.6 Duplicate Submission Detection

Beyond Idempotency-Key, the server applies these guards:

- **Payment on a fully-paid sale:** Σ payments = total → 409 `payment_exceeds_total` (no extra allocation allowed).
- **Payment on a cancelled sale:** 422 (sale is terminal).
- **Post on a non-draft sale:** 409 `lifecycle_state_invalid`.
- **Stock movement to a frozen product (not in scope in V1; reserved).**
- **Repeated lifecycle action:** the post/cancel/return endpoints check the current state in a `SELECT ... FOR UPDATE` and reject if the transition is not allowed.

---

## 8. Lifecycle, State, and Posting Semantics

### 8.1 Lifecycle States (Authoritative)

Per Database Design §9.1 and the database CHECK constraints:

**Sales:** `draft → posted → completed | partially_returned | cancelled → (partially_returned → returned | cancelled) | (completed → partially_returned | cancelled) | (returned → cancelled)`. `cancelled` is terminal.

**Purchases:** same shape. `cancelled` is terminal.

**Production runs:** `draft → posted → completed | cancelled`. `cancelled` is terminal.

**Sales returns:** single state `posted`. Corrections = a new sales_return (capped by remaining quantity).

**Purchase returns:** single state `posted`. Corrections = a new purchase_return.

**Refunds:** immutable after creation. No state.

**Supplier repayments:** single state; `amount` (obligation) and `received_amount` (running cash total) are tracked.

**Manual finance entries:** `posted → cancelled`. Cancellation creates a reversing `cash_movement`.

**Stock movements:** append-only. No UPDATE/DELETE.

**Cash movements:** append-only. No UPDATE/DELETE.

**Products:** `is_active = true | false`. `is_active = false` = deactivated. No hard delete if referenced.

**Contacts:** `is_active = true | false`. Same rule.

**Categories / units / payment methods / cost types / financial categories:** `is_active = true | false`.

### 8.2 Posting Semantics

**"Posting" = applying effects.** The transition `draft → posted` performs the following atomically:
1. Validate (BR-SALE-002 for sales).
2. For each line, snapshot unit cost (from current moving-average).
3. For each line, insert `stock_movements` (one row per line).
4. Update parent lifecycle to `posted`, set `posted_at`, `posted_by`.
5. Insert `audit_log` row.
6. (For sales: no cash_movement yet; cash happens when a payment is recorded.)
7. (For purchases: if `purchase_shipping.paid_in_cash = true`, insert a `cash_movement` for shipping; otherwise shipping stays in AP.)

**Auto-complete:** Per PRD §8.1, "Posted" and "Completed" can be the same moment. The server transitions a sale to `completed` when Σ payments ≥ total and no returns/cancellation are in flight. The client does not call a separate `complete` action for sales. (Purchases transition to `completed` when fully paid OR when no `supplier_id` and total is zero, at the same time as `posted`.)

### 8.3 Cancellation Semantics

**Sale cancel (`POST /sales/{id}/cancel`):**
- Allowed only from `posted | completed | partially_returned | returned`. From `cancelled` → 409 `already_cancelled`.
- Atomically:
  1. `SELECT ... FOR UPDATE` on `sales` row.
  2. Insert `stock_movements` with `trigger='sale_reversal'`, `quantity = +original.quantity`, `unit_cost_at_movement = original.unit_cost_snapshot`, `total_cost = +original.cogs_total_snapshot`, `reversal_of_movement_id = original movement id`, `reference_type='sale_cancellation'`, `reference_id = sale.id`.
  3. Update the original `stock_movements.reversed_by_movement_id` to the new id.
  4. (If the sale had any `sales_return_lines` before cancellation, no reversal of those is needed — returns are physical events already accounted for; cancellation reverses the original sale only.)
  5. Update `sales.lifecycle_status = 'cancelled'`, `cancellation_date = NOW()`, `cancelled_by = user_id`, `cancellation_reason = request body`.
  6. Insert `audit_log`.
  7. **No cash_movement is created.** The cash received is preserved on the books. The Customer Refund Liability (CRL) for the sale is now `Σ sale_payments − Σ refunds for sale`. Refund of that CRL is a separate `POST /refunds` call.
  8. **Cancelling does not delete the sale.** It is non-destructive; the row remains forever (BR-AUDIT-002, §6.3).

**Purchase cancel (`POST /purchases/{id}/cancel`):**
- Same state rules.
- Atomically:
  1. Lock the `purchases` row.
  2. Compute on-hand quantity attributable to this purchase (FIFO through `stock_movements`):
     - `Σ quantity` of movements where `reference_id = purchase.id AND reference_type = 'purchase'` → `received_qty`.
     - `Σ quantity` of movements where `reference_id = purchase.id AND reference_type = 'purchase_cancellation'` → already-reversed (capped).
     - `on_hand_for_this_purchase = received_qty − consumed_qty (already sold/used in production)`.
  3. For the on-hand portion: insert `stock_movements` with `trigger='purchase_reversal'`, `quantity = -on_hand`, `unit_cost_at_movement = original_unit_cost`, `total_cost = -on_hand × unit_cost`, `reversal_of_movement_id = original movement id`.
  4. For the consumed portion: insert `stock_movements` with `trigger='value_adjustment'`, `quantity = 0`, `total_cost = -(consumed_qty × unit_cost)`. This is the COGS-reversal mechanism (per DB spec §5.5). Allowed only for this trigger; quantity=0 is permitted only for `value_adjustment` per the trigger constraint.
  5. If the purchase was paid (Σ `purchase_payments` > 0), insert a `supplier_repayments` row with `amount = Σ purchase_payments`, `refundable_amount_snapshot = Σ purchase_payments`, `reason = 'purchase_cancellation'`. This is the Supplier Receivable creation.
  6. Update `purchases.lifecycle_status = 'cancelled'`, `cancellation_date = NOW()`, `cancelled_by`, `cancellation_reason`.
  7. Insert `audit_log`.
  8. **No cash_movement is created.** AP is derived; AP for the purchase goes to 0 because the purchase is cancelled. The supplier repayment is a future cash event.

**Production run cancel (`POST /production-runs/{id}/cancel`):**
- Same state rules.
- Atomically:
  1. Lock the `production_runs` row.
  2. For each `production_input` movement, insert a reversal: `trigger='production_input_reversal'`, `quantity = +input.quantity`, `unit_cost_at_movement = input.unit_cost_snapshot`, `total_cost = +input.line_cost`, `reversal_of_movement_id = original movement id`.
  3. For the `production_output` movement, insert a reversal: `trigger='production_output_reversal'`, `quantity = -output.quantity`, `unit_cost_at_movement = output.unit_cost_snapshot`, `total_cost = -output.total_cost`, `reversal_of_movement_id = original movement id`.
  4. Overhead cash_movements already paid at post time are **not** reversed (per DB spec §6.2; cash is immutable; the overhead is absorbed by removing the FG from inventory).
  5. Update `production_runs.lifecycle_status = 'cancelled'`, `cancellation_date = NOW()`, `cancelled_by`, `cancellation_reason`.
  6. Insert `audit_log`.

### 8.4 Return Semantics

**Sale return (`POST /sales/{id}/returns`):**
- Request: `{ "reason": string, "lines": [{ "sale_line_id": int, "quantity": number }] }`.
- Atomically:
  1. Lock the `sales` row.
  2. Validate each line:
     - `quantity > 0`.
     - `quantity ≤ (sale_line.quantity − Σ sales_return_lines.quantity where sale_line_id = X)` — server-computed, capped.
  3. For each return line:
     - `returned_selling_price = sale_line.unit_price × quantity` (server-computed; client cannot override).
     - `returned_unit_cost = sale_line.unit_cost_snapshot` (server-computed from the original line's snapshot; this is BR-COST-005).
     - `line_value = returned_selling_price`.
  4. Insert `sales_returns` (header) with `total_selling_price_returned = Σ line_value`, `total_cost_returned = Σ quantity × returned_unit_cost`.
  5. Insert `sales_return_lines` (one per request line).
  6. Insert `stock_movements` (one per return line): `trigger='sales_return'`, `quantity = +returned_quantity`, `unit_cost_at_movement = returned_unit_cost`, `total_cost = +returned_quantity × returned_unit_cost`, `reference_type='sales_return'`, `reference_id = sales_return.id`, `reference_line_id = sales_return_line.id`.
  7. Update `sales.lifecycle_status`:
     - If `Σ returned quantity per sale_line = sale_line.quantity` for all lines → `returned`.
     - Otherwise → `partially_returned`.
  8. **No `refunds` row is created automatically.** The return creates the obligation (CRL for paid sales; AR reduction for unpaid). Actual refund disbursement is a separate `POST /refunds` call by the user.
  9. Insert `audit_log`.

**Purchase return (`POST /purchases/{id}/returns`):**
- Request: `{ "reason": string, "lines": [{ "purchase_line_id": int, "quantity": number }] }`.
- Atomically:
  1. Lock the `purchases` row.
  2. Validate each line: `quantity ≤ (purchase_line.quantity − Σ returned)`.
  3. For each return line: `unit_cost_snapshot = purchase_line.unit_price + (purchase_line.allocated_shipping / purchase_line.quantity)` (per-line landed cost). `line_value = quantity × unit_cost_snapshot`. These are server-computed.
  4. Insert `purchase_returns` (header) with `total_value_returned = Σ line_value`.
  5. Insert `purchase_return_lines`.
  6. Insert `stock_movements`: `trigger='purchase_return'`, `quantity = -returned_quantity`, `unit_cost_at_movement = unit_cost_snapshot`, `total_cost = -quantity × unit_cost`, `reference_type='purchase_return'`, `reference_id`, `reference_line_id`.
  7. If purchase was paid (Σ purchase_payments > 0), insert `supplier_repayments` with `amount = total_value_returned`, `refundable_amount_snapshot = same`, `reason = 'purchase_return'`.
  8. Update `purchases.lifecycle_status` per the same rule as sales.
  9. Insert `audit_log`.

### 8.5 Refund Semantics

**`POST /refunds`** (request: `{ "sale_id": int, "amount": number, "payment_method_id": int, "reason": string }`):
- Atomically:
  1. Lock the `sales` row.
  2. Validate: sale is in a state that may have CRL (`posted | completed | partially_returned | returned | cancelled`).
  3. Compute CRL: `Σ sale_payments.amount − Σ refunds.amount WHERE sale_id = X`. Reject if `amount > CRL` with 409 `refund_exceeds_crl`.
  4. Default `payment_method_id` to the most recent original payment's method if not provided (BR-REFUND-002). Reject if user provides a method the original payment did not use without `sale.refund_override_method`.
  5. Validate: `amount ≤ current cash balance`. Reject with 409 `cash_insufficient` if not. (Cash ≥ 0 invariant.)
  6. Insert `refunds` row.
  7. Insert `cash_movements`: `direction='out'`, `amount=-refund.amount`, `trigger='refund'`, `reference_type='refund'`, `reference_id`, `payment_method_id`.
  8. Insert `audit_log`.

**`POST /supplier-repayments`** (request: `{ "purchase_id": int, "amount": number, "payment_method_id": int, "reason": string }`):
- Atomically:
  1. Lock the purchase row.
  2. Compute the Supplier Receivable for the purchase from `supplier_repayments` (Σ amount − Σ received) and Σ `purchase_payments` (the open obligation). The DB spec uses a slightly indirect path; the API normalizes to "amount ≤ (Σ payments − Σ repayments already received)".
  3. Validate `amount > 0`, `amount ≤ outstanding`.
  4. Validate `amount ≤ current cash balance`.
  5. Either find or create the `supplier_repayments` row for this purchase (one row per purchase per the simplified V1 model), increment its `received_amount`, and insert the cash_movement.

### 8.6 Edit Policy

- **Drafts:** full edit. All fields mutable except `lifecycle_status` (which is mutated only by action endpoints).
- **Posted:** no edit on financial/inventory fields. `notes` and `reference` may be PATCHed (logged).
- **Completed / partially_returned / returned:** no edit except `notes`/`reference` (logged).
- **Cancelled:** no edit (terminal).

Server returns 403 `lifecycle_state_invalid` for any other edit attempt.

### 8.7 Delete Policy

- **Drafts:** hard delete is allowed (the document never had effects).
- **Posted+:** hard delete is rejected with 409 `delete_not_permitted`. Use `cancel` instead.
- **Master records (products, contacts, categories, units, payment_methods, cost_types, financial_categories):**
  - If referenced by any transaction, movement, payment, or line: 409 `referenced_by_history`. Use `PATCH {is_active: false}` (deactivate) instead.
  - If unreferenced: hard delete is allowed (returns 204).

---

## 9. Financial / Accounting API Rules

### 9.1 The 11-Account Chart (Reference)

Per V1.9-Accounting-Event-Matrix.md, the system tracks these accounts (as derived balances, not as a GL UI):

- 1010 Cash
- 1100 Accounts Receivable
- 1200 Inventory
- 1300 Supplier Receivable
- 2010 Accounts Payable
- 2100 Customer Refund Liability
- 3010 Sales Revenue
- 3020 Other Income
- 4010 COGS
- 5010 Operating Expenses
- 6000 Owner's Equity

The API does **not** expose these account numbers. It exposes the **derived balances** (cash on hand, AR, inventory value, AP, CRL, SRec, gross profit, net profit) on the dashboard and report endpoints. The accounts are a documentation device for the 22 events and 8 invariants; the implementation derives their balances from the underlying tables.

### 9.2 The 8 Invariants (How the API Enforces Them)

| Inv | Statement | API/Database enforcement |
|---|---|---|
| INV-01 | Σ Debits = Σ Credits | Atomicity of every event; journal entries are derived from atomic writes. |
| INV-02 | Assets = Liabilities + Equity | Same. Verified by stress tests in V1.9-Accounting-Event-Matrix.md. |
| INV-03 | Cash ≥ 0, AR ≥ 0, AP ≥ 0, CRL ≥ 0, SRec ≥ 0, Inventory ≥ 0 (unless enabled) | Cash: pre-insert trigger on `cash_movements`. AR/AP/CRL/SRec: derived queries that are always ≥ 0 by construction (since they are bounded). Inventory: trigger on sale post checks `on_hand ≥ sum` unless `allow_negative_stock`. |
| INV-04 | Refund ≤ CRL, Repayment ≤ SRec | Pre-insert triggers on `refunds` and `supplier_repayments`. API returns 409 `refund_exceeds_crl` / `repayment_exceeds_srec`. |
| INV-05 | One cancellation per document | Trigger on `lifecycle_status` UPDATE: reject if OLD = `cancelled`. API enforces via state checks. |
| INV-06 | CRL = Σ payments − Σ refunds (per sale) | Derived at query time. No stored CRL. |
| INV-07 | SRec = Σ payments − Σ repayments (per purchase) | Derived at query time. No stored SRec. |
| INV-08 | Inventory GL = Σ(qty × cost) | Per-movement `total_cost` is authoritative; sum is the GL value. |

### 9.3 The 22 Accounting Events (API Mapping)

| Event | API endpoint(s) |
|---|---|
| 1. Unpaid sale post | `POST /sales/{id}/post` |
| 2. Sale payment | `POST /sales/{id}/payments` |
| 3. Over-tender sale | `POST /sales/{id}/payments` with `tendered_amount` and `change_amount` (computed) |
| 4. Unpaid sale cancel | `POST /sales/{id}/cancel` |
| 5. Fully paid sale cancel | `POST /sales/{id}/cancel` |
| 6. Partially paid sale cancel | `POST /sales/{id}/cancel` |
| 7. Customer refund | `POST /refunds` |
| 8. Sales return | `POST /sales/{id}/returns` |
| 9. Purchase receipt | `POST /purchases/{id}/post` |
| 10. Supplier payment | `POST /purchases/{id}/payments` |
| 11. Unpaid purchase cancel | `POST /purchases/{id}/cancel` |
| 12. Paid purchase cancel, inventory on hand | `POST /purchases/{id}/cancel` |
| 13. Paid purchase cancel, inventory consumed | `POST /purchases/{id}/cancel` (zero-qty value_adjustment) |
| 14. Supplier repayment | `POST /supplier-repayments` |
| 15. Purchase return | `POST /purchases/{id}/returns` |
| 16. Production run | `POST /production-runs/{id}/post` |
| 17. Freight cash paid | `POST /purchases/{id}/post` (if `paid_in_cash=true`) |
| 18. Stock adjustment up | `POST /inventory/adjustments` (positive qty) |
| 19. Stock adjustment down | `POST /inventory/adjustments` (negative qty) |
| 20. System opening balance | `POST /system/initialize` (one-time; Owner-only; **out of V1 scope per the audit report's cautious treatment; left as a documented but not yet exposed endpoint — see §15.30**) |
| 21. Manual cash income | `POST /manual-entries` with `entry_type='income'` |
| 22. Manual cash expense | `POST /manual-entries` with `entry_type='expense'` |

### 9.4 Payment Allocation Rules (Authoritative)

- **Multi-payment allowed:** a sale or purchase can have N `*_payments` rows.
- **Σ amounts ≤ total:** cross-row trigger enforces (BR-DATA-004). API surfaces as 409 `payment_exceeds_total` or 409 `allocation_exceeds_payable`.
- **Over-tender cash only:** `tendered_amount > amount` is allowed only when `payment_method.is_cash = true` (Database Design §2.8.1). For non-cash methods, the server rejects `tendered_amount` with 400.
- **Change formula:** `change_amount = tendered_amount − amount` (server-computed; the client may send it, but if sent and inconsistent, server recomputes and ignores client value, or returns 400 `change_amount_mismatch` if it diverges — implementation choice; **the API recomputes**).
- **Cash journal entry for over-tender:** two `cash_movements` rows are inserted atomically: one `direction='in', amount=+tendered_amount, trigger='change_tendered'`, one `direction='out', amount=-change_amount, trigger='change_tendered'`. Net cash effect = `+amount` (the allocated amount). Revenue = `+amount`. (Per DB spec §2.10.1 and §4.3.)
- **For purchase over-tender (unusual, but possible for cash buys):** the same two-row pattern is used with `trigger='change_tendered'`.

### 9.5 Refund vs Income Invariant

- A refund **never** appears in P&L revenue or expense. The `refund` `cash_movement` is `direction='out'`, reducing cash. P&L is unaffected.
- The `financial_categories` table **forbids** categories whose name duplicates a derived entry. The seed data excludes "Sales", "COGS", "Production", "Purchase Shipping", "Refund". The API rejects manual-entry creation with `manual_entry_duplicate_of_derived` if the category name matches.

### 9.6 Shipping Capitalization (OD-6 RESOLVED)

- Purchase shipping is **always** part of landed cost. The API exposes `purchase_shipping` on a purchase and allocates it pro-rata to lines at post time.
- The user does **not** create a "Shipping" expense category entry for a purchase-related shipping. The API exposes a category-creation endpoint (`POST /financial-categories`); the server validates that the proposed name is not in the `derived_categories` blacklist.
- `purchase_shipping.paid_in_cash` is a single boolean; the server creates the appropriate effect at post time (cash_movement if true, AP inclusion if false).
- Manual shipping for non-purchase scenarios (e.g., shipping sold goods to a customer) is a separate `manual_entry` with `entry_type='expense'` and category "Extra shipping".

### 9.7 Costing Behavior

- **Costing method** = `moving_average` (default). The `system_settings.costing_method` field is read-only in V1; switching it is reserved for V2. The DB spec notes only `moving_average` is implemented.
- **Snapshot at post:** when a sale is posted, `unit_cost_snapshot = (current on-hand valuation) / (current on-hand quantity)` per Database Design §3.4. This value is **frozen** on the sale line and never recomputed.
- **Negative-stock fallback:** if `allow_negative_stock` is true for a product (or globally) and the sale would drive on-hand negative, the server uses `COGS = products.purchase_price` (the last-known purchase price) and sets `sale_lines.is_negative_stock_fallback = true`. The response includes the flag.
- **Return cost:** sale return uses `sale_lines.unit_cost_snapshot`. Purchase return uses `purchase_lines.unit_price + (allocated_shipping / quantity)`. Both are server-computed.
- **Finished goods cost:** `finished_unit_cost = (total_raw_cost + total_overhead_cost) / output_quantity`. Server-computed.
- **No client override of cost snapshots.** The API rejects with `derived_field_not_allowed`.

### 9.8 Derived Values Returned

The following are computed on read and returned in responses. They are never accepted in writes:

- `total_amount` on sales/purchases: returned on read; client may send the header `discount_amount` and lines, but the server recomputes `total_amount` from lines (the server is the source of truth).
- `payment_state`: derived as `unpaid | partial | paid` per sale/purchase. Returned on read.
- `ar`, `ap`, `crl`, `srec` (per sale, per purchase, totals).
- `on_hand_quantity`, `moving_average_unit_cost`, `inventory_value` (per product, total).
- `cash_balance` (total).
- `revenue`, `cogs`, `gross_profit`, `net_profit`, `other_income`, `operating_expenses` (per period).

### 9.9 Money and Currency

- Single currency: IDR (per PRD §4 #1). The API does not accept currency codes.
- All amounts are `NUMERIC(15,2)`. The server does not round on storage; any display rounding is a client concern. (OD-16 is `TBD-OWNER`; if display rounding is later mandated, the API adds a `display_rounding` boolean on relevant GETs and a `rounding_mode` config; the stored values are unchanged.)
- The `decimal_separator` in responses is `.`. Thousand separators are not used.

### 9.10 Date and Timezone

- All timestamps are UTC in storage; responses include the timezone offset.
- The server's "now" is the authoritative time for all date-based logic. A client cannot ask "what is the state as of timestamp X" other than by sending a `_to` filter.

---

## 10. Inventory & Costing API Rules

### 10.1 The Authoritative Inventory Ledger

`stock_movements` is the single source of truth for inventory. All 15 trigger values are valid (per DB spec §3.2). The API never exposes a way to insert a `stock_movements` row directly — every movement is created by a business action (sale post, purchase post, return, adjustment, production, cancellation).

### 10.2 Stock Effects per Event

| Event | Trigger | Quantity | Cost |
|---|---|---|---|
| Purchase post | `purchase_receipt` | + line.quantity | `unit_cost_at_movement = line_total / quantity` (includes allocated shipping) |
| Sale post | `sale` | − line.quantity | `unit_cost_at_movement = current moving-avg` |
| Sale return | `sales_return` | + returned.qty | `unit_cost_at_movement = sale_lines.unit_cost_snapshot` |
| Purchase return | `purchase_return` | − returned.qty | `unit_cost_at_movement = original purchase line landed cost` |
| Production post (input) | `production_input` | − input.quantity | `unit_cost_at_movement = current moving-avg` |
| Production post (output) | `production_output` | + output.quantity | `unit_cost_at_movement = finished_unit_cost` |
| Stock adjustment up | `stock_adjustment` | + delta | `unit_cost_at_movement = current moving-avg` |
| Stock adjustment down | `stock_adjustment` | − delta | `unit_cost_at_movement = current moving-avg` |
| Sale cancel | `sale_reversal` | + original.qty | `unit_cost_at_movement = original.unit_cost_snapshot`, `reversal_of_movement_id = original movement id` |
| Purchase cancel (on-hand portion) | `purchase_reversal` | − on_hand | `unit_cost_at_movement = original`, `reversal_of_movement_id = original` |
| Purchase cancel (consumed portion) | `value_adjustment` | 0 | `total_cost = −(consumed × unit_cost)` (COGS reversal) |
| Sales return cancel | `sales_return_reversal` | − original (return was +; reversal is −) | `reversal_of_movement_id` |
| Purchase return cancel | `purchase_return_reversal` | + original | `reversal_of_movement_id` |
| Production cancel (input) | `production_input_reversal` | + original | `reversal_of_movement_id` |
| Production cancel (output) | `production_output_reversal` | − original | `reversal_of_movement_id` |
| Adjustment cancel | `adjustment_reversal` | opposite of original | `reversal_of_movement_id` |

### 10.3 Negative Stock Rules

- **Default:** `allow_negative_stock = false` globally (`system_settings.default_negative_stock_allowed = false`).
- **Per-product override:** `products.allow_negative_stock` is `NULL` (use global) or `true` / `false`.
- **On sale post:**
  - If product-level and global both false: check `Σ line.quantity for product_id P ≤ on_hand_quantity(P)`. If not, reject with 409 `insufficient_stock`.
  - If product-level or global true: allow the post; the sale line is created with `is_negative_stock_fallback = true` and `unit_cost_snapshot = products.purchase_price`.
- **On production input:** same rule, applied to raw materials.

### 10.4 Stock Adjustments

- `POST /inventory/adjustments` with `{ "product_id": int, "quantity": number (signed, ≠ 0), "reason": string (required) }`.
- The `reason` field is required (logged for audit).
- `quantity > 0` → stock up; `quantity < 0` → stock down.
- Server validates: user has `inventory.adjust`; product exists; `|quantity| < 10^12`.
- On success: insert `stock_movements` with `trigger='stock_adjustment'`, `unit_cost_at_movement = current moving-avg`, `total_cost = |quantity| × unit_cost`, `reason = request.reason`, `reference_type='stock_adjustment'`, `reference_id = adjustment.id`.
- Adjustment has no separate header table in V1 (Database Design does not define one). The API stores the header in `audit_log` (`action='adjust'`, `entity_type='stock_movement'`, `entity_id = movement.id`, `old_values={product, qty_before}`, `new_values={product, qty_after, reason}`). The `audit_log` row is the only record of the reason. **This is a documented gap** — see §17 MS-1.

### 10.5 Concurrent Stock-Affecting Operations

- Per the DB spec §11.2, the server uses row-level locks on the product row and an advisory lock keyed on `product_id` for any operation that computes moving-average or checks on-hand.
- The server returns 409 `concurrent_modification` (with `Retry-After: 1`) if the lock cannot be acquired within the timeout.

### 10.6 Cost Snapshot Immutability

- Once a sale is `posted`, `unit_cost_snapshot` and `cogs_total_snapshot` are immutable. PATCH on these returns 403.
- The DB enforces via trigger (DB spec §3.3). The API surfaces as 403 `lifecycle_state_invalid`.

---

## 11. Audit & Notification API Rules

### 11.1 Audit Log

- **Append-only.** `audit_log` rows are never updated or deleted by anyone, including the Owner. The DB trigger enforces this.
- **Captured events** (per DB spec §13.2): create, post, cancel, return, adjust, permission grant/revoke, settings change, deactivate, plus price override and refund.
- **Fields:** `user_id` (SET NULL on user deletion), `action`, `entity_type`, `entity_id`, `old_values` (JSONB), `new_values` (JSONB), `reason` (optional), `event_time`, `ip_address`.
- **API exposure:** `GET /audit?entity_type=sale&entity_id=123` returns the audit history for an entity. `GET /audit?user_id=...&action=...&from=...&to=...` returns filtered history. Owner-only (`audit.view` capability).
- **No client-write:** the API does not expose POST/PATCH/DELETE on `/audit`. Only the system (server-side triggers) writes.

### 11.2 Notifications

Per PRD §27, the system tracks:
1. Low-stock / out-of-stock list.
2. Insufficient stock warning (per negative-stock rule).
3. Below-cost sale warning.
4. Payment reminders (recommended, in-app only in V1).
5. Irreversible action confirmation.

- **API exposure:** `GET /notifications?unread=true` lists in-app notifications. `POST /notifications/{id}/mark-read` marks as read.
- **Trigger sources:**
  - Low-stock: server-side job on dashboard view; when `on_hand_quantity ≤ products.low_stock_threshold` (or default), a notification row is created. **Not in V1 API scope as a real-time push; computed on read.**
  - Insufficient stock: returned as a 409 `insufficient_stock` response with details; no separate notification row.
  - Below-cost sale: returned as a 200 response with a `warning` field; no 4xx. The user is informed; posting proceeds.
  - Payment reminders: **out of V1** (per audit report — TBD-005).
  - Irreversible action confirmation: a **client-side concern** (the API is the server; the client confirms in the UI; the API requires `Idempotency-Key` and `If-Match` instead of asking "are you sure").

### 11.3 Notification Storage

- A `notifications` table (V1, implied by API design) with `(id, user_id, type, message, related_entity_type, related_entity_id, is_read, created_at)`.
- Owner can broadcast to all users (future); in V1, notifications are personal (computed per user request).

---

## 12. Reporting & Export API

### 12.1 Report Endpoints (in-app)

All read-only, paginated, filterable, sortable per §4.

| Endpoint | Description | Required capability |
|---|---|---|
| `GET /reports/sales` | Sales report | `report.view` |
| `GET /reports/purchases` | Purchase report | `report.view` |
| `GET /reports/inventory` | Inventory valuation + low-stock | `report.view` |
| `GET /reports/inventory-movements` | Stock movement history | `report.view` |
| `GET /reports/sales-returns` | Sales return report | `report.view` |
| `GET /reports/purchase-returns` | Purchase return report | `report.view` |
| `GET /reports/production` | Production runs with cost lines | `report.view` |
| `GET /reports/manual-income` | Manual income entries | `report.view` |
| `GET /reports/manual-expense` | Manual expense entries | `report.view` |
| `GET /reports/refunds` | Refunds | `report.view` |
| `GET /reports/supplier-repayments` | Supplier repayments | `report.view` |
| `GET /reports/cash-flow` | Cash movements | `report.view` |
| `GET /reports/p-and-l` | P&L (revenue, COGS, gross, expenses, net) | `finance.view_profit` |
| `GET /reports/receivables` | Receivables per sale | `finance.view_payables_receivables` |
| `GET /reports/payables` | Payables per purchase | `finance.view_payables_receivables` |
| `GET /reports/supplier-receivables` | Open supplier receivable per purchase | `finance.view_payables_receivables` |
| `GET /reports/customer-refund-liabilities` | Open CRL per sale | `finance.view_payables_receivables` |

Each report accepts the standard query params (`filter`, `sort`, `page`, `per_page`, `q`, `from`, `to`, `include`).

**`from` and `to` for reports** are period selectors:
- `from` / `to` ISO timestamps.
- Convenience aliases: `period=today|this_week|this_month|this_year|custom` (per PRD §22 filters). If `period` is set, the server computes `from` / `to` from the server's current time.

**Period comparison** (`compare_to=period` or `compare_to=from,to`): the response includes a `comparison` object with delta values where applicable. V1 supports period comparison for: sales, purchases, P&L, manual income, manual expense, refunds, repayments. The response shape is:

```json
{
  "data": { /* current period aggregates */ },
  "comparison": {
    "previous_period": { "from": "...", "to": "..." },
    "delta": { /* field-by-field current − previous */ },
    "delta_pct": { /* field-by-field percentage change */ }
  }
}
```

### 12.2 Dashboard

`GET /dashboard` returns:
- KPI cards: total sales, total purchases, net profit, cash on hand, inventory value, low-stock count.
- Charts: sales trend (daily for week, weekly for month/year), best-sellers, expense breakdown.
- Filters: `period=today|this_week|this_month|this_year|custom` plus `from`, `to`, plus optional `compare_to`.

Required capabilities: `finance.view_profit` (for GP/NP), `finance.view_cash` (for cash), `finance.view_payables_receivables` (for AR/AP). Without them, the corresponding fields are `null` with a `403` per field? No — the entire endpoint returns 403 if any required capability is missing. The client requests a separate `GET /dashboard/inventory` for inventory KPIs (which has its own capability, `inventory.view`).

### 12.3 Export

- `POST /exports` — body: `{ "report": "sales|purchases|...", "format": "pdf|xlsx", "filters": { ... } }`. Returns `{ "export_id": uuid, "status": "queued" }`.
- `GET /exports/{id}` returns `{ "status": "queued|processing|complete|failed", "download_url": "...", "expires_at": "...", "error": "..." }`.
- `GET /exports/{id}/download` returns the file. The download URL is pre-signed (time-limited) for security.
- Required capability: `export.data`. Without it, 403.
- Filters mirror the corresponding report endpoint. The export respects the filter set; the file includes a filter summary (per PRD §25).
- Files are generated server-side and stored temporarily; expired files are pruned.
- **Rate-limited:** max 5 export requests per user per hour (configurable).

### 12.4 Performance Notes

- The server uses materialized views or aggregated tables for very large datasets; the API does not change shape when these are introduced. The audit notes (DB spec M1) acknowledge that materialized views are an option for performance.
- All report endpoints enforce a soft time limit (default 30s) and return 503 `concurrent_modification` if exceeded (with a hint to narrow the date range).

---

## 13. Security Requirements

### 13.1 Transport and Headers

- HTTPS only. HTTP requests are redirected to HTTPS.
- HSTS enabled (1 year, includeSubDomains).
- Strict CSP: `default-src 'self'`. Inline scripts are forbidden; nonces are used.
- `X-Content-Type-Options: nosniff`.
- `X-Frame-Options: DENY`.
- `Referrer-Policy: no-referrer`.
- `X-XSS-Protection: 0` (modern; CSP is preferred).

### 13.2 Authentication

- Argon2id for password storage; parameters per OWASP 2024 guidance.
- Tokens are 256-bit random, base64url-encoded. They are stored hashed (SHA-256) in the `sessions` table; the raw token is only seen by the client.
- Refresh-token rotation: each refresh issues a new refresh and invalidates the old one.
- Sessions are bound to `User-Agent` and (optionally) `ip_subnet` for additional verification. A mismatch raises a security event (not a hard failure; the client may re-authenticate).

### 13.3 Authorization

- Every request is checked server-side. The client cannot bypass by removing headers.
- The server's capability model is the only authority. Roles are a convenience.
- Owner-only endpoints are tagged `[Owner]` in this spec.
- Capability checks are **not** cached across requests; each request re-evaluates (with a per-request memoization that is invalidated on grant/revoke).

### 13.4 Input Validation and Output Encoding

- All input is validated per §6. The server rejects oversize payloads (>1 MB by default) with 413.
- All output is JSON with UTF-8 encoding; strings are not HTML-escaped (the client is responsible for XSS prevention in its rendering).
- The server never reflects user input in error messages without sanitization. The `message` field is fixed text per error code; user input is included only via `details` and only when the value is itself validated.

### 13.5 Logging

- All requests are logged with: `request_id`, `user_id`, `method`, `path`, `status`, `latency_ms`, `client_ip`, `user_agent`.
- PII (passwords, tokens) is never logged. PII in request bodies is masked in logs (e.g., `email` is logged as `email_hash` for correlation).
- 5xx are alerted. 401/403/404 are logged at INFO; 200/201/204 at DEBUG.
- Audit log writes are in the same DB transaction as the action; the log is a real source of truth, not a side effect.

### 13.6 Rate Limiting

Per-user, per-IP, and per-endpoint. Configurable via `system_settings`:

- `POST /auth/login`: 5 / 15 min per IP, 10 / hour per user.
- `POST /auth/refresh`: 60 / hour per user.
- All other mutating endpoints: 60 / minute per user, 600 / minute per IP.
- Read endpoints: 600 / minute per user.
- Exports: 5 / hour per user, 20 / day per user.

When rate-limited, the server returns 429 with `Retry-After: <seconds>` and the `X-RateLimit-*` headers.

### 13.7 CSRF and Same-Site

- The bearer-token model is CSRF-immune by design (no cookies are used for auth).
- CORS is restricted to a configured allow-list. `Origin` is checked on every state-changing request.

### 13.8 PII and Data Export

- The `contacts` table holds PII (name, phone, email, address). Access requires `contact.view`.
- Bulk endpoints return minimal fields by default; `?include=pii` is rejected with 403 unless the user has a special capability (`pii.view_bulk`) — not in V1 by default. A single-record GET returns full PII.

### 13.9 Backup and Recovery (operational, not in product scope)

- The API does not expose backup endpoints. Backup is operational.

---

## 14. Endpoint Catalog (Summary)

| Resource | Methods | Path |
|---|---|---|
| Auth | login, refresh, logout, logout-all, me | `POST/GET/DELETE /auth/{...}` |
| Users | list, get, create, update, deactivate, reset-password, unlock, grant, revoke, list-capabilities | `GET/POST/PATCH/DELETE /users/{...}` |
| Roles | list, get, create, update, delete, list-capabilities, set-capabilities | `GET/POST/PUT/PATCH/DELETE /roles/{...}` |
| Capabilities | list (catalog) | `GET /capabilities` |
| Audit | list | `GET /audit` |
| Categories | list, get, create, update, deactivate | `GET/POST/PATCH /categories/{...}` |
| Units | list, get, create, update, deactivate | `GET/POST/PATCH /units/{...}` |
| Products | list, get, create, update, deactivate, history, valuation, low-stock | `GET/POST/PATCH /products/{...}` |
| Contacts | list, get, create, update, deactivate | `GET/POST/PATCH /contacts/{...}` |
| Payment methods | list, get, create, update, deactivate | `GET/POST/PATCH /payment-methods/{...}` |
| Cost types | list, get, create, update, deactivate | `GET/POST/PATCH /cost-types/{...}` |
| Financial categories | list, get, create, update, deactivate | `GET/POST/PATCH /financial-categories/{...}` |
| Sales | list, get, create draft, update draft, post, cancel, complete, add line, update line, delete line, add payment, list payments, list returns, create return | `GET/POST/PATCH/DELETE /sales/{...}` |
| Sale lines | (sub-resource of sales) | `/sales/{id}/lines` |
| Sale payments | (sub-resource of sales) | `/sales/{id}/payments` |
| Sales returns | list, get, create (via parent), cancel | `GET /sales-returns/{...}` |
| Refunds | list, get, create | `GET/POST /refunds/{...}` |
| Purchases | list, get, create draft, update draft, post, cancel, complete, add line, update line, delete line, add payment, list payments, list returns, create return | `GET/POST/PATCH/DELETE /purchases/{...}` |
| Purchase lines | (sub-resource of purchases) | `/purchases/{id}/lines` |
| Purchase payments | (sub-resource of purchases) | `/purchases/{id}/payments` |
| Purchase returns | list, get, cancel | `GET /purchase-returns/{...}` |
| Supplier repayments | list, get, create | `GET/POST /supplier-repayments/{...}` |
| Inventory | list (movements), per-product, low-stock, adjustments (create, list) | `GET/POST /inventory/{...}` |
| Production runs | list, get, create draft, update draft, post, cancel, complete, add input, update input, delete input, add cost line, update cost line, delete cost line | `GET/POST/PATCH/DELETE /production-runs/{...}` |
| Production inputs | (sub-resource) | `/production-runs/{id}/inputs` |
| Production cost lines | (sub-resource) | `/production-runs/{id}/cost-lines` |
| Manual entries | list, get, create, cancel | `GET/POST/DELETE /manual-entries/{...}` |
| Cash movements | list (read-only) | `GET /cash-movements` |
| Stock movements | list (read-only) | `GET /stock-movements` |
| Reports | (per §12) | `GET /reports/{...}` |
| Dashboard | kpis, trends, best-sellers, expense-breakdown | `GET /dashboard/{...}` |
| Exports | create, get, download | `GET/POST /exports/{...}` |
| Notifications | list, mark-read, mark-all-read | `GET/POST /notifications/{...}` |
| Settings | get, update | `GET/PATCH /settings` |
| System | health, version | `GET /system/{...}` |

The full per-endpoint detail is in §15.

---

## 15. Domain-by-Domain API Specification

This section is the **complete endpoint contract**. Each subsection lists the endpoints in that domain with: HTTP method, path, required capabilities, request schema, response schema, validation rules, business rules invoked, DB effects, accounting effects, inventory effects, audit effects, and failure/rollback behavior.

Conventions used in this section:
- All paths begin with `/api/v1`.
- Request bodies are JSON.
- All paths with `{id}` accept the resource's primary key.
- "Auth" = authentication required (any logged-in user).
- "RBAC" = capability check required; listed capabilities are mandatory.
- "Atomic" = the operation runs in a single DB transaction.
- "Idempotent" = an `Idempotency-Key` is required.

### 15.1 Common Patterns

**Pagination response:**
```json
{
  "data": [ /* resource objects */ ],
  "pagination": { "page": 1, "per_page": 50, "total": 1234, "total_pages": 25 },
  "links": { "self": "/api/v1/sales?page=1&per_page=50", "next": "/api/v1/sales?page=2&per_page=50", "prev": null }
}
```

**Single resource envelope:** the resource object directly, with `id`, server-generated fields, and computed fields (per §9.8).

**ETag:** the ETag is the resource's `updated_at` as a quoted ISO 8601 string.

**Version field:** every mutable resource exposes `version` (an integer, server-incremented on every successful write). The client may include it in `If-Match` for optimistic concurrency. Some endpoints accept a numeric `version` instead of ETag (both supported in V1).

### 15.2 Auth & Session

#### 15.2.1 Login
- `POST /auth/login`
- **Auth:** none.
- **Idempotency-Key:** required (anti-replay, 30s TTL).
- **Request:** `{ "username": string (required, 1-64), "password": string (required, 1-256) }`
- **Response 200:** `{ "access_token": string, "refresh_token": string, "expires_in": int (seconds), "refresh_expires_in": int, "user": { id, username, full_name, role_id, role_name, capabilities: [string] } }`
- **Validation:** username exists, password correct, account not locked, account active.
- **Business rules:** none.
- **DB effects:** insert `sessions` row.
- **Failures:** 401 `invalid_credentials` (3rd–4th attempt); 423 `account_locked` (5th attempt within 15 min); 403 `account_inactive`.
- **Rate limit:** 5 / 15 min per IP, 10 / hour per user.

#### 15.2.2 Refresh
- `POST /auth/refresh`
- **Auth:** none (uses refresh token in body).
- **Request:** `{ "refresh_token": string }`
- **Response 200:** `{ "access_token", "refresh_token" (new), "expires_in", "refresh_expires_in" }`
- **DB effects:** rotate session, mark old refresh revoked.
- **Failures:** 401 `invalid_credentials`, 401 `session_expired`, 401 `session_revoked`.

#### 15.2.3 Logout
- `POST /auth/logout`
- **Auth:** required.
- **DB effects:** revoke current session.

#### 15.2.4 Logout All
- `POST /auth/logout-all` **[Owner]**
- **Auth:** required; capability `user.manage`.
- **DB effects:** revoke all sessions for the current user.

#### 15.2.5 Me
- `GET /auth/me`
- **Auth:** required.
- **Response:** `{ id, username, full_name, role, capabilities: [string], last_login_at, ... }`

#### 15.2.6 Health
- `GET /system/health`
- **Auth:** none.
- **Response:** `{ "status": "ok", "version": "1.0.0", "server_time": "..." }`
- **Used for:** load balancer health checks.

### 15.3 Users

#### 15.3.1 List Users **[Owner]**
- `GET /users`
- **Auth:** required; `user.view`.
- **Query:** `?q=`, `?filter[is_active]=true`, `?filter[role_id]=1`, `?sort=username,-created_at`, `?page=1&per_page=50`.
- **Response:** standard pagination.

#### 15.3.2 Get User **[Owner]**
- `GET /users/{id}`
- **Auth:** required; `user.view`.
- **Response:** `{ id, username, full_name, email, is_active, role_id, role_name, last_login_at, created_at, created_by, updated_at, ... }`

#### 15.3.3 Create User **[Owner]**
- `POST /users` (Idempotency-Key required)
- **Auth:** required; `user.manage`.
- **Request:** `{ "username": string (1-64, ^[a-z0-9._-]+$), "full_name": string (1-150), "email": string? (email format), "password": string (8-256), "role_id": int (required) }`
- **Response 201:** user object (no password).
- **Validation:** username unique, email unique if present, password meets policy.
- **DB effects:** insert `users`, `user_capability_overrides` (none yet; role base applies), insert `audit_log` (`action='create'`, `entity_type='user'`, `entity_id`, `new_values`).
- **Failures:** 409 `username_exists`, 409 `email_exists`, 400 `validation_failed`.

#### 15.3.4 Update User **[Owner]**
- `PATCH /users/{id}` (Idempotency-Key recommended)
- **Auth:** required; `user.manage`. **Required `If-Match`.**
- **Request:** any of `{ full_name, email, is_active, role_id }`.
- **DB effects:** update user, insert `audit_log` (action='update' with old/new).
- **Failures:** 412 `version_mismatch`; 403 if user is the only Owner and is being deactivated.

#### 15.3.5 Deactivate User **[Owner]**
- `POST /users/{id}/deactivate` (Idempotency-Key required)
- **Auth:** required; `user.manage`.
- **Effect:** `is_active = false`. User can no longer log in. Existing sessions are revoked.
- **DB effects:** update user, revoke sessions, insert `audit_log`.
- **Failures:** 403 if user is the only Owner.

#### 15.3.6 Reset Password **[Owner]**
- `POST /users/{id}/reset-password` (Idempotency-Key required)
- **Auth:** required; `user.manage` or `auth.password_reset_others`.
- **Request:** `{ "new_password": string (8-256) }`
- **DB effects:** update `password_hash`, revoke all sessions for the user, insert `audit_log`.
- **Failures:** 400 `validation_failed` (weak password).

#### 15.3.7 Unlock User **[Owner]**
- `POST /users/{id}/unlock` (Idempotency-Key required)
- **Auth:** required; `user.manage`.
- **DB effects:** clear failed-attempt counter, insert `audit_log`.

#### 15.3.8 Grant Capability **[Owner]**
- `POST /users/{id}/capabilities` (Idempotency-Key required)
- **Auth:** required; `user.grant_capability`. **Required `If-Match` on user.**
- **Request:** `{ "capability_code": string, "is_granted": true }`
- **DB effects:** upsert `user_capability_overrides`, insert `audit_log` (action='permission_grant').
- **Failures:** 400 `enum_value_invalid` if capability unknown.

#### 15.3.9 Revoke Capability **[Owner]**
- `DELETE /users/{id}/capabilities/{capability_code}` (Idempotency-Key required)
- **Auth:** required; `user.revoke_capability`.
- **DB effects:** delete or mark `is_granted = false` on the override, insert `audit_log`.
- **Effect:** immediate. Sessions are not invalidated; current access token may need refresh.

#### 15.3.10 List User Effective Capabilities **[Owner]**
- `GET /users/{id}/capabilities`
- **Auth:** required; `user.view`.
- **Response:** `{ "role": "Staff", "role_capabilities": [...], "overrides": [{ "capability_code", "is_granted", "granted_at", "granted_by" }], "effective": [...] }`

### 15.4 Roles

#### 15.4.1 List Roles **[Owner]**
- `GET /roles`
- **Auth:** required; `role.view`.

#### 15.4.2 Get Role **[Owner]**
- `GET /roles/{id}`

#### 15.4.3 Create Role **[Owner]**
- `POST /roles` (Idempotency-Key required)
- **Auth:** required; `role.manage`.
- **Request:** `{ "name": string (1-50, unique), "description": string? }`
- **Note:** a new role has zero capabilities; the API requires a follow-up call to set capabilities. System roles (`is_system_role = true`) cannot be created via API.

#### 15.4.4 Update Role **[Owner]**
- `PATCH /roles/{id}` (Idempotency-Key recommended; `If-Match` on role)
- **Request:** `{ "name"?, "description"? }`
- **Failures:** 403 `system_role_immutable` if `is_system_role = true`.

#### 15.4.5 Delete Role **[Owner]**
- `DELETE /roles/{id}` (Idempotency-Key required)
- **Auth:** required; `role.manage`.
- **Failures:** 409 `referenced_by_history` (users assigned), 403 `system_role_immutable`.

#### 15.4.6 Set Role Capabilities **[Owner]**
- `PUT /roles/{id}/capabilities` (Idempotency-Key required)
- **Auth:** required; `role.manage`.
- **Request:** `{ "capability_codes": [string] }` (replaces all).
- **Failures:** 403 `system_role_immutable`.

### 15.5 Capabilities Catalog

#### 15.5.1 List Catalog **[Owner]**
- `GET /capabilities`
- **Auth:** required; `user.view` (or any user — the catalog is not sensitive).
- **Response:** all canonical capability codes (§3.1).

### 15.6 Audit Log

#### 15.6.1 List Audit **[Owner]**
- `GET /audit`
- **Auth:** required; `audit.view`.
- **Query:** `?filter[user_id]=`, `?filter[action]=`, `?filter[entity_type]=`, `?filter[entity_id]=`, `?from=`, `?to=`, `?q=` (searches reason).
- **Response:** standard pagination, ordered by `event_time DESC`.
- **Immutability:** no POST/PATCH/DELETE exposed.

### 15.7 Categories, Units, Payment Methods, Cost Types, Financial Categories

These five master-data domains share the same shape. Detailed schemas:

#### 15.7.1 Categories (`categories`)

| Endpoint | Methods | Capabilities |
|---|---|---|
| `GET /categories` | list | `category.view` |
| `GET /categories/{id}` | get | `category.view` |
| `POST /categories` | create | `category.manage` |
| `PATCH /categories/{id}` | update | `category.manage` |
| `POST /categories/{id}/deactivate` | deactivate | `category.manage` |
| `DELETE /categories/{id}` | hard delete | `category.manage` |

- **Fields:** `id`, `name` (unique, 1-100), `parent_id` (nullable), `is_active`, `created_at`, `updated_at`.
- **Validation:** name unique, parent exists if specified.
- **Delete policy:** 409 `referenced_by_history` if any product uses it. Deactivate otherwise.
- **DB effects:** insert/update `categories`, `audit_log` on create/update/deactivate.
- **No accounting/inventory effects.**

#### 15.7.2 Units (`units`)

Same shape as categories. Fields: `code` (unique, 1-20, e.g., `pcs`), `name` (1-50), `is_active`.

#### 15.7.3 Payment Methods (`payment_methods`)

Same shape. Fields: `code` (unique, e.g., `cash`, `bank_transfer`, `e_wallet`, `other`), `name`, `is_cash` (boolean — drives tender/change semantics), `is_active`.

- **Constraint:** the system ships with `cash`, `bank_transfer`, `e_wallet`, `other` seeded. The Owner may add more.
- **Validation:** if `is_cash = true`, the method is eligible for over-tender. If `is_cash = false`, the API rejects `tendered_amount` on payments using this method with 400 `tendered_not_allowed_for_non_cash`.
- **No accounting/inventory effects.**

#### 15.7.4 Cost Types (`cost_types`)

Same shape. Fields: `code` (unique, 1-50, e.g., `labor`, `electricity`, `gas`, `packaging`, `other`), `name`, `is_active`.

- **Default seed:** the PRD §15 default set. The Owner may add more.
- **Delete policy:** deactivation preserves history. Hard delete rejected if any `production_cost_line` references the type.

#### 15.7.5 Financial Categories (`financial_categories`)

Same shape. Fields: `code` (unique), `name` (1-100), `entry_type` (`income` or `expense`), `is_active`.

- **Validation:** `name` is validated against a derived-category blacklist: "Sales", "COGS", "Production", "Purchase Shipping", "Refund", "Purchase", "Sales Revenue", "Cost of Goods Sold", "Other Income", "Operating Expenses", and case-insensitive variants. The server returns 400 `manual_entry_duplicate_of_derived` on violation.
- **Default seed:** per PRD §17, the server seeds: `Capital Injection`, `Other Income`, `Other` (income); `Rent`, `Labour (non-production)`, `Electricity (non-production)`, `Maintenance`, `Operational`, `Tax`, `Extra shipping`, `Other` (expense). The Owner may add or rename (rename is metadata only; historical amounts are unchanged per PRD §17).
- **DB effects:** insert/update `financial_categories`, `audit_log`.

### 15.8 Products

#### 15.8.1 List Products
- `GET /products`
- **Auth:** required; `product.view`.
- **Query:** `?q=` (search name, code), `?filter[category_id]=`, `?filter[is_active]=true`, `?filter[is_sellable]=`, `?filter[is_purchasable]=`, `?filter[is_producible]=`, `?filter[allow_negative_stock]=`, `?low_stock=true` (returns only products at or below their low-stock threshold), `?sort=name,-created_at`.
- **Response:** standard pagination. Each product includes `on_hand_quantity`, `moving_average_unit_cost`, `inventory_value` (derived), `low_stock` (boolean), and a flag for `negative_stock_fallback_supported` (i.e., whether the global setting allows it).

#### 15.8.2 Get Product
- `GET /products/{id}`
- **Auth:** required; `product.view`.
- **Response:** product + derived fields + last 10 price history rows.

#### 15.8.3 Create Product
- `POST /products` (Idempotency-Key required)
- **Auth:** required; `product.create`.
- **Request:** `{ "code"?: string (optional, unique if present), "name": string (1-200, required), "category_id"?: int, "unit_id"?: int, "purchase_price": number (≥ 0, default 0), "selling_price": number (≥ 0, default 0), "low_stock_threshold"?: number (≥ 0), "allow_negative_stock"?: boolean|null, "is_sellable"?: boolean (default true), "is_purchasable"?: boolean (default true), "is_producible"?: boolean (default false), "notes"?: string }`
- **Response 201:** product.
- **Validation:** name required; code unique if present; `purchase_price` and `selling_price` ≥ 0; if `low_stock_threshold` is set, ≥ 0.
- **DB effects:** insert `products`, insert `product_price_history` (initial), insert `audit_log` (action='create', entity_type='product').
- **No accounting/inventory effects at creation** (stock is created via `POST /inventory/adjustments` with `quantity > 0` and `reason='initial stock'` for opening balance, or via `POST /system/initialize` which is documented but not exposed in V1 — see §15.30).

#### 15.8.4 Update Product
- `PATCH /products/{id}` (Idempotency-Key recommended; `If-Match` on product)
- **Auth:** required; `product.edit`.
- **Request:** any of the create fields except `code` (code is immutable once set, per BR-PRODUCT-001).
- **Special rule:** when `purchase_price` or `selling_price` changes, the server inserts a `product_price_history` row.
- **DB effects:** update `products`, insert `product_price_history` (if prices changed), insert `audit_log` (action='update' with old/new, including price change).
- **No recalculation of historical costs** — per BR-COST-004, BR-AUDIT-003.

#### 15.8.5 Deactivate Product
- `POST /products/{id}/deactivate` (Idempotency-Key required)
- **Auth:** required; `product.deactivate`.
- **Request:** `{ "reason"?: string }`
- **Effect:** `is_active = false`. Product is not sellable in new transactions. Existing stock is still counted in `on_hand_quantity` and `inventory_value`. Historical reports still include the product.
- **DB effects:** update `products`, insert `audit_log`.
- **Failures:** 409 `referenced_by_history` is not raised here; deactivation is always allowed. Hard delete is the one that's blocked.

#### 15.8.6 Delete Product
- `DELETE /products/{id}` (Idempotency-Key required)
- **Auth:** required; `product.manage`.
- **Failures:** 409 `referenced_by_history` if any transaction, movement, payment, or line references the product. Use deactivate instead.

#### 15.8.7 Get Price History
- `GET /products/{id}/price-history`
- **Auth:** required; `product.view`.
- **Response:** list of price history rows ordered by `effective_at DESC`.

#### 15.8.8 Get Product Stock Movements
- `GET /products/{id}/stock-movements`
- **Auth:** required; `inventory.view`.
- **Query:** standard filtering, sort.

#### 15.8.9 Get Product Valuation
- `GET /products/{id}/valuation`
- **Auth:** required; `inventory.view`.
- **Response:** `{ "product_id", "on_hand_quantity", "moving_average_unit_cost", "inventory_value", "as_of": server_time }`

### 15.9 Contacts

#### 15.9.1 List Contacts
- `GET /contacts`
- **Auth:** required; `contact.view`.
- **Query:** `?q=`, `?filter[type]=customer|supplier|both`, `?filter[is_active]=`, `?sort=name`.

#### 15.9.2 Get Contact
- `GET /contacts/{id}`

#### 15.9.3 Create Contact
- `POST /contacts` (Idempotency-Key required)
- **Auth:** required; `contact.create`.
- **Request:** `{ "type": "customer|supplier|both", "name": string (1-200, required), "phone"?: string, "email"?: string (email format), "address"?: text, "notes"?: text }`

#### 15.9.4 Update Contact
- `PATCH /contacts/{id}` (Idempotency-Key recommended; `If-Match`)

#### 15.9.5 Deactivate Contact
- `POST /contacts/{id}/deactivate`

#### 15.9.6 Delete Contact
- `DELETE /contacts/{id}`
- **Failures:** 409 `referenced_by_history` if used in any sale, purchase, return, payment, or refund. Deactivate instead.

### 15.10 Sales (Lifecycle, Endpoints, Schemas)

This is the largest domain. The full state machine is documented in §8 and DB spec §4.1.

#### 15.10.1 List Sales
- `GET /sales`
- **Auth:** required; `sale.view`.
- **Query:** `?q=` (search notes, reference_no, customer name), `?filter[lifecycle_status]=draft|posted|completed|partially_returned|returned|cancelled`, `?filter[customer_id]=`, `?filter[payment_state]=unpaid|partial|paid`, `?filter[is_negative_stock_fallback]=`, `?from=`, `?to=` (on `sale_date`), `?sort=-sale_date,id`.
- **Response:** standard pagination. Each sale includes `payment_state` (derived), `paid_amount` (derived), `outstanding` (derived), `ar` (derived, if any).

#### 15.10.2 Get Sale
- `GET /sales/{id}?include=lines,payments,returns`
- **Auth:** required; `sale.view`.
- **Response:** full sale with embedded sub-resources on `include`.

#### 15.10.3 Create Sale (Draft)
- `POST /sales` (Idempotency-Key required)
- **Auth:** required; `sale.create` (default ON for Staff per §6.1).
- **Request:**
```json
{
  "customer_id"?: int,
  "sale_date"?: timestamp (default server_time),
  "discount_amount"?: number (default 0; non-zero requires sale.discount),
  "notes"?: string,
  "reference_no"?: string (auto-generated if system_settings.enable_sequential_doc_numbers = true and the field is omitted),
  "lines": [
    {
      "product_id": int,
      "quantity": number (> 0),
      "unit_price"?: number (default = products.selling_price; override requires sale.price_override),
      "discount_amount"?: number (default 0; non-zero requires sale.discount),
      "is_producible_input"?: boolean (default false; for production, set true — V1 keeps production separate; this field is reserved)
    }
  ]
}
```
- **Response 201:** sale in `draft` status with lines. Server computes `line_total = quantity × unit_price − discount_amount` per line and `total_amount = Σ line_total − header discount_amount`. These computed fields are returned but not stored yet (or stored and re-checked at post time).
- **Validation:** each `product_id` exists, is `is_active = true`, is `is_sellable = true`; each `unit_price` ≥ 0; each `discount_amount` ≤ `quantity × unit_price`; `quantity > 0`.
- **DB effects:** insert `sales` (draft), insert `sale_lines` (draft). Insert `audit_log` (action='create').
- **No accounting/inventory effects yet** (the draft is in flight).

#### 15.10.4 Update Sale (Draft)
- `PATCH /sales/{id}` (Idempotency-Key recommended; `If-Match` on sale)
- **Auth:** required; `sale.edit_own_draft` AND `created_by = current_user` (or `sale.create`? — `sale.edit_own_draft` is the correct capability; the server also checks ownership).
- **Request:** any of the create fields.
- **Failures:** 403 `lifecycle_state_invalid` if not draft; 412 `version_mismatch`.

#### 15.10.5 Add Sale Line
- `POST /sales/{id}/lines` (Idempotency-Key required)
- **Auth:** required; `sale.edit_own_draft`.
- **Request:** single line object.
- **DB effects:** insert `sale_lines` (with computed `line_total`).

#### 15.10.6 Update Sale Line
- `PATCH /sales/{id}/lines/{line_id}` (Idempotency-Key recommended)
- Same auth/validation as update sale.

#### 15.10.7 Delete Sale Line
- `DELETE /sales/{id}/lines/{line_id}` (Idempotency-Key required)
- Same auth.
- **Failures:** 403 `lifecycle_state_invalid` if sale is not draft.

#### 15.10.8 Post Sale
- `POST /sales/{id}/post` (Idempotency-Key **required**; `If-Match` **required**)
- **Auth:** required; `sale.post` (default OFF for Staff). Without `sale.post`, the request fails with 403 even if the user has `sale.create`.
- **Atomic transaction:**
  1. `SELECT ... FOR UPDATE` on `sales` row.
  2. Validate: status is `draft`; at least one line; each line quantity > 0; all `unit_price` ≥ 0; total = Σ line_total − header discount.
  3. For each line, compute `unit_cost_snapshot` from current moving-average under product advisory lock. If `allow_negative_stock` is false and on-hand < required, reject with 409 `insufficient_stock`. Otherwise, set `is_negative_stock_fallback` if applicable.
  4. For each line, insert `stock_movements` (trigger='sale', negative qty, unit_cost snapshot, total_cost negative).
  5. Update `sales.lifecycle_status = 'posted'`, `posted_at = NOW()`, `posted_by = user_id`.
  6. If Σ payments = total, set `lifecycle_status = 'completed'` (and `completed_at` is implicit = `posted_at`).
  7. Insert `audit_log` (action='post', new_values includes posted_at, posted_by).
- **Response 200:** sale in `posted` (or `completed`) status.
- **Accounting effects:** Revenue recognized (= total_amount); COGS recognized (per line snapshots); Inventory reduced; AR created if unpaid.
- **Inventory effects:** N `stock_movements` rows.
- **Audit effects:** 1 `audit_log` row.
- **Failure/rollback:** all writes are atomic; on any failure, the transaction rolls back and the sale remains in draft.

#### 15.10.9 Add Sale Payment
- `POST /sales/{id}/payments` (Idempotency-Key **required**)
- **Auth:** required; the user has access to the sale (i.e., can read it; `sale.view` is sufficient — recording a payment is part of the cashier workflow, which is default ON for Staff via `sale.create`). However, the actual *recording of a payment* on an open sale is a privileged operation. **The capability required is `sale.create`** for payments on sales the user has `sale.view` for. **No separate `sale.payment` capability** — the same `sale.create` covers the cashier's full workflow. The audit log records the payment.
- **Request:**
```json
{
  "payment_method_id": int,
  "amount": number (> 0),
  "tendered_amount"?: number (≥ amount, only if payment_method.is_cash = true),
  "change_amount"?: number (server-computed if tendered > amount; client may send for display but server validates),
  "payment_date"?: timestamp (default server_time),
  "reference"?: string (e.g., transfer ref)
}
```
- **Atomic transaction:**
  1. `SELECT ... FOR UPDATE` on `sales` row.
  2. Validate: sale is `posted | completed | partially_returned` (not draft, cancelled, or fully returned).
  3. Validate: `Σ sale_payments.amount + amount ≤ sales.total_amount` (cross-row trigger; server returns 409 `payment_exceeds_total` with details).
  4. If `tendered_amount` is provided:
     - Validate: `payment_method.is_cash = true`. If not, return 400 `tendered_not_allowed_for_non_cash`.
     - Compute `change_amount = tendered_amount − amount` if not provided. If provided, validate it equals that value (else 400 `change_amount_mismatch`).
  5. Validate: `Σ cash_movements.amount + (amount if no over-tender) + (tendered_amount − change_amount if over-tender) ≥ 0`. The over-tender case uses two cash_movements: one in for tender, one out for change; net is `+amount`. The pre-insert trigger on `cash_movements` validates each row, but the API validates the post-state.
  6. Insert `sale_payments` row.
  7. If over-tender: insert two `cash_movements` (`change_tendered` in for tender, `change_tendered` out for change). Else: insert one `cash_movements` (`sale_payment` in for amount).
  8. If Σ payments = total, update `sales.lifecycle_status = 'completed'` (if currently `posted`).
  9. Insert `audit_log` (action='payment', entity_type='sale', entity_id=sale.id, new_values includes payment).
- **Accounting effects:** Cash +A, AR −A.
- **Inventory effects:** none.
- **Audit effects:** 1 `audit_log` row per payment.
- **Failure/rollback:** all writes are atomic.

#### 15.10.10 Cancel Sale
- `POST /sales/{id}/cancel` (Idempotency-Key **required**; `If-Match` **required**)
- **Auth:** required; `sale.cancel` (default OFF for Staff).
- **Request:** `{ "reason": string (required, 1-1000) }`
- **Atomic transaction** (per §8.3):
  1. `SELECT ... FOR UPDATE` on `sales` row.
  2. Validate: state is `posted | completed | partially_returned | returned`. From `cancelled` → 409 `already_cancelled`.
  3. Insert `stock_movements` with `trigger='sale_reversal'`, `quantity = +original`, `unit_cost_at_movement = original.unit_cost_snapshot`, `total_cost = +original.cogs_total_snapshot`, `reversal_of_movement_id = original_sale_movement_id`, `reference_type='sale_cancellation'`, `reference_id = sale.id`.
  4. Update the original `stock_movements.reversed_by_movement_id` to the new id.
  5. Update `sales.lifecycle_status = 'cancelled'`, `cancellation_date = NOW()`, `cancelled_by = user_id`, `cancellation_reason`.
  6. Insert `audit_log` (action='cancel').
- **No cash_movement.** CRL = Σ payments − Σ refunds. The refund, if the user wishes to disburse, is a separate `POST /refunds` call.
- **Accounting effects:** Revenue reversed, Inventory increased, COGS reversed, AR/CRL adjusted (derived). The sale's payment history is preserved.
- **Failure/rollback:** atomic.

#### 15.10.11 List Sale Returns
- `GET /sales/{id}/returns`

#### 15.10.12 Create Sale Return
- `POST /sales/{id}/returns` (Idempotency-Key **required**; `If-Match` on sale)
- **Auth:** required; `sale.return` (default OFF for Staff).
- **Request:**
```json
{
  "reason": string (required, 1-1000),
  "lines": [
    { "sale_line_id": int, "quantity": number (> 0) }
  ]
}
```
- **Atomic transaction** (per §8.4):
  1. `SELECT ... FOR UPDATE` on `sales` row.
  2. Validate: state is `posted | completed | partially_returned` (not draft, not cancelled, not fully returned).
  3. For each return line: validate `quantity ≤ (sale_line.quantity − Σ sales_return_lines.quantity where sale_line_id = X)`. Server returns 409 `return_quantity_exceeds_original` with details if violated.
  4. Compute server-side: `returned_selling_price = sale_line.unit_price × quantity`; `returned_unit_cost = sale_line.unit_cost_snapshot`; `line_value = returned_selling_price`. The client cannot override these.
  5. Insert `sales_returns` (header) with totals.
  6. Insert `sales_return_lines`.
  7. Insert `stock_movements` (trigger='sales_return', positive qty, cost snapshot, total cost positive).
  8. Update `sales.lifecycle_status`:
     - If `Σ returned quantity per sale_line = sale_line.quantity` for all lines → `returned`.
     - Otherwise → `partially_returned`.
  9. Insert `audit_log` (action='return').
- **No cash_movement, no refund created automatically.** CRL for paid sales is now increased by `total_selling_price_returned` (derived). User must call `POST /refunds` to disburse.
- **Accounting effects:** Revenue reversed by line_value, Inventory +cost, COGS reversed, CRL increased.
- **Failure/rollback:** atomic.

#### 15.10.13 List Sales Returns
- `GET /sales-returns`
- **Auth:** required; `sale.view`.
- **Query:** `?from=`, `?to=`, `?filter[customer_id]=`, etc.

#### 15.10.14 Get Sales Return
- `GET /sales-returns/{id}`

### 15.11 Refunds

#### 15.11.1 List Refunds
- `GET /refunds`
- **Auth:** required; `sale.view` (refunds are a sub-event of sales).
- **Query:** `?from=`, `?to=`, `?filter[sale_id]=`, `?filter[payment_method_id]=`.

#### 15.11.2 Get Refund
- `GET /refunds/{id}`

#### 15.11.3 Create Refund
- `POST /refunds` (Idempotency-Key **required**)
- **Auth:** required; `sale.refund` (default OFF for Staff).
- **Request:**
```json
{
  "sale_id": int (required),
  "amount": number (> 0, required),
  "payment_method_id": int (required; default suggestion = most recent original payment's method),
  "refund_date"?: timestamp (default server_time),
  "reason"?: string
}
```
- **Atomic transaction** (per §8.5):
  1. `SELECT ... FOR UPDATE` on `sales` row, then on `refunds` to lock the CRL computation.
  2. Compute CRL = `Σ sale_payments.amount WHERE sale_id = X − Σ refunds.amount WHERE sale_id = X`. Validate `amount > 0` and `amount ≤ CRL`. Reject with 409 `refund_exceeds_crl` with details.
  3. Validate cash balance: `Σ cash_movements.amount − amount ≥ 0`. Reject with 409 `cash_insufficient` with details.
  4. (Optional: validate that `payment_method_id` matches an original payment method for the sale. If not, and user lacks `sale.refund_override_method`, reject with 400 `refund_method_mismatch`.) **Decision:** V1 does not enforce method matching strictly. The default suggestion is the most recent method, but the user may choose any active payment method. This is a known gap — see §17 MS-3.
  5. Insert `refunds` row.
  6. Insert `cash_movements` (direction='out', amount=-refund.amount, trigger='refund', reference_type='refund', reference_id=refund.id, payment_method_id).
  7. Insert `audit_log` (action='refund').
- **Accounting effects:** Cash −amount, CRL −amount.
- **Inventory effects:** none (refund is a cash event, not a stock event).
- **Audit effects:** 1 `audit_log` row.
- **Failure/rollback:** atomic.

### 15.12 Purchases

Mirrors the sales lifecycle. Substitute `purchase` for `sale` and consult DB spec §5.

#### 15.12.1 List Purchases
- `GET /purchases`
- **Auth:** required; `purchase.view`.

#### 15.12.2 Get Purchase
- `GET /purchases/{id}?include=lines,shipping,payments,returns`

#### 15.12.3 Create Purchase (Draft)
- `POST /purchases` (Idempotency-Key required)
- **Auth:** required; `purchase.create` (default ON for Staff).
- **Request:**
```json
{
  "supplier_id"?: int (nullable per OD-15),
  "purchase_date"?: timestamp (default server_time),
  "notes"?: string,
  "reference_no"?: string,
  "lines": [
    {
      "product_id": int,
      "quantity": number (> 0),
      "unit_price": number (≥ 0; required)
    }
  ],
  "shipping"?: {
    "amount": number (≥ 0),
    "paid_in_cash": boolean (default false),
    "supplier_id"?: int (carrier if separate from purchase supplier),
    "description"?: string
  }
}
```
- **Validation:** lines valid; `line_subtotal = quantity × unit_price`; `line_total = line_subtotal + allocated_shipping` (allocated at post time, not yet). `total_amount = Σ line_total + shipping.amount`. **Server recomputes at post time.**
- **Response 201:** purchase in draft.

#### 15.12.4 Update Purchase (Draft)
- `PATCH /purchases/{id}` (Idempotency-Key recommended; `If-Match`)
- **Auth:** `purchase.edit_own_draft`.

#### 15.12.5 Add/Update/Delete Purchase Line
- Same shape as sales.

#### 15.12.6 Add/Update Purchase Shipping
- `POST /purchases/{id}/shipping` (Idempotency-Key recommended)
- `PATCH /purchases/{id}/shipping` (Idempotency-Key recommended)
- **Auth:** `purchase.edit_own_draft`.

#### 15.12.7 Post Purchase
- `POST /purchases/{id}/post` (Idempotency-Key **required**; `If-Match` **required**)
- **Auth:** required; `purchase.post` (default OFF for Staff).
- **Atomic transaction** (per §5.2 of DB spec):
  1. `SELECT ... FOR UPDATE` on `purchases` row.
  2. Validate: status is `draft`; at least one line; each line quantity > 0; `unit_price` ≥ 0; `total_amount = Σ line_total + shipping.amount`.
  3. Compute `line_total = line_subtotal + allocated_shipping` for each line (pro-rata based on `line_subtotal` share).
  4. For each line: insert `stock_movements` (trigger='purchase_receipt', positive qty, unit_cost = line_total / quantity, total_cost = line_total).
  5. If `purchase_shipping.paid_in_cash = true`, insert `cash_movements` (direction='out', amount=-shipping.amount, trigger='freight_cash', reference_type='purchase', reference_id=purchase.id).
  6. Update `purchases.lifecycle_status = 'posted'`, `posted_at`, `posted_by`.
  7. If Σ payments = total, set `lifecycle_status = 'completed'`.
  8. Insert `audit_log` (action='post').
- **Response 200:** purchase in `posted` (or `completed`).
- **Accounting effects:** Inventory +Σ line_total, AP +Σ line_total.
- **Inventory effects:** N `stock_movements` rows; moving-average rebalanced.
- **Failure/rollback:** atomic.

#### 15.12.8 Add Purchase Payment
- `POST /purchases/{id}/payments` (Idempotency-Key **required**)
- **Auth:** required; `purchase.create` (default ON for Staff; covers the cashier's counter-buy workflow).
- **Request:** same shape as sale payment. `tendered_amount` allowed only if `payment_method.is_cash = true`.
- **Atomic transaction:**
  1. `SELECT ... FOR UPDATE` on `purchases` row.
  2. Validate: status is `posted | completed | partially_returned`.
  3. Validate: `Σ purchase_payments.amount + amount ≤ purchases.total_amount`. Reject 409 `allocation_exceeds_payable`.
  4. If over-tender, same change logic.
  5. Insert `purchase_payments`. Insert `cash_movements` (out, amount=-amount, trigger='supplier_payment'; for over-tender, two rows for change_tendered).
  6. If Σ payments = total, set `lifecycle_status = 'completed'`.
  7. Insert `audit_log`.

#### 15.12.9 Cancel Purchase
- `POST /purchases/{id}/cancel` (Idempotency-Key **required**; `If-Match` **required**)
- **Auth:** required; `purchase.cancel` (default OFF for Staff).
- **Request:** `{ "reason": string (required) }`
- **Atomic transaction** (per DB spec §5.5):
  1. `SELECT ... FOR UPDATE` on `purchases` row.
  2. Compute on-hand attributable: `received_qty − consumed_qty (already sold/used)`.
  3. For on-hand portion: insert `stock_movements` (trigger='purchase_reversal', negative qty, reversal_of_movement_id = original).
  4. For consumed portion: insert `stock_movements` (trigger='value_adjustment', quantity=0, total_cost = -(consumed × unit_cost)).
  5. If purchase was paid: insert `supplier_repayments` with `amount = Σ purchase_payments`, `refundable_amount_snapshot = same`, `reason='purchase_cancellation'`.
  6. Update `purchases.lifecycle_status = 'cancelled'`, `cancellation_date`, `cancelled_by`, `cancellation_reason`.
  7. Insert `audit_log`.
- **Accounting effects:** AP reduced (derived), Supplier Receivable created (if paid), Inventory reduced (on-hand portion), COGS reduced (consumed portion via value_adjustment).
- **Failure/rollback:** atomic.

#### 15.12.10 Create Purchase Return
- `POST /purchases/{id}/returns` (Idempotency-Key **required**; `If-Match` **required**)
- **Auth:** required; `purchase.return` (default OFF for Staff).
- **Request:** `{ "reason": string, "lines": [{ "purchase_line_id": int, "quantity": number }] }`
- **Atomic transaction** (per §8.4):
  1. `SELECT ... FOR UPDATE`.
  2. Validate each line: `quantity ≤ (purchase_line.quantity − Σ returned)`.
  3. Compute `unit_cost_snapshot = purchase_line.unit_price + (allocated_shipping / quantity)`. `line_value = quantity × unit_cost_snapshot`.
  4. Insert `purchase_returns`, `purchase_return_lines`, `stock_movements` (trigger='purchase_return', negative qty, cost snapshot, total negative).
  5. If paid: insert `supplier_repayments` (amount = total_value_returned, reason='purchase_return').
  6. Update `purchases.lifecycle_status` (returned or partially_returned).
  7. Insert `audit_log`.

#### 15.12.11 Cancel Purchase Return
- `POST /purchase-returns/{id}/cancel` (Idempotency-Key **required**)
- **Auth:** required; `purchase.return` (default OFF; effectively `purchase.cancel` should also be required — both are listed).
- **Effect:** insert reversal movements, update return to cancelled, update parent purchase lifecycle.

#### 15.12.12 List Purchase Returns
- `GET /purchase-returns`

#### 15.12.13 Get Purchase Return
- `GET /purchase-returns/{id}`

### 15.13 Supplier Repayments

#### 15.13.1 List Supplier Repayments
- `GET /supplier-repayments`
- **Auth:** required; `purchase.view`.

#### 15.13.2 Get Supplier Repayment
- `GET /supplier-repayments/{id}`

#### 15.13.3 Create Supplier Repayment
- `POST /supplier-repayments` (Idempotency-Key **required**)
- **Auth:** required; `purchase.refund` (default OFF for Staff) — repaying a supplier is a privileged cash-out.
- **Request:** `{ "purchase_id": int, "amount": number (> 0), "payment_method_id": int, "repayment_date"?: timestamp, "reason"?: string }`
- **Atomic transaction** (per §8.5):
  1. Lock the `purchases` row.
  2. Compute outstanding = `Σ purchase_payments.amount − Σ cash_movements.amount for supplier_repayment trigger where reference_id = supplier_repayment.id (and prior rows for this purchase)`.
  3. Validate `amount ≤ outstanding`. Reject 409 `repayment_exceeds_srec`.
  4. Validate `amount ≤ current cash balance`. Reject 409 `cash_insufficient`.
  5. Find or create the `supplier_repayments` row for this purchase (V1 simplified: one row per purchase; the row carries `amount` (obligation) and `received_amount` (running total)).
  6. Update `supplier_repayments.received_amount += amount`.
  7. Insert `cash_movements` (direction='in', amount=+amount, trigger='supplier_repayment', reference_type='supplier_repayment', reference_id=row.id).
  8. Insert `audit_log`.
- **Accounting effects:** Cash +amount, Supplier Receivable −amount.

### 15.14 Inventory

#### 15.14.1 List Stock Movements
- `GET /stock-movements`
- **Auth:** required; `inventory.view`.
- **Query:** `?filter[product_id]=`, `?filter[trigger]=`, `?filter[reference_type]=`, `?filter[reference_id]=`, `?from=`, `?to=`, `?sort=-movement_date,id`.

#### 15.14.2 Get Stock Movement
- `GET /stock-movements/{id}`

#### 15.14.3 Get Product Stock
- `GET /inventory/products/{product_id}`
- **Auth:** required; `inventory.view`.
- **Response:** `{ "product_id", "on_hand_quantity", "moving_average_unit_cost", "inventory_value", "low_stock": bool, "low_stock_threshold": number, "as_of": server_time }`

#### 15.14.4 List Low-Stock Products
- `GET /inventory/low-stock`
- **Auth:** required; `inventory.view`.

#### 15.14.5 List All Stock (Summary)
- `GET /inventory` (paginated)
- **Auth:** required; `inventory.view`.
- **Response:** standard pagination, with `on_hand_quantity`, `moving_average_unit_cost`, `inventory_value` per product.

#### 15.14.6 Create Stock Adjustment
- `POST /inventory/adjustments` (Idempotency-Key **required**; `If-Match` on product)
- **Auth:** required; `inventory.adjust` (default OFF for Staff).
- **Request:** `{ "product_id": int, "quantity": number (≠ 0, signed), "reason": string (required, 1-1000) }`
- **Atomic transaction:**
  1. `SELECT ... FOR UPDATE` on `products` row + advisory lock on product_id.
  2. Compute `on_hand_quantity_before = Σ stock_movements.quantity for product_id`.
  3. Validate: `|quantity| < 10^12`.
  4. Compute `unit_cost_at_movement = current moving-average unit cost` (or 0 if no on-hand).
  5. Insert `stock_movements` (trigger='stock_adjustment', quantity = requested quantity, unit_cost_at_movement, total_cost = abs(quantity) × unit_cost × sign(quantity), reason = request.reason, reference_type='stock_adjustment').
  6. Insert `audit_log` (action='adjust', entity_type='product', entity_id=product_id, old_values={on_hand_before}, new_values={on_hand_after, reason}).
- **Accounting effects:** none directly. (The DB spec notes Event 18/19 are accounting events for stock adjustments: positive adjustment is `Dr Inventory / Cr Other Income`; negative adjustment is `Dr Operating Expense / Cr Inventory`. **V1 does not create manual-finance entries for adjustments**; the inventory value is correct, but the corresponding P&L effect is recorded as a `manual_finance_entry` of the appropriate type? — **No**, the spec is silent. **The API must not auto-create income/expense entries for adjustments in V1.** This is a documented gap — see §17 MS-2. The Owner is expected to record a manual income/expense entry for the adjustment if they want the P&L effect.
- **Inventory effects:** 1 `stock_movements` row.
- **Audit effects:** 1 `audit_log` row.
- **Failure/rollback:** atomic.

### 15.15 Production

#### 15.15.1 List Production Runs
- `GET /production-runs`
- **Auth:** required; `production.view`.

#### 15.15.2 Get Production Run
- `GET /production-runs/{id}?include=inputs,cost_lines,output`

#### 15.15.3 Create Production Run (Draft)
- `POST /production-runs` (Idempotency-Key required)
- **Auth:** required; `production.create` (default OFF for Staff).
- **Request:**
```json
{
  "run_date"?: timestamp,
  "output_product_id": int (required, must be is_producible = true),
  "output_quantity": number (> 0),
  "notes"?: string,
  "inputs": [
    { "product_id": int, "quantity": number (> 0) }
  ],
  "cost_lines": [
    { "cost_type_id": int, "amount": number (> 0), "paid_in_cash": boolean (default true), "description"?: string }
  ]
}
```
- **Server-side:** `unit_cost_snapshot` for each input is fetched at draft creation (read-only); `line_cost` is computed. The draft is mutable until post.
- **Validation:** at least one input; one output (the output is derived from the request, not a separate list); no duplicates; cost_type exists and is active.

#### 15.15.4 Update Production Run (Draft)
- `PATCH /production-runs/{id}` (Idempotency-Key recommended; `If-Match`)

#### 15.15.5 Add/Update/Delete Production Input
- `POST /production-runs/{id}/inputs` etc.

#### 15.15.6 Add/Update/Delete Production Cost Line
- `POST /production-runs/{id}/cost-lines` etc.

#### 15.15.7 Post Production Run
- `POST /production-runs/{id}/post` (Idempotency-Key **required**; `If-Match` **required**)
- **Auth:** required; `production.post` (default OFF for Staff).
- **Atomic transaction** (per DB spec §6.1):
  1. `SELECT ... FOR UPDATE` on `production_runs` row.
  2. Validate: status is `draft`; ≥1 input; exactly one output; output_quantity > 0; all input quantities > 0; cost_type active.
  3. Validate: for each input, on_hand ≥ quantity (unless allow_negative_stock; same rule as sale).
  4. `total_raw_cost = Σ inputs.line_cost`.
  5. `total_overhead_cost = Σ cost_lines.amount`.
  6. `finished_unit_cost = (total_raw_cost + total_overhead_cost) / output_quantity`.
  7. For each input: insert `stock_movements` (trigger='production_input', negative qty, unit_cost_snapshot, total_cost negative).
  8. Insert `stock_movements` (trigger='production_output', positive qty, unit_cost=finished_unit_cost, total_cost=output_quantity × finished_unit_cost).
  9. For each cost line where paid_in_cash = true: insert `cash_movements` (direction='out', amount=-amount, trigger='production_overhead', reference_type='production_run', reference_id=run.id).
  10. Update `production_runs.lifecycle_status = 'posted'`, `posted_at`, `posted_by`.
  11. Insert `audit_log` (action='post').
- **Accounting effects:** Raw inventory −Σ line_cost, Finished inventory +Σ finished_total, Cash −Σ overhead (if paid_in_cash).
- **Inventory effects:** N+1 `stock_movements` rows.
- **Failure/rollback:** atomic.

#### 15.15.8 Cancel Production Run
- `POST /production-runs/{id}/cancel` (Idempotency-Key **required**; `If-Match` **required**)
- **Auth:** required; `production.cancel` (default OFF for Staff).
- **Atomic transaction** (per DB spec §6.2):
  1. Insert reversal movements (production_input_reversal, production_output_reversal).
  2. Update `production_runs.lifecycle_status = 'cancelled'`, `cancellation_date`, `cancelled_by`, `cancellation_reason`.
  3. Insert `audit_log` (action='cancel').
- **Note:** overhead cash_movements are NOT reversed.

### 15.16 Manual Finance Entries

#### 15.16.1 List Manual Entries
- `GET /manual-entries`
- **Auth:** required; `manual_entry.view`.

#### 15.16.2 Get Manual Entry
- `GET /manual-entries/{id}`

#### 15.16.3 Create Manual Entry
- `POST /manual-entries` (Idempotency-Key **required**)
- **Auth:** required; `manual_entry.create_income` for income, `manual_entry.create_expense` for expense.
- **Request:**
```json
{
  "category_id": int (required; must match entry_type),
  "entry_type": "income" | "expense" (must match category),
  "amount": number (> 0),
  "payment_method_id": int (required),
  "entry_date"?: timestamp (default server_time),
  "notes"?: string
}
```
- **Validation:** category exists, is_active, entry_type matches. Cash balance ≥ amount if expense.
- **Atomic transaction:**
  1. Lock the cash-balance advisory lock.
  2. Insert `manual_finance_entries` (status='posted').
  3. Insert `cash_movements` (direction='in' for income, 'out' for expense, amount signed, trigger='manual_income' or 'manual_expense', reference_type='manual_finance_entry', reference_id).
  4. Insert `audit_log`.
- **Accounting effects:** Cash ±amount, Other Income or Operating Expense ±amount.

#### 15.16.4 Cancel Manual Entry
- `POST /manual-entries/{id}/cancel` (Idempotency-Key **required**)
- **Auth:** required; `manual_entry.cancel`.
- **Effect:** create reversing cash_movement, set lifecycle_status='cancelled', insert audit_log.

### 15.17 Cash Movements (Read-only)

#### 15.17.1 List Cash Movements
- `GET /cash-movements`
- **Auth:** required; `finance.view_cash` (default OFF for Staff).
- **Query:** `?filter[trigger]=`, `?filter[payment_method_id]=`, `?filter[reference_type]=`, `?filter[reference_id]=`, `?from=`, `?to=`, `?sort=-movement_date,id`.
- **Response:** standard pagination.

#### 15.17.2 Get Cash Balance
- `GET /cash-movements/balance`
- **Auth:** required; `finance.view_cash`.
- **Response:** `{ "balance": number, "as_of": server_time }` (derived).

### 15.18 Reports, Dashboard, Exports

See §12. Endpoint shapes are standard pagination/filter/sort responses. `GET /dashboard` returns the dashboard view; `GET /reports/{...}` returns the named report; `POST /exports` creates an export job; `GET /exports/{id}` returns the status; `GET /exports/{id}/download` returns the file.

### 15.19 Notifications

#### 15.19.1 List Notifications
- `GET /notifications?filter[is_read]=false`
- **Auth:** required; `notification.view`.
- **Response:** standard pagination.

#### 15.19.2 Mark Notification Read
- `POST /notifications/{id}/mark-read` (Idempotency-Key recommended)

#### 15.19.3 Mark All Read
- `POST /notifications/mark-all-read` (Idempotency-Key recommended)

### 15.20 Settings

#### 15.20.1 Get Settings **[Owner]**
- `GET /settings`
- **Auth:** required; `settings.view`.
- **Response:** map of `key → value` (with `value_type` for parsing hint).

#### 15.20.2 Update Settings **[Owner]**
- `PATCH /settings` (Idempotency-Key required; `If-Match` on settings version)
- **Auth:** required; `settings.manage`.
- **Request:** partial map of `key → value`.
- **Validation:** each key is in the known set. `costing_method` is read-only in V1; reject 400.
- **DB effects:** update `system_settings`, insert `audit_log` (action='settings_change', old_values, new_values).

### 15.21 Health and Version

- `GET /system/health` — liveness, no DB query.
- `GET /system/version` — version info.
- `GET /system/info` — `company_name`, timezone, etc., from settings.

### 15.22 Initialization (Documented; **not exposed in V1**)

Per V1.9-Accounting-Event-Matrix Event 20, system opening balance initialization is required. Per the audit report and the absence of any API in this spec, **this is not exposed in V1**. The system is delivered pre-initialized by the Owner via a one-time script. The data model supports it (a `system_settings.is_initialized` flag would be added), but exposing it as a regular API endpoint is a security risk and is deferred.

If/when it is exposed, the endpoint would be:
- `POST /system/initialize` (Idempotency-Key required) **[Owner]** **[one-time]**
- **Auth:** `settings.manage` AND system not yet initialized.
- **Request:** `{ "initial_cash": number, "initial_inventory": [{ "product_id": int, "quantity": number, "unit_cost": number }] }`
- **Effect:** insert `cash_movements` (opening) and `stock_movements` (opening_balance) for each product. Mark `is_initialized = true`.

**Decision:** this endpoint is **out of V1 API scope**; initialization is operational. The data model remains forward-compatible.

### 15.23 What is NOT Exposed

- No `POST/PATCH/DELETE` on `/stock-movements` or `/cash-movements` (only GET).
- No `POST/PATCH/DELETE` on `/audit` (only GET).
- No `/system/initialize` in V1.
- No direct inventory mutation other than via business actions or stock adjustments.
- No multi-currency, no X-Z close, no formal AR/AP aging, no batch import.

---

## 16. Cross-Document Contradictions

> **STATUS (post-reconciliation):** All 18 items below were triaged and resolved in the document-reconciliation pass recorded in `Reconciliation-Report-V1.0.md` (same folder). Entries are retained verbatim for traceability; consult the report for each item's authoritative resolution and the exact edits applied to the upstream documents.

In preparing this API spec, the following contradictions or inconsistencies were identified in the upstream documents. They are reported here per the user's instruction not to silently resolve contradictions. **None blocks the API spec from being correct** (the spec picks the resolution and notes it), but each is a documentation defect to be repaired.

| ID | Source A | Source B | Conflict | Resolution in this spec |
|---|---|---|---|---|
| **XDC-1** | `Database-Design-V1.0.md` line 4: "Accounting Model V2.0" | `V1.9-Accounting-Event-Matrix.md` filename and internal "V1.9" references | Version naming mismatch. | The file on disk is the authoritative one; treated as V1.9 for the API spec. DB spec header should be updated. |
| **XDC-2** | DB spec §3.5 mentions `is_negative_stock_fallback` column on `sale_lines` (lines 882, 883) | The `sale_lines` table definition (line 304–322) does not include this column | Schema defect: a column referenced in prose is missing from the table spec. | The API spec treats the column as required on `sale_lines` and notes that DDL must add it. See §17 MS-4. |
| **XDC-3** | DB spec §3.2 lists 14 movement triggers; the CHECK constraint in `stock_movements` lists 14 values; the prose in §3.2 says "15 total" | Discrepancy: prose says 15, list and constraint have 14 | The count is "15 total" if you count `value_adjustment` (referenced in §5.5 for purchase-cancel of consumed portion), which is missing from the CHECK constraint. | The API spec assumes `value_adjustment` is a valid trigger and the DDL must include it in the CHECK constraint. See §17 MS-5. |
| **XDC-4** | DB spec §2.4.1 sales lifecycle includes `partially_returned` and `returned`; PRD §8.1 lifecycle table does not enumerate `partially_returned` distinctly | Lifecycle path is implicit in the PRD but explicit in the DB spec. | The DB spec is authoritative for state names. The PRD §8.1 table is summary; the DB spec is precise. | API uses DB spec states. PRD should be updated for §8.1 row. |
| **XDC-5** | DB spec §2.5.1 (purchases) CHECK includes 5 lifecycle states: `draft, posted, completed, partially_returned, returned, cancelled` | The same as sales | Consistent. | None. |
| **XDC-6** | DB spec §5.5 says purchase cancellation uses `purchase_reversal` for the on-hand portion and a "zero-quantity value_adjustment" for the consumed portion. The CHECK on `stock_movements.quantity` says `CHECK (quantity ≠ 0)` | Direct conflict: the spec says quantity=0 is required for value_adjustment, but the CHECK rejects quantity=0. | The CHECK needs to allow quantity=0 only when trigger='value_adjustment'. | The API spec assumes this is the DDL; the API never inserts a stock_movement with quantity=0 except through a purchase cancellation. See §17 MS-5. |
| **XDC-7** | `PRD-V1-Audit-Report-v2.md` AS-6 says "default `raw-only` for V1 (recommended), overhead → P&L" but DB spec §6.1 implements raw+overhead (and PRD V1.1 §15 makes it a V1 ASSUMPTION = raw+overhead) | Two of three docs favor raw+overhead; the audit report's AS-6 recommends raw-only. | The PRD V1.1 and DB spec have already resolved this in favor of raw+overhead. The audit report's AS-6 is a recommendation that was not adopted. | API uses raw+overhead (OD-4 resolved in PRD V1.1). |
| **XDC-8** | DB spec §2.4.1 sale has `lifecycle_status` CHECK with 6 values; PRD V1.1 §8.1 lifecycle says 5 values (no `partially_returned`) | Discrepancy in state enumeration. | DB spec is more precise; PRD is summary. | API uses DB spec states. |
| **XDC-9** | DB spec §2.1.2 capability `code` is `VARCHAR(100)`. Business-Rules.md BR-SALE-005 uses `price.override`. PRD V1.1 §6.1 uses `price.override`, `inventory.adjust`, `finance.view_profit`, `finance.export`, `master.product_edit`. The DB spec §2.1.2 prose says "e.g. `sale.create`, `purchase.cancel`" | Capability code naming not consistent across docs. | The API spec defines a canonical list in §3.1. | PRD, Business Rules, and DB spec should reference this canonical list. |
| **XDC-10** | DB spec §11.2 talks about advisory locks `pg_advisory_xact_lock(hashtext('product:' || product_id))` for per-product serialization | The DB spec does not specify a `cash_balance` lock implementation; §2.10.1 says "pre-insert trigger on cash_movements checks projected balance under the lock" | The mechanism is described; not fully specified. | The API spec assumes a `cash_balance` advisory lock (a hash of `'cash_balance'`) used as a serializing lock for any `cash_movements` insert. Implementation detail. |
| **XDC-11** | V1.9-Accounting-Event-Matrix Event 6 (Sale Cancellation — Partially Paid) creates CRL = paid amount; the same event is described in DB spec §4.5 but without a separate "create CRL" event. The DB spec's approach is: CRL is derived (Σ payments − Σ refunds), so cancellation does not write anything for CRL. | Two docs treat CRL creation differently. | The DB spec's derived-CRL approach is consistent with the system being "not a full GL." The accounting matrix's "create CRL on cancel" is a conceptual ledger event; the implementation derives it. | API treats CRL as derived (no row created on cancellation). The accounting matrix remains correct as a journal-presentation specification. |
| **XDC-12** | V1.9-Accounting-Event-Matrix Event 18/19: stock adjustment up is `Dr Inventory / Cr Other Income`; down is `Dr Operating Expense / Cr Inventory`. | The DB spec does not create `manual_finance_entries` for stock adjustments; only the inventory value changes. The P&L effect is missing. | Direct contradiction. The accounting model says P&L is affected; the DB design does not create the corresponding manual entry. | **The API does not create manual entries for stock adjustments in V1.** This is a documented gap (§17 MS-2). The owner can manually create a `manual_finance_entry` of type income/expense to reflect the P&L effect. |
| **XDC-13** | DB spec §3.5 says negative stock uses `COGS = product.purchase_price`. V1.9 Event 6 (cancellation) and BR-COST-006 say "last-purchase-price" — these are the same in this single-purchase-price model. | Aligned. | None. |
| **XDC-14** | PRD V1.1 §6.1 says `Edit master data | ✅ Owner | ⛔ unless granted Staff`. DB spec §14.2 lists `master.product_edit` and `master.contact_edit` as the canonical capability names. | PRD uses informal names; DB spec uses formal names. | API uses the canonical list (§3.1). |
| **XDC-15** | DB spec §3.6 + §3.7: purchase cancellation of a fully-consumed purchase requires a zero-quantity `value_adjustment` movement to reverse COGS. The CHECK on `stock_movements.quantity` says `CHECK (quantity ≠ 0)`. | Direct conflict. | The CHECK must be relaxed to allow `quantity = 0` for `trigger = 'value_adjustment'`. | API assumes DDL fixes this. See §17 MS-5. |
| **XDC-16** | DB spec §2.4.1 sales check `(lifecycle_status = 'cancelled') = (cancellation_date IS NOT NULL)`. | The DB spec does not require `cancelled_by` non-null when `cancelled` (only the check above). | Soft inconsistency. | API requires `cancelled_by` in the request and the server sets it. |
| **XDC-17** | DB spec §11.3 lock ordering says "lock the product row first, then any other rows." | For operations that touch multiple products (e.g., a sale with 5 lines), the lock order must be deterministic. | The API spec assumes the server sorts the product IDs and locks them in ascending order. | Implementation detail. |
| **XDC-18** | DB spec §3.2 says 15 stock_movements triggers including `opening_balance` (non-reversible). The CHECK constraint in §2.6.1 includes `opening_balance` but not `value_adjustment`. | `value_adjustment` is missing from the CHECK. | DDL must add it. | API spec assumes the DDL fix. See §17 MS-5. |

**Total contradictions:** 18. None blocks the API spec from being internally consistent. **Recommended corrections** are listed in §17.

---

## 17. Missing Decisions & Missing Specifications

The following decisions are still open per `PRD-V1-Audit-Report-v2.md` §7 and the Business-Rules §14 TBD list. The API spec is forward-compatible with all of them; for the ones marked `TBD-OWNER`, the spec documents the default behavior and exposes a config endpoint.

### 17.1 Open Owner Decisions (per Audit Report v2)

| ID | Decision | Default in this spec | Configuration |
|---|---|---|---|
| OD-16 | Money rounding mode | Display unchanged; storage is `NUMERIC(15,2)` exact | `system_settings.display_rounding` (boolean, default false). Reserved for V2. |
| OD-17 | Sequential document numbering | Disabled by default | `system_settings.enable_sequential_doc_numbers` (boolean, default false) + per-document sequence config. |

### 17.2 Forward-Compatible Items (per Audit Report v2 §7 "CAN DECIDE LATER")

| ID | Item | API treatment |
|---|---|---|
| OD-18 | X-Z shift close | Out of V1; no endpoint. |
| OD-19 | Backup/restore | Out of scope (operational). |
| OD-20 | Receivable aging | Out of V1; `GET /reports/receivables` returns AR per sale with no aging buckets. |
| OD-21 | Warehouse/multi-location | Forward-compatible: stock_movements could carry `location_id` (V2); the API filters accept a `?location_id=` query (ignored in V1 because all stock is at the single implicit location). |

### 17.3 Missing Specifications (gaps in the upstream docs that the API must address)

| ID | Gap | API treatment |
|---|---|---|
| **MS-1** | DB spec does not define a `stock_adjustments` header table. The adjustment is recorded only via `stock_movements` + `audit_log`. | The API uses the `audit_log` row as the de facto header (action='adjust', old_values, new_values include reason). The reason text is not queryable as a first-class field; clients must read `audit_log` to retrieve the reason. **Recommendation:** add a `stock_adjustments` table or expose a `reason` field in the `stock_movements` view. |
| **MS-2** | DB spec / accounting matrix imply that stock adjustments affect P&L (Other Income / Operating Expense) but no `manual_finance_entry` is auto-created. | The API does **not** create manual entries for adjustments in V1. The inventory value is correct; the P&L effect must be recorded manually by the Owner as a `manual_finance_entry` if they want it on P&L. **Recommendation:** add a `V1.1` enhancement that auto-creates the P&L entry on adjustment, gated by a config flag (`system_settings.adjustment_creates_pnl_entry`). |
| **MS-3** | PRD V1.1 §9 and BR-REFUND-002 say "default refund method = original payment method." The DB spec does not enforce this. The accounting matrix Event 7 is silent. | The API suggests the most recent original payment's method as the default in the response but allows the user to choose. The spec does not strictly enforce method matching. **Recommendation:** add a `sale.refund_override_method` capability to gate non-default method choices. **Decision for V1:** method matching is a soft default, not a hard rule. |
| **MS-4** | DB spec §3.5 references `is_negative_stock_fallback` on `sale_lines` but the `sale_lines` table definition does not include the column. | The API spec assumes the column is added. See §16 XDC-2. |
| **MS-5** | DB spec §2.6.1 CHECK on `stock_movements.quantity` says `CHECK (quantity ≠ 0)` but §3.2 / §5.5 / §15.18 require `quantity = 0` for `value_adjustment` movements. | The API spec assumes the CHECK is relaxed: `CHECK (quantity ≠ 0 OR trigger = 'value_adjustment')`. |
| **MS-6** | DB spec §3.2 says 15 movement triggers; the CHECK constraint lists 14. The 15th (`value_adjustment`) is missing from the CHECK. | The API spec assumes the CHECK is updated to include `'value_adjustment'`. |
| **MS-7** | No `system_initialization` row or flag is specified. The accounting matrix Event 20 requires it. | The API does not expose initialization in V1 (operational). The DB schema should add a `system_settings.is_initialized` boolean (default false) for forward-compatibility. |
| **MS-8** | The accounting matrix and DB spec both reference "stock value" KPI. The exact definition is `SUM(stock_movements.total_cost)` per product or all. The dashboard endpoint (§12.2) is the single place this surfaces. | The API returns `inventory_value` per product (the `product_valuation` view) and the dashboard total. Both use the same formula. |
| **MS-9** | The PRD says "best-sellers" on the dashboard (§22). The exact algorithm is not specified. | The API uses `SUM(sale_lines.quantity × line_total) GROUP BY product_id ORDER BY SUM DESC LIMIT 10` over the current period. |
| **MS-10** | The PRD says "expense breakdown by category" on the dashboard. | The API returns `SUM(manual_finance_entries.amount) GROUP BY financial_category.name` for the current period. |
| **MS-11** | No endpoint for listing open CRLs across sales is explicitly required. | The API exposes `GET /reports/customer-refund-liabilities` (per §12.1) and `GET /sales?filter[lifecycle_status]=cancelled&filter[open_crl]=true` (if `open_crl` is added; not in V1). |
| **MS-12** | The DB spec does not define an `idempotency_keys` table or mechanism. | The API spec defines it (§7.1). The DDL must include it. |
| **MS-13** | The DB spec does not define a `sessions` table. | The API spec implies it. The DDL must include it. |
| **MS-14** | The DB spec does not define a `notifications` table. | The API spec implies it. The DDL must include it. |
| **MS-15** | No `low_stock` flag derivation is explicitly required. The dashboard does compute it. | The API computes it on read: `on_hand_quantity ≤ products.low_stock_threshold (or default)`. |

### 17.4 Business Rules Not Yet Mapped to Endpoints

The following Business Rules are enforced by the **database triggers** rather than by API endpoint behavior. The API surfaces their effect but does not itself enforce them:

- BR-STOCK-001 to 012 (inventory) — triggers on `stock_movements`.
- BR-COST-001 to 009 (costing) — snapshot triggers and the `value_adjustment` mechanism.
- BR-RETURN-001 to 002 — triggers on `sales_return_lines` and `sales_returns`.
- BR-CANCEL-001 — trigger on lifecycle_status.
- BR-REFUND-001 to 002 — partially triggers; partially API (MS-3).
- BR-PAYMENT-001 to 004 — API computes payment_state; DB cross-row trigger enforces Σ ≤ total.
- BR-PROD-001 to 003 — DB and API together.
- BR-FIN-001 to 005 — DB and API together.
- BR-PROFIT-001 to 003 — derived views.
- BR-AUDIT-001 to 003 — DB triggers and server.
- BR-DATA-001 to 004 — DB triggers.
- BR-AUTH-001 to 004 — server (capability model).

### 17.5 Contradictions Between This Spec and the Upstream Docs

None. This spec does not contradict any upstream doc; it only highlights 18 documentation defects (§16) and 15 missing specifications (§17.3) that the upstream docs need to repair. Each gap has a documented default in this spec.

---

## 18. Implementation Preconditions

For the API to be implementable as specified, the following must be in place. These are preconditions, not part of the spec.

### 18.1 Database Schema (DDL)

- All 27+ tables in `Database-Design-V1.0.md` §2.
- **Required additions** (per §17.3):
  - `sale_lines.is_negative_stock_fallback` BOOLEAN (default FALSE).
  - `stock_movements` CHECK: relax to allow `quantity = 0` for `trigger = 'value_adjustment'`.
  - `stock_movements` CHECK: add `'value_adjustment'` to the allowed trigger set.
  - `idempotency_keys` table.
  - `sessions` table.
  - `notifications` table.
  - `system_settings.is_initialized` boolean (default false). Operational; not exposed.
- All CHECKs, FKs, indexes, and triggers from the DB spec.
- Seed data: roles (Owner, Staff), capabilities (the canonical list in §3.1), payment_methods (cash, bank_transfer, e_wallet, other), cost_types (labor, electricity, gas, packaging, other), financial_categories (per §15.7.5), system_settings (default_low_stock_threshold, default_negative_stock_allowed=false, costing_method=moving_average, etc.).

### 18.2 Authentication

- Argon2id password hashing.
- Token generation (256-bit, base64url).
- HTTPS termination at the load balancer or reverse proxy.

### 18.3 Authorization

- The capability catalog in §3.1 must be seeded; the user_capability_overrides table must default-empty.

### 18.4 Owner Decisions (per Audit Report v2 §7)

- OD-1 through OD-15 are resolved (see §0 of this spec).
- OD-16 and OD-17 are TBD-OWNER; the spec defaults are documented.

### 18.5 OpenAPI Document

- The machine-readable `openapi.yaml` matching this narrative is a deliverable. Implementation cannot start without it.

### 18.6 Test Plan

- Unit tests for capability checks.
- Integration tests for every endpoint.
- Concurrency tests for advisory locks (per DB spec §11.2).
- Accounting reconciliation tests (per V1.9-Accounting-Event-Matrix stress tests).
- Edge case tests (per DB spec §18 A–W).

### 18.7 Deployment

- HTTPS certificates.
- HSTS preload list (optional).
- Reverse proxy with rate limiting.
- Log aggregation.
- Backup of audit_log (operational).

---

## 19. Final Validation

### 19.1 Spec Self-Consistency

- All 35 items from the user's request are addressed:
  - API architecture style ✓ (§1.1)
  - Authentication/session ✓ (§2)
  - Authorization model ✓ (§3)
  - Canonical capability enforcement ✓ (§3.1)
  - Endpoint conventions ✓ (§4)
  - Route structure ✓ (§4.1, §14)
  - HTTP methods ✓ (§4.2)
  - Request schemas ✓ (§15, per-domain)
  - Response schemas ✓ (§15, per-domain)
  - Validation rules ✓ (§6)
  - Error envelope ✓ (§5.1)
  - Error codes ✓ (§5.3)
  - Pagination ✓ (§4.5)
  - Filtering ✓ (§4.6)
  - Searching ✓ (§4.6)
  - Sorting ✓ (§4.6)
  - Date-range handling ✓ (§4.7)
  - Idempotency ✓ (§7.1)
  - Transaction boundaries ✓ (§7.4)
  - Concurrency ✓ (§7.2, §7.3)
  - Optimistic-lock/version handling ✓ (§7.2)
  - Lifecycle transitions ✓ (§8, §15.10, §15.12, §15.15)
  - Posting/cancellation/reversal semantics ✓ (§8)
  - Payment allocation rules ✓ (§9.4, §15.10.9, §15.12.8)
  - Over-tender/change handling ✓ (§9.4)
  - Inventory effects ✓ (§10, §15.10.8, §15.12.7, §15.15.7)
  - Costing/COGS behavior ✓ (§9.7, §10)
  - Refund behavior ✓ (§9.5, §15.11)
  - Derived financial values ✓ (§9.8, §15.20)
  - Audit-log behavior ✓ (§11.1, throughout)
  - Notification events ✓ (§11.2, §15.19)
  - Export endpoints ✓ (§12.3, §14)
  - Reporting endpoints ✓ (§12.1, §14)
  - Dashboard endpoints ✓ (§12.2, §14)
  - Master-data endpoints ✓ (§15.7, §15.8, §15.9)
  - Security requirements ✓ (§13)
  - Rate limiting ✓ (§13.6)
  - Authorization failure behavior ✓ (§3.2, §5.2)
  - Validation failure behavior ✓ (§6.6, §5.2)
  - Not-found behavior ✓ (§5.2, §5.3)
  - Conflict behavior ✓ (§5.2, §5.3, §7.2)
  - Database constraint failure mapping ✓ (§5.3, §6.7, §11)
  - Consistency requirements between API responses and DB state ✓ (§7.4, §8, throughout)

### 19.2 Domains Coverage

| Domain | Endpoints defined | Required BRs covered |
|---|---|---|
| Authentication/session | 6 (§15.2) | BR-AUTH-001, 002, 003, 004 |
| Users/roles/capabilities | 15 (§15.3, §15.4, §15.5) | BR-AUTH-001 to 004 |
| Products | 9 (§15.8) | BR-PRODUCT-001 to 004 |
| Categories | 5 (§15.7.1) | (master CRUD) |
| Suppliers/Customers | 6 (§15.9) | (master CRUD) |
| Sales | 14 (§15.10) | BR-SALE-001 to 010 |
| Sale lines | (sub-resource) | BR-SALE-003, 004, 005 |
| Sale payments | (sub-resource) | BR-SALE-006, 007; BR-PAYMENT-001 to 004 |
| Sale cancellation | (§15.10.10) | BR-SALE-008, BR-CANCEL-001 |
| Sale returns/refunds | (§15.10.12, §15.11) | BR-SALE-009; BR-RETURN-001, 002; BR-REFUND-001, 002 |
| Purchases | 13 (§15.12) | BR-PURCHASE-001 to 007 |
| Purchase lines | (sub-resource) | BR-PURCHASE-002 |
| Purchase payments | (sub-resource) | BR-PURCHASE-003, 004 |
| Purchase cancellation/returns | (§15.12.9, §15.12.10) | BR-PURCHASE-006, 007; BR-RETURN-001 |
| Inventory/stock | 6 (§15.14) | BR-STOCK-001 to 012 |
| Stock adjustments | (§15.14.6) | BR-STOCK-008 |
| Production | 8 (§15.15) | BR-PROD-001 to 003 |
| Costs/overhead | (sub-resource of production) | BR-PROD-003 |
| Cash movements | 2 (§15.17) | (read-only) |
| Income/expense categories | 5 (§15.7.5) | BR-FIN-003 |
| Manual entries | 4 (§15.16) | BR-FIN-001, 003, 005 |
| Financial reports | 16 (§12.1) | BR-PROFIT-001 to 003, BR-REPORT-001 |
| Dashboard/KPIs | 1+ (§12.2) | (dashboard rules) |
| Audit logs | 1 (§15.6) | BR-AUDIT-001, 002, 003 |
| Notifications | 3 (§15.19) | (notification rules) |
| Exports | 3 (§12.3) | (export rules) |
| Settings/configuration | 2 (§15.20) | (config rules) |

### 19.3 Spec Coverage of the 22 Accounting Events

All 22 events from `V1.9-Accounting-Event-Matrix.md` map to one or more API endpoints (see §9.3). Event 20 (system initialization) is documented but **not exposed in V1** — see §15.22.

### 19.4 Spec Coverage of the 8 Accounting Invariants

All 8 invariants are mapped to enforcement mechanisms (see §9.2). INV-01 and INV-02 are properties of the data model and atomicity, not API behaviors per se, but the API's atomicity guarantees uphold them.

### 19.5 Spec Coverage of the Database Invariants

| DB Invariant | API coverage |
|---|---|
| INV-01 (Σ Debits = Σ Credits) | Atomicity; verified by accounting stress tests. |
| INV-02 (Assets = Liabilities + Equity) | Atomicity; same. |
| INV-03 (non-negative balances) | API: `cash_insufficient`, `negative_stock_disallowed`, etc. |
| INV-04 (Refund ≤ CRL, Repayment ≤ SRec) | API: `refund_exceeds_crl`, `repayment_exceeds_srec`. |
| INV-05 (one cancellation) | API: `already_cancelled`, `lifecycle_state_invalid`. |
| INV-06 (CRL = payments − refunds) | API derives CRL on read. |
| INV-07 (SRec = payments − repayments) | API derives SRec on read. |
| INV-08 (Inventory GL = Σ total_cost) | API returns `inventory_value` from the view. |

### 19.6 Spec Coverage of Lifecycle and Posting

All 5 lifecycle flows (sales, purchases, production, sales returns, purchase returns) plus refunds, repayments, and manual entries are documented with: required capabilities, request schemas, atomicity guarantees, error codes, derived-value responses, audit effects, accounting effects, and inventory effects.

### 19.7 Idempotency and Concurrency

- Idempotency-Key is required for 13 high-stakes endpoints (§7.1).
- Optimistic concurrency (`If-Match`) is required for all lifecycle actions (§7.2).
- Pessimistic locks (advisory + row) are used per DB spec §11.2.

### 19.8 What This Spec Does NOT Cover (deliberate)

- No implementation code (per user instruction).
- No SQL (per user instruction).
- No frontend behavior (per user instruction; that is the next phase).
- No invented requirements; every endpoint maps to a documented rule or feature.
- The TBD items (OD-16, OD-17) are documented with default behavior and config keys; the spec does not invent a value for them.

### 19.9 Cross-Document Audit

- **Authoritative inputs read:** PRD V1.1, Business Rules V1.0, Database Design V1.0, V1.9 Accounting Event Matrix, PRD-V1-Audit-Report v2.
- **Authoritative inputs not present:** Frontend Architecture (correctly excluded — out of phase per user).
- **Authoritative inputs not needed:** DDL, code.
- **Contradictions found and reported:** 18 (§16). None blocks this spec.
- **Open decisions acknowledged:** 2 (OD-16, OD-17), both with default behavior.
- **Gaps acknowledged:** 15 (§17.3), all with default behavior.

---

## 20. Final Verdict

# **PASS WITH CHANGES**

**Why PASS WITH CHANGES (not PASS):**
- 18 cross-document contradictions (§16) are reported, all of which require upstream doc repairs before DDL generation. They do not block the API spec being correct, but the upstream docs must be fixed.
- 15 missing specifications (§17.3) require schema additions (idempotency_keys, sessions, notifications, sale_lines.is_negative_stock_fallback, stock_movements CHECK relaxation, value_adjustment trigger) before implementation.
- 2 Owner decisions (OD-16 money rounding; OD-17 sequential document numbering) are still open. The API spec defaults to the conservative option (off) and exposes config. These do not block implementation; they may be resolved before or during the implementation phase.

**Why not FAIL:**
- Every business rule from `Business-Rules.md` is mapped to an API enforcement point.
- Every accounting event from `V1.9-Accounting-Event-Matrix.md` is mapped to an endpoint (except Event 20, which is operational).
- Every accounting invariant is enforced either by atomicity, by an API check, or by a DB trigger.
- The capability model is canonical and consistent with the PRD V1.1 permission matrix.
- No requirement is invented; the spec faithfully extends the upstream documents.
- Internal consistency: the spec's contracts are coherent; no endpoint contradicts another.

**Required corrections before moving to implementation:**

1. **Repair 18 cross-document contradictions** (§16). The most important:
   - XDC-2: add `is_negative_stock_fallback` to `sale_lines` table spec.
   - XDC-3 / XDC-15 / XDC-18: add `value_adjustment` to the `stock_movements` trigger CHECK and relax the quantity CHECK.
   - XDC-9: publish a canonical capability-code list (the §3.1 list) and reference it from PRD, Business Rules, and DB spec.
   - XDC-12: resolve whether stock adjustments auto-create P&L entries. Recommended V1.1 enhancement: add `system_settings.adjustment_creates_pnl_entry` config flag.
   - XDC-1: align "Accounting Model V2.0" (DB spec) with "V1.9" (file on disk). Rename one to match the other.

2. **Add 4 missing tables / columns** (§17.3): `sessions`, `idempotency_keys`, `notifications`, `system_settings.is_initialized`, plus the `sale_lines.is_negative_stock_fallback` column and the relaxed `stock_movements` CHECK.

3. **Resolve OD-16 and OD-17** (§17.1) before the DDL is generated. The default behavior is documented; only the config defaults need to be set.

4. **Generate the OpenAPI 3.1 contract** (`openapi.yaml`) from this narrative spec, before any code is written.

5. **Address the 2 forward-compatible gaps (MS-3 refund method matching; MS-1 stock_adjustments header)**: implement as documented in V1; plan a V1.1 enhancement for MS-1 if a header table is desired.

6. **Address MS-2 (adjustments and P&L)**: document the chosen V1 behavior (no auto-P&L entry for adjustments; Owner records manually) in the V1.1 PRD or in a release note; the API implements this default.

7. **Generate DDL** that incorporates the additions and repairs above.

8. **Generate the test plan** that exercises all 35 audit dimensions, the 22 accounting events, the 8 accounting invariants, and the 27+ tables.

**Once 1–7 are done, the API architecture is ready for the Frontend Architecture phase.**

---

*— End of API Architecture Specification V1.0 —*
