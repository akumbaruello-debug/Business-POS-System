# POS V2 — Performance & Navigation Plan

**For:** Hermes room (Hermes = lead/implementer, Cassandra = skeptic, Prob = tester)
**Stack:** Next.js 16 (App Router) + FastAPI + PostgreSQL
**Goal:** V2 should feel like a desktop app. No big spinners between pages. Pages you already visited come back instantly with their state.

---

## 0. How this room works

Read this first. These rules apply to every phase.

1. **Work in phases, in order.** Do not start a phase until the previous gate is passed.
2. **Audit before changing.** Phase 0 changes no code. It only reads and measures.
3. **Verify before trusting.** This plan was written from research notes. Version-specific claims (Cache Components, Activity, `use cache`, `cacheLife`, `updateTag`, the "3 preserved routes" limit, "Instant Navigations") must be **checked against the Next.js version actually installed in the repo and the official docs for that version**. If something differs, say so and adapt. Do not assume the plan is right.
4. **Measure, don't guess.** Every phase ends with before/after numbers.
5. **One change set per phase**, small and reviewable. Commit per phase on its own branch (e.g. `perf/v2-phase-1-nav`).
6. **Plain reports.** Short, direct, no jargon dumps.

### Roles

| Bot | Job |
|---|---|
| **Hermes** | Does the audit and implementation. Writes the phase report. |
| **Cassandra** | Challenges each decision *before* it is implemented and each result *after*. Looks for wrong assumptions, risky caching, over-engineering. Must sign off at every gate. |
| **Prob** | Runs the measurements and the behavior tests. Reports numbers, not opinions. Must sign off at every gate. |

Any bot may pass on a step it has nothing to add to. Say "pass" and why in one line.

### Gate rule

A phase is done only when:
- Hermes: work finished + report written
- Cassandra: "no blocking concerns" (or concerns resolved)
- Prob: measurements recorded + tests pass

If Cassandra and Prob disagree, Ello decides.

### Report format (every phase)

```
## Phase N report
- What was checked / changed:
- Files touched:
- Numbers (before → after):
- Risks / open questions:
- Cassandra sign-off:
- Prob sign-off:
```

Save reports to `docs/perf/phase-N-report.md`.

---

## 1. Target behavior

```
OPEN APP      → app shell shows immediately → content streams in
CLICK PAGE    → destination already prefetched → instant switch, only fresh data loads
GO BACK       → previous page returns with its state (search, scroll, filters)
```

### Product targets (stricter than Google's "good")

| Metric | Target |
|---|---|
| App shell visible | < 500 ms |
| Main content visible | < 1.0 s |
| Interactive | < 1.5 s |
| Prefetched route click | feels immediate |
| Dynamic route click | shell immediately, data streams |
| Return to recent route | no full-screen loader, state kept |

Reference only (Google "good"): LCP ≤ 2.5 s, INP ≤ 200 ms, CLS ≤ 0.1.

### Two rules for the dev standards

- A page must never make the user wait for data that is not needed to show the page structure.
- A page already visited must not be rebuilt from zero, unless its cache was intentionally invalidated or evicted.

---

## 2. Hard safety rule: POS data must never be stale-wrong

Wrong stock or wrong cash is worse than a 200 ms slower page.

- Cache only by **business invalidation**, never "cache everything for N minutes".
- Every cached read needs a matching mutation that invalidates it (tags).
- If unsure whether something is safe to cache → **do not cache it**, flag it to Cassandra.

Data categories (Phase 3 maps every endpoint into one):

| Category | Examples | Policy |
|---|---|---|
| Static | units, categories, payment methods, financial categories, company settings, UI config | long-lived cache |
| Semi-dynamic | product list, customer list | cached + tag invalidation on mutation |
| Highly dynamic | current stock, today's cash, payment/sale/purchase status | fresh or streamed, no stale cache |
| User-specific | per-user data, permissions | dynamic or tightly scoped cache |

