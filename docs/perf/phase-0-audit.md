# POS V2 — Phase 0 Audit & Baseline (clean c9fac35)

Branch `wip/phase1-groundwork` @ `5fca712` protects Phase 1 groundwork.
Baseline measured on: git worktree `Business-POS-System-phase0-baseline`, detached at `c9fac35` (last pre-Phase-1 commit).

- App: Next.js 16.3.4 (production, `next start -p 3003`) + FastAPI (uvicorn 127.0.0.1:8001, DB `pos_dev`)
- **Both** frontend and backend from the `c9fac35` worktree. Backend provenance verified: worktree `backend/app/main.py` byte-identical to `git show c9fac35:backend/app/main.py` (CRLF-normalized); `auth.py` matches c9fac35 (no Phase-1 `Response` import); worktree `git status` clean.
- Headless Chromium, authenticated via localStorage tokens. **One login per run**, token reused across trials (no per-request logins).
- Every metric = **median of 3 trials**.
- Raw dataset: `docs/perf/baseline_clean_1.json` (verified clean run). Legacy `baseline_raw_1.json` / `baseline_slow3g_1.json` / `baseline_dash_login_1.json` are kept for history but are **provisional** — their backend provenance is unverified (a Phase-1 backend may have served them).

## Baseline numbers (verified clean run, median of 3)

### Cold load (fresh context, empty cache) — full navigated `ready` (DOM + post-hydration settle)

| Route | dcl (ms) | ready (ms) | API calls | API bytes | JS chunks | JS total (B) |
|---|---|---|---|---|---|---|
| /customers | 42 | **861** | 2 | 9,778 | 10 | 648,366 |
| /inventory | 30 | **808** | 2 | 8,477 | 10 | 649,027 |
| /sales | 35 | **848** | 3 | 29,916 | 10 | 657,896 |
| /purchases | 36 | **879** | 4 | 50,304 | 10 | 656,527 |
| /pos | 36 | **827** | 5 | 42,919 | 10 | 646,634 |

All API responses **200** in every trial (0 bad trials; `/pos` includes one 307 redirect, as before). JS/API byte totals match the earlier provisional run exactly, so those figures are stable.

### Real same-tab route navigation (sidebar `<a>` click on a warm cache — same tab, same context)

| From → To | median ms |
|---|---|
| dashboard → customers | 134 |
| dashboard → inventory | 126 |
| dashboard → sales | 116 |
| dashboard → purchases | 105 |
| dashboard → pos | 125 |

> **Correction vs the earlier audit:** the old "594–614 ms" nav figures were wrong — they came from a `page.goto()` + a fixed 500 ms settle, not a real in-app click. The corrected harness clicks the actual sidebar `<a>` in the same tab and times to `domcontentloaded`. Real in-app navigation is **~105–134 ms**, because the ~650 KB JS is already browser-cached after the dashboard load. It is still a full `href` navigation (bare `<a>`, no `<Link>`), so state is not preserved and the page fully reloads — but on warm cache the latency is small.

### Browser back (target route → dashboard, same context)

| From | median ms |
|---|---|
| customers → dashboard | 16 |
| inventory → dashboard | 16 |
| sales → dashboard | 16 |
| purchases → dashboard | 17 |
| pos → dashboard | 16 |

Back restores the dashboard from the back/forward cache — near-instant. The earlier "~530 ms back" figure was also a `goto` + settle artifact.

### API per screen (cold, first trial — verified clean run)

| Screen | Endpoints (bytes) |
|---|---|
| /customers | auth/me 1363, contacts?type=customer 8415 |
| /inventory | auth/me 1363, inventory 7114 |
| /sales | auth/me 1363, contacts?per_page=500 13416, sales 15137 |
| /purchases | auth/me 1363, contacts?per_page=500 5311, products?per_page=500 27374, purchases 16256 |
| /pos | auth/me 1363, contacts?per_page=500 13416, products?per_page=500 27374, payment-methods 766 (+1 307) |

Note: `/sales`, `/purchases`, `/pos` each pull **per_page=500** reference lists (contacts/products) on every screen load.

### Slow network (provisional — legacy `baseline_slow3g_1.json`, backend provenance unverified)

| Route | ready (ms) |
|---|---|
| /customers | 10,838 |
| /inventory | 10,818 |
| /sales | 10,830 |
| /purchases | 10,847 |
| /pos | 10,830 |

~13× cold-load cost at 150 ms latency / 200 Kbps. **Mark provisional: re-run against the verified c9fac35 backend before relying on it.**

## Top 10 problems (ranked by impact on the verified numbers)