---

## Phase 0 — Audit only (NO code changes)

**Owner:** Hermes · **Review:** Cassandra · **Measure:** Prob

### 0.1 Inspect and document
- [ ] `next.config.*` (current flags, `cacheComponents`, any `experimental.staleTimes`)
- [ ] Installed Next.js / React versions (exact)
- [ ] Root layout and every nested layout
- [ ] `AppShell`: is it shared across routes, or does each page render its own?
- [ ] Every file with `"use client"` — list them, mark which are whole pages vs small interactive pieces
- [ ] Navigation code: find every `window.location.href`, `router.push`, `<a>` used for internal links
- [ ] Providers (auth, theme, query, etc.): how many, how heavy, where mounted
- [ ] Data fetching: where each page fetches, and whether requests run in sequence (waterfalls)
- [ ] Existing `loading.tsx` and `<Suspense>` boundaries
- [ ] Fonts, images
- [ ] Biggest dependencies (run a bundle analyzer)
- [ ] FastAPI: which endpoints each main screen calls, response sizes, anything returning "everything"

### 0.2 Baseline measurements (Prob)
Measure and record for the main pages (POS, Sales, Customers, Inventory, Purchases, Payments, Finance, Reports, Settings):
- [ ] Initial load, cold browser
- [ ] Initial load, warm browser
- [ ] Route navigation (A → B)
- [ ] Repeat navigation (B → A)
- [ ] Back/forward navigation
- [ ] Slow network (throttled)
- [ ] Initial JS size per route
- [ ] Number and size of API calls per screen

### 0.3 Deliverable
`docs/perf/phase-0-audit.md` containing:
1. Findings per area above
2. Baseline numbers table
3. **Top 10 problems ranked by impact**
4. Which plan items below are **needed / not needed / already done**

**Gate 0:** Cassandra checks the audit is honest (no guessing). Prob confirms the baseline is repeatable. **Ello approves the ranked list before Phase 1.**

---

## Phase 1 — Navigation foundation

**Goal:** Dashboard → Customers → Inventory → Finance → Customers feels like switching views in a desktop app.

- [ ] One permanent shared `AppShell` (sidebar + header) in a shared layout. No page-specific shells.
- [ ] All internal navigation uses Next `<Link>` / router. **No `window.location.href` for normal navigation.**
- [ ] **P0: Convert sidebar `<a>` tags to `<Link>`** — `components/layout/sidebar.tsx` currently uses bare `<a href={item.href}>` for all 55 nav items. This triggers full browser navigation with no prefetch. Convert all sidebar nav items to `next/link`'s `<Link>`.
- [ ] One `window.location.href` found in `app/(protected)/users/_client.tsx` (logout redirect) — intentional, keep as-is.
- [ ] Confirm sidebar links are prefetching in production build (not dev).
- [ ] Add intentional hover/intent prefetch for the main sidebar destinations only (POS, Sales, Customers, Products/Inventory, Purchases, Payments, Finance, Reports, Settings).
- [ ] **Do not** aggressively prefetch large table rows or long lists. Disable prefetch on those links.
- [ ] Add `loading.tsx` / `<Suspense>` boundaries so only the data part shows a skeleton, never the whole page.
- [ ] Enable `cacheComponents: true` **only if** Phase 0 confirms the installed version supports it and Cassandra agrees on the migration cost. Document what breaks.

**Cassandra must challenge:** Does enabling Cache Components force rewrites of existing data fetching? Is it worth it now?

**Prob checks:** Navigation timings vs baseline (production build). Sidebar does not remount between routes (e.g. sidebar scroll position / open state survives).

**Gate 1:** Report with before/after navigation numbers.

---

## Phase 2 — Instant shell + route state preservation

**State preservation requirement:** State (search, filters, scroll position) MUST be URL-based for reliability across page reloads, duplicate tabs, and back/forward navigation. Client-side React state alone is insufficient for a true desktop app experience.

- [ ] For each main page, split into **static shell** (header, actions, filters, table structure) and **dynamic content** (rows, stock values) wrapped in `<Suspense>`.
- [ ] Remove any page-wide `if (!data) return <FullScreenSpinner />`.
- [ ] If Cache Components is on: confirm recently visited routes keep their state (Activity behavior). Check the actual preserved-route limit in the installed version — the research notes said 3, **verify**.
- [ ] Return-to-route test: Customers → search "john" → scroll down → open Inventory → return to Customers.

**Expected:** same search text, same scroll, same filters, no blank reset.

**Prob checks:** Run that exact test on at least Customers and Inventory. Record pass/fail per page. Also check what happens after visiting 4+ other routes (eviction) — it should degrade gracefully, not break.

**Cassandra checks:** Is any preserved page showing outdated business data (stock, cash) after returning? Preserved UI state is fine; stale numbers are not.

**Gate 2:** Pass/fail table per page.

---

## Phase 3 — Data cache architecture

- [ ] List every FastAPI endpoint and every data read in the frontend.
- [ ] Map each to a category from section 2 (Static / Semi-dynamic / Highly dynamic / User-specific). Save as `docs/perf/cache-map.md`.
- [ ] Implement caching per category using the installed Next version's supported APIs (`use cache`, `cacheLife`, tags).
- [ ] For every cached read, define its invalidation: which mutation triggers which tag (`updateTag` / `revalidateTag` or the version's equivalent).
- [ ] Do **not** use `experimental.staleTimes` as the main solution (documented as experimental, not recommended for production — verify).
- [ ] Replace `unstable_cache` usage if present and if the installed version supports the replacement.

**Cassandra must review `cache-map.md` line by line** and try to break it:
- "User changes stock → can any screen still show the old number?"
- "Payment recorded → can cash/finance still show old totals?"
- "Sale voided → what updates?"

**Prob checks:** For each mutation, verify the affected screens show fresh data on next view. Use a written test list in `docs/perf/phase-3-tests.md`.

**Gate 3:** No stale-data failures. Cassandra explicitly signs the cache map.

---

## Phase 4 — Bundle optimization

**Target:** Initial JS bundle < 500KB gzipped (current: ~2.0MB total .next/static)

- [ ] Run the bundle analyzer; list reasons the initial JS is large.
- [ ] Move logic from Client Components to Server Components where possible.
- [ ] Target shape: server = pages, layouts, data fetching, table markup, static UI, formatting. Client = search input, dropdowns, modals, interactive row actions, live state.
- [ ] Break up any "whole page is `use client`" file.
- [ ] Lazy-load heavy features so they are **not** in the initial bundle: charts, PDF viewers, CSV/XLSX tools, rich text editor, large date libs, advanced data grids, image editors.
- [ ] Remove duplicate libraries; reduce providers and client state.

**Prob checks:** Initial JS per route before/after. Dashboard must not load the Reports chart engine.

**Gate 4:** JS size table, no functional regressions (Prob runs main user flows).

---

## Phase 5 — API optimization (FastAPI)

- [ ] For each major screen record: number of requests, request size, TTFB, DB time, serialization time.
- [ ] Split "return everything" endpoints into small ones, e.g. list (paginated) vs detail vs movements/history.
- [ ] List endpoints return only what the list shows.
- [ ] Remove request waterfalls: **request B never waits for request A unless B truly depends on A.** Run independent calls in parallel (auth/settings/permissions/dashboard/notifications).
- [ ] Check DB queries behind slow endpoints (missing indexes, N+1).

**Cassandra checks:** Did splitting endpoints break any existing consumer? Are the new endpoints still correct under pagination/filters?

**Prob checks:** Per-screen request count and size before/after; API contract still passes.

**Gate 5:** Per-screen API table.

---

## Phase 1.5 — Server component migration (PREREQUISITE for Phase 2 state preservation)

**Status:** ✅ COMPLETE (auth infrastructure) ⏸️ PAUSED (page conversion strategy needed)

**Completed:**
- ✅ Backend: `auth.py` sets httpOnly cookies on login/refresh (`secure=is_production`)
- ✅ Backend: `logout` and `logout_all` clear cookies on server response
- ✅ Backend: `deps.py` `current_principal` reads both Bearer header AND cookie
- ✅ Frontend: `ProtectedLayout` is now a server component
- ✅ Frontend: `server-api-client.ts` forwards cookies via `next/headers`
- ✅ Frontend: `AppShell` provides `SessionContext` from server-resolved user

**Implementation approach (verified):**
1. ProtectedLayout (server component) reads `access_token` cookie via `next/headers`
2. Server API client forwards cookie to `/auth/me`
3. On success, passes `user` prop to client `AppShell`
4. `AppShell` wraps children in `SessionContext.Provider`
5. Page components continue using `useSession()` hook (from client-side localStorage)

**Security:** httpOnly cookies prevent XSS token theft. Client components still read localStorage for backwards compatibility with existing code.

**Page conversion analysis (22 page.tsx files):**
- **POS page**: Keep as client component - complex checkout workflow with real-time state transitions (build → tender → done)
- **Dashboard**: Read-only dashboard, ideal candidate for RSC + Suspense
- **Customers, Inventory, Sales, etc.**: Read-heavy list pages, good candidates for RSC

**Strategy options for Phase 2:**
1. **Full RSC conversion**: All pages become server components, data fetched in component scope
2. **Hybrid**: Core data as RSC, interactive elements as client components with `use` hooks
3. **Lazy adoption**: Only convert when state preservation needed

**Gate 1.5:** @user — approve page conversion strategy for Phase 2 state preservation.

---

## Phase 6 — Browser caching / bfcache

- [~] Remove `unload` listeners; use `pagehide` / `visibilitychange` instead. (NO unload listeners found in app code — only in Next.js framework internals.)
- [~] Remove `window.scrollTo(0,0)` effects that break bfcache scroll restoration. (NOT PRESENT — Probe confirmed zero `scrollTo` in app source.)
- [ ] Test back / forward / returning to previous route / duplicate tab / refresh / new tab.
- [ ] Use the browser's `notRestoredReasons` info to find what blocks bfcache.
- [ ] Note: separate Chrome tabs are separate pages and do not share in-memory React state. Only HTTP cache and bfcache help there. Set expectations accordingly.

**Gate 6:** Table of scenarios with pass/fail and blocker reasons.

---

## Phase 7 — Advanced speculation (optional, only if still needed)

Only start if Phases 1–6 are done and numbers still miss targets.

- [ ] High-confidence prerender for a few very predictable destinations using Speculation Rules.
- [ ] Measure CPU/network cost. Drop it if it wastes more than it saves.

**Cassandra must justify** that it is actually needed before it is built.

---

## 3. Final verification (after the last phase)

Prob runs the full set against the targets in section 1 and fills this table:

| Check | Target | Before | After | Pass |
|---|---|---|---|---|
| App shell visible | < 500 ms | | | |
| Main content visible | < 1.0 s | | | |
| Interactive | < 1.5 s | | | |
| Prefetched route click | immediate | | | |
| Return to recent route keeps state | yes | | | |
| No full-screen loader on revisit | yes | | | |
| Stock/cash always fresh after mutations | yes | | | |

Cassandra writes a final "what could still go wrong" list (max 10 lines).

---

## 4. Start here

**Hermes:** begin Phase 0 now. Change nothing. Post `docs/perf/phase-0-audit.md` to the room when done.
**Cassandra:** review the audit for guesses and missing areas.
**Prob:** record the baseline numbers using a production build.
**All:** stop at Gate 0 and wait for Ello's approval of the ranked problem list.