1. **Cold load is slow (~800–880 ms) because pages are client-rendered with no SSR content.** Data fetched in `useEffect` after hydration; `dcl` is 30–42 ms (empty HTML shell) but `ready` is 808–879 ms. Baseline proof: dcl vs ready gap on every route. Biggest absolute win = cold-path improvement (RSC/hybrid groundwork on `5fca712`).

2. **~650 KB uncompressed JS per route (10 chunks) must download+parse on every cold visit.** 229 KB Next/React chunk + 160 KB + 112 KB shared. Drives the dcl→ready gap. Bundle splitting is Phase 4 scope but it is the largest single cold-load contributor.

3. **per_page=500 bloat on reference endpoints.** `/sales`, `/purchases`, `/pos` refetch large contacts/products lists every visit (13,416 / 5,311+27,374 / 13,416+27,374 B). ~30–50% of each screen's API bytes; also delays `ready` on slow networks.

4. **Route state is not preserved; every navigation refetches.** Because nav uses bare `href` (full page load) there is no client-side state cache; search/filter/scroll are lost on every route change and on back. Not visible in the fast warm-nav timing, but a UX + data-cost issue. `<Link>` + SPA navigation fixes this (Phase 1).

5. **Sidebar uses bare `<a>` tags → full navigation, no prefetch, no SPA transition.** Confirmed: `frontend/components/layout/sidebar.tsx` renders `<a href>` (baseline worktree). Measured warm nav is fast (105–134 ms), so this ranking is **lower than the earlier audit claimed** — the payoff is state preservation + prefetch, not nav latency on warm cache.

6. **Warm cache doesn't help cold-readiness.** Revisiting a route in the same context still refetches every API call; only JS is cached. No memoization/router cache.

7. **Dashboard fetches the same endpoint twice** (current + previous period) plus inventory + sales report = 5 calls on the landing page. Low risk, free to merge.

8. **Report-dashboard fetch is a waterfall around `auth/me`** — several dashboard calls serialize behind the token check on cold load.

9. **Slow network multiplies everything to ~10.8 s (provisional).** No cache headers on responses; slow-path recovery needs caching, not just nav tuning.

10. **One 307 redirect on `/pos` payment-methods** (no trailing slash) adds a round trip; trivial.

## Plan item status

| Plan item | Needed? | Status |
|---|---|---|
| One shared AppShell in layout | **Already done** | AppShell shared in `(protected)/layout.tsx` |
| `<Link>` sidebar nav | **Needed** | P0 — bare `<a>` today |
| Hover/intent prefetch (sidebar main) | **Needed** | None today (no `<Link>`) |
| Disable prefetch on large tables/lists | **Needed** | N/A yet (no prefetch anything) |
| `loading.tsx` / Suspense boundaries | **Needed** | Only `(protected)/loading.tsx` exists |
| `cacheComponents: true` | **Defer** | Not verified against 16.3.4; Phase 2/3 decision |
| `experimental.staleTimes` | **Do not use** | Version-specific check |
| Route state preservation (cache/activity) | **Needed** | Phase 2/3; low risk today (fresh fetch) |
| Bundle optimization | **Needed** | ~650 KB/route; Phase 4 |

*See `POS_V2_PERFORMANCE_PLAN.md` for phase gates and target behavior.*

## Gate 0 self-review

### Tester pass (reproduction)
A verified clean run was completed this time: `baseline_clean_1.json`, both frontend + backend confirmed at `c9fac35`, one login, 3 trials per route, **0 bad trials, all API 200**. Cold-ready medians reproduce the earlier provisional run within noise (customers 861 vs 815, inventory 808 vs 779 — +5 to +9%, plausible for the same machine, different settle windows), and JS/API bytes are byte-identical. The earlier failure modes did not recur: no asyncio error (login once, no DB minting), no expired token (fresh login per run), no rate-limit trips (single login).

### Skeptic pass — what changed vs the earlier audit
- **Navigation timing was wrong before, now corrected.** The old 594–614 ms / 530 ms figures came from a `goto`-with-settle harness, not real clicks. Real same-tab nav is 105–134 ms; back is ~16 ms. This materially lowers problem #5's rank: **in-app navigation is already fast on warm cache**. The compelling remaining wins are cold load (#1), bundle size (#2), and state preservation (#4).
- **Cold load is the honest bottleneck.** ~800–880 ms ready vs 30–42 ms dcl proves the client-rendering cost; that is where Phase 1's RSC/hybrid groundwork pays off.
- **What is still NOT measured:** slow-network against the verified c9fac35 backend (provisional numbers only); interaction latency inside pages; memory footprint. Flagged, not hidden.

### Verdict
Verified clean baseline at `c9fac35` for frontend + backend (provenance checked), 0 bad trials, one reusable login. **Gate 0 is ready for review.** Phase 1 (on `5fca712`) should re-run the same harness for a before/after comparison.
