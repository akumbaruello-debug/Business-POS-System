# Business-POS-System — V3 Product & Engineering Plan

**Status:** Draft plan for staged implementation  
**Purpose:** Working context for Hermes/OpenCode and the human owner. This is a plan, not authorization to modify production.  
**Priority rule:** Verify actual repository/database state before relying on any historical report in this document.

---

## 1. Goal

Evolve Business-POS-System safely for the fish trading workflow described by the business tester, while preserving the current V1 workflows, UI patterns, accounting invariants, and real transaction history. V3 is a product roadmap label; it does **not** require a Git branch literally named `v3`.

The plan must support:

- Products differentiated by configurable **Jenis + Size + Kualitas**.
- Stock and inventory valuation per resulting SKU.
- Configurable units (including kg, kuintal, ton, and optionally buah) with safe conversion rules.
- Owner-controlled purchase and selling price defaults, with granular permission for delegated staff.
- HPP/landed-cost allocation that is configurable, defaults to weight-based allocation where applicable, and supports permitted overrides.
- Mixed/unsorted receiving and sorting into product grades, including explicit stock loss or remaining unsorted weight.
- Existing DP, partial payments, and settlements for sales and purchases.
- Sales and purchase returns with inspection and multiple settlement options.
- Editable starter templates for settings so the owner does not need to create all master data from scratch.
- Simple, detailed page-level help for users, without dead buttons or a UI redesign.
- A safe, verified route from development to production.

## 2. Non-negotiable safety and product rules

1. **No destructive database operations.** Never reset, reseed, drop, truncate, or destructively modify production. The development database `pos_dev` is also protected: do not reset/reseed/destructively modify it. Test fixtures that recreate schemas may only run against the isolated test database `pos_test` after verifying the connection target.
2. **No direct production data cleanup with ad hoc SQL.** Suspected test transactions must be reconciled and handled through an audited, approved application workflow or a specifically reviewed migration. Never silently delete posted financial records.
3. **No silent historical recalculation.** Posted transaction prices, cost snapshots, payment allocations, stock movements, and accounting events remain historical facts. New configuration applies to future/draft work unless a reviewed migration explicitly says otherwise and the owner approves it.
4. **No redesign.** Preserve the established V1 UI and business workflows. Reuse existing Settings pages, APIs, permissions, components, and ledger logic where suitable. Do not resurrect old CSS or create a V0/V1 hybrid.
5. **No duplicate features.** Audit current functionality before adding an alternative. Extend partial payments, Units, Cost Types, product master data, permissions, and returns where appropriate; do not rebuild them from scratch without evidence that the existing implementation is unsuitable.
6. **No placeholders/dead controls.** Every new action must work, have appropriate permission checks, be tested, and show understandable success/error feedback.
7. **No commit or deploy without explicit user approval.** Do not push, open a PR, deploy, change production environment variables, or apply production migrations without explicit authorization.
8. **Small, evidence-led steps.** Workflow: bounded audit → written decision → one focused implementation change → focused tests → verify result → report. No endless audits and no assumptions presented as facts.
9. **Protect credentials.** Never put secrets, tokens, environment values, personal/customer details, or live database credentials in the plan, logs, or prompt output.

## 3. How to use this plan with an AI coding agent

At the start of every new agent conversation, the agent must:

1. Read this entire `V3-PLAN.md` and the template README.
2. Inspect `git status`, current branch/commit, repository root, and relevant worktree before editing. Do not infer the working directory or branch from an old chat.
3. State the current phase, what is already evidenced in code/tests, what remains unknown, and the single next task proposed.
4. Do only the specifically authorized task. Do not implement the whole roadmap in one pass.
5. Before editing a schema, API, or UI, find the existing model, service, route, permission/capability, tests, and relevant business rule. Keep the audit limited to the task.
6. After the task, report exact changed files, migrations, tests and commands run, pass/fail outcomes, risks, and the next phase. Do not claim a test passed unless it actually ran.
7. Stop before commits, pushes, PRs, deployments, or production data changes unless specifically approved.

### Suggested response format for the agent

- **Current phase / status:**
- **Evidence inspected:** paths, functions, tests, commits (with concrete results)
- **Decision needed:** only if a material business or architecture choice is unresolved
- **One next task:**
- **Safety impact:** data, migrations, permissions, accounting, production
- **Tests and acceptance criteria:**

## 4. Known context and issues to verify

The entries below come from prior project discussions and code inspection. They are not a replacement for checking the current checkout, production configuration, or database state. Treat anything marked **verify** as an open investigation, not a confirmed diagnosis.

### 4.1 Existing V1 capabilities to preserve

- V1 business rules describe multiple/split payment allocations and derive paid/partial/unpaid state from payment totals.
- Default inventory costing is Moving Average; it is configurable for future postings. A sale captures a cost snapshot.
- Purchase shipping/transport is currently part of acquisition cost and is currently distributed proportionally to line value. It must not also be counted as a second manual operating expense.
- Product purchase/selling prices are master/reference prices and are distinct from inventory unit cost/HPP.
- Posted costs/transactions are frozen historically.
- Existing settings include **Settings → Units** and **Settings → Cost Types**. The current Units feature was inspected as code/name/active status only; conversion factors were not present. The current Cost Types feature was tied to production cost lines and name/active status, not a complete landed-cost allocation system.
- Existing granular capabilities were observed for unit and cost-type management. Reuse the permission pattern and audit exact capabilities before adding new ones.
- An earlier report said Help implementation was present, but a later inspection found the `v2-help-feature` worktree clean at `c9fac352`, with no Help implementation diff/files. Verify the actual current checkout; do not assume Help already exists.
- Performance work was previously committed as `9b43f867` (GZip response compression and POS payment-method endpoint slash fix). Do not repeat this work unless a current regression is evidenced.

### 4.2 Production/deployment issues requiring separate verification

**A. Preview login differs from the canonical production deployment — unresolved.** The user reported a failure at `business-pos-system-frontend-44n8vgpxm.vercel.app/login` while the canonical `business-pos-system-frontend.vercel.app/dashboard` worked. Compare the exact deployment commit and preview vs production environment configuration, API URL, auth/Supabase project reference, cookie/redirect settings, and CORS behavior. Do not assume the cause before logs and network responses support it. Never expose secret values in output.

**B. Product save showed `Failed to fetch` despite the update being committed — unresolved.** An earlier production audit found a product price update persisted in the database and an audit record existed, despite the frontend error. Investigate the request/response lifecycle, client error handling, backend response, and any post-commit/network failure. Do not blindly repeat a price update or tell users save failed if the server already committed it. Make retries safe where appropriate.

**C. Suspected production test data needs owner-approved reconciliation.** A prior test was reported to have created a large `Capital Injection` manual finance entry (Rp999,999,999) and a purchase with Rp3,000,000 freight, including stock/ledger effects. Treat these records as suspected test data, not automatic deletion candidates. Verify current records and all related cash movements, stock, payables, journal/financial reporting effects, and get the owner's explicit approval before any cleanup or reversal. Use an auditable correction flow; no direct SQL deletion.

**D. Production schema/runtime mismatch — verify.** A prior backend error mentioned missing `supplier_repayments.purchase_return_id`. Check current migrations, deployed migration state, and relevant API usage. Fix through a reviewed, forward-compatible migration tested in isolation; do not hot-edit production schema without review and approval.

**E. Accounting classification — verify.** A prior concern was that capital-injection/manual finance entries might be included in an income/P&L query. Review event classification and reports so capital/equity funding is not misclassified as operating revenue. Confirm intended accounting behavior against approved business rules before changing queries.

**F. Production readiness/data hygiene.** Before real operational use, perform a read-only production readiness review and owner-approved reconciliation of test data. Export/backup and capture an evidence trail before any approved corrective action. Do not block the feature plan on unverified guesses; track each item with observed evidence and an explicit disposition.

## 5. Confirmed business requirements and recommended policies

Use labels as follows: **Confirmed** = user explicitly agreed; **Recommended** = suggested design to validate against the current implementation; **Verify** = requires a narrow audit or owner decision.

### 5.1 Product identity and custom attributes

- **Confirmed:** each **Jenis + Size + Kualitas** combination is a distinct SKU/product with its own quantity, inventory valuation/HPP, purchase price, and selling price.
- **Confirmed:** PP and Mco are examples, not a closed list. Owner should be able to maintain their own values/categories through Settings; staff can do so only when authorized.
- **Recommended:** model Jenis, Size, and Kualitas as managed master values and link a product/SKU to the selected values. Enforce uniqueness for the combination so accidental duplicates cannot be created.
- **Recommended:** allow unused user-created values to be deleted; if a value is referenced by products or historical transactions, block hard deletion and offer deactivation/archive. Existing transactions must keep displaying the original meaningful label/history.
- **Verify:** whether current product fields already model these dimensions and how much migration/backfill is required. Do not assume every existing product can be mapped automatically.

### 5.2 Units and quantities

- **Confirmed/recommended standard conversions:** 1 kg = 1 kg; 1 kuintal = 100 kg; 1 ton = 1,000 kg.
- `buah`/`pcs` is a count unit, not a weight unit. Never convert buah to kg unless the product has an explicit, approved product-specific conversion rule. Units from different dimensions must not be added together.
- **Recommended:** extend existing Settings → Units rather than making a second Units menu. Store conversion dimension and factor. For mass products, use kg as the canonical stock unit and display/accept kg, kuintal, or ton as needed. Preserve transaction input units for display/audit while storing normalized quantities safely.
- Conversion changes must not rewrite historical transaction quantities. Existing units that are used cannot be hard-deleted; they can be deactivated. Unused entries can be deleted.
- Validate decimal precision, rounding, large quantities, and mixed-unit entry. Define precision/tolerance with existing business behavior before implementation.

### 5.3 Purchase price, selling price, and HPP

- **Confirmed:** Owner controls default pricing; staff may edit only when granted appropriate permission.
- **Recommended:** support a base price per kg per SKU, with automatically derived kuintal/ton display prices. Support a deliberate per-unit override when the Owner wants, for example, a non-linear special price by ton. Purchase and selling prices are separate.
- If base price changes, recompute derived non-overridden unit prices. Preserve explicit overrides until the Owner changes/removes them. Make derived vs overridden status visible to authorized users and audit price changes.
- Capture the applicable price in each posted transaction; historical sales/purchases must not change if a master price later changes.
- Keep **purchase reference price**, **selling reference price**, and **actual inventory HPP** conceptually separate. HPP must incorporate purchase acquisition cost according to the configured costing policy; do not use a master reference price as an unverified replacement for moving-average inventory cost.

### 5.4 Purchase cost / landed cost allocation

- **Confirmed/recommended:** automatic allocation by weight (kg equivalent) as a configurable default for suitable purchase costs, with permitted transaction-level overrides.
- Preserve existing V1 default behavior (allocation proportionally by line value) until a reviewed settings/migration decision changes the default. Do not silently switch behavior for historic or already-created purchases.
- **Recommended allocation modes:** weight/kg equivalent, line value/proportion, equal per eligible line (only if useful to the business), and manual per-line allocation. Owner chooses default; individual override is permission-controlled and audited.
- Each purchase cost category must say whether the cost is eligible to be capitalized into inventory cost or treated as an operating expense. Never assume all “biaya operasional” is inventory cost. Avoid double-counting a cost as both landed cost and operating expense.
- Freeze the allocation and cost snapshots when a purchase is posted. Reversal/correction should use supported accounting events, not silently recalculate the posted document.
- Existing **Cost Types** is known to be used for production-cost records. Audit reuse carefully. Keep production Cost Types separate from purchase landed-cost categories unless the existing model can represent both scopes cleanly without breaking reports.

### 5.5 Receiving and sorting

- **Confirmed:** some purchased goods arrive already classified; others arrive mixed and must be sorted later.
- **Recommended:** already-classified purchases can be received directly into the appropriate SKU. Mixed deliveries must be recorded as pending/unclassified inventory and must not be sold as a classified SKU before sorting.
- Prefer a receipt/lot classification state over inventing a fake sellable SKU, if the existing inventory architecture supports this safely. Choose the least disruptive model after a bounded schema/service audit.
- A sort operation records input lot, output SKU/grade, output quantity, operator, time, and audit trail. One input can result in multiple grades/SKUs; one result may be loss/waste and another may remain unsorted.
- Reconcile exactly at persisted quantity precision: input quantity = classified outputs + explicit recorded loss/waste + remaining unsorted quantity. Tolerance is zero; never round away, absorb, or auto-correct a discrepancy.
- Loss/waste is a stock movement with a reason; it is not a normal sellable product by default. Optional custom grades can be configured independently.
- Approved Phase 4 valuation: allocate the source inventory carrying value proportionally by base weight across classified outputs, recorded loss, and remaining unsorted quantity. Use deterministic cent rounding so allocations reconcile to the source value. Recognize loss once as an expense and inventory reduction. Exclude non-capitalizable purchase expenses and use neither selling prices nor reference prices.
- Convert only configured mass units and snapshot conversion details at receipt and sorting time. Reject count units for weight-based reconciliation.
- One sorting event consumes one source receipt. A source may have multiple events; events cannot combine receipts.

### 5.6 DP, partial payments, and settlements

- **Confirmed:** purchases and sales support down payment (DP), subsequent partial payments, and settlement. Example: total Rp10,000,000; DP Rp3,000,000; later payment Rp2,000,000; outstanding Rp5,000,000.
- Existing V1 partial payment/allocation behavior must be inspected and reused. Test that status, outstanding amount, payment allocation, cash/bank movements, supplier/customer balances, and reversals remain consistent.
- A failed frontend response after a committed payment must not invite a duplicate payment. Retry/idempotency behavior must be safe and the UI should verify server state where appropriate.
- **Verify:** existing behavior for overpayment, payment reversal, void/cancel, refund, and multiple payment methods before changing code.

### 5.7 Sales returns and purchase returns

**Sales return — confirmed policy direction:**

- Returned fish enters quarantine/inspection; it is not immediately sellable stock.
- Inspection may release acceptable stock, reclassify it to a lower/custom grade (e.g. PP → Mco), split quantities into multiple outcomes, or record waste/afkir.
- Example only: a 10 kg return may result in 3 kg accepted as Mco and 7 kg waste. The product must support partial outcomes and quantity reconciliation.
- Keep the original sale and payment history; link a return to the original sale and correctly record stock, refund/customer credit, and financial/accounting effects. Guard against duplicate returns and double-restock.

**Purchase return — confirmed:**

- The business may return goods to the supplier or settle a quality issue in another way.
- Support appropriate documented outcomes such as physical return, supplier cash/bank refund, credit against supplier payable, replacement goods, or negotiated discount/credit. A single issue may combine outcomes when the actual supplier agreement does so.
- Link every settlement to the source purchase/return, maintain stock and payable/refund integrity, and preserve an audit trail. Do not create duplicate credits, payments, or stock receipts.
- Audit the existing purchase-return flow and the reported `supplier_repayments.purchase_return_id` schema issue before implementation.

### 5.8 Permissions and change history

- Reuse existing granular capabilities and UI patterns. Audit before introducing any new capability names.
- Owner controls master data, default price policies, cost-allocation defaults, tolerance settings, and sensitive stock adjustments by default. Staff can act only when explicitly granted the relevant permission.
- Permissions must be enforced server-side, not just by hiding a button.
- Audit price changes, master data changes, overrides, sorting, waste, return decisions, and manual stock/cost corrections with actor, timestamp, before/after values, and reason where appropriate.

### 5.9 Page-level Help

- **Previously requested:** a Help button on every page that explains each page's purpose and workflow in detailed but simple, user-friendly language.
- Preserve the current page layout; integrate with the existing shared layout/header if available. Help content should be page-aware, actionable, and consistent with current permissions and real features.
- Do not write Help content for controls/features that do not exist. Do not claim the feature is present until verified in the current source and browser.
- Include tests for route coverage, language/content coverage where localization applies, and basic browser behavior. Avoid a huge heavy client bundle if a lightweight existing UI pattern can do the job.

## 6. Starter data templates

Starter CSVs are included next to this plan in `templates/`. They are **editable examples**, not proof of a current CSV importer. The agent must inspect the current API/import capability and either map the templates to a safe preview-first importer or document an owner-operated/manual entry process. Never bulk-insert starter rows into production without preview, duplicate checks, owner confirmation, and explicit approval.

Templates supplied:

- `units.csv`: kg, kuintal, ton, and buah/pcs. Count units are a different dimension from mass units.
- `production_cost_types.csv`: starter examples for production Cost Types aligned to the existing Settings → Cost Types concept.
- `purchase_landed_cost_types.csv`: separate starter examples for purchase acquisition costs; default capitalization values are suggestions to review, not universal accounting rules.
- `product_attribute_values.csv`: sample custom Jenis/Size/Kualitas values only. Example values are not a mandatory product catalog.

Data lifecycle policy:

- Show a preview and validation errors before any import/apply action.
- Make import repeat-safe/idempotent (no duplicates when the same file is applied twice).
- Allow user-created/unreferenced values to be deleted.
- If referenced by products/transactions/accounting records, prevent hard deletion and allow deactivation/archive with historical labels retained.
- Do not create all possible combinations of Jenis × Size × Kualitas automatically. Create the SKU combinations that the business actually stocks, unless the owner explicitly requests a bulk-create workflow.
- The owner must be able to edit or remove starter defaults before importing them.

## 7. Phased delivery plan

Every phase starts with a short bounded audit of only the relevant paths and ends with documented evidence. A phase does not authorize later phases automatically; after reporting, propose the next single task.

### Phase 0 — Establish the actual baseline and safety boundary

**Purpose:** prevent work on the wrong checkout or mistaken historical assumptions.

Tasks:
- Confirm repository root, current branch/commit, `git status`, worktrees, and exact frontend/backend directories.
- Verify the current main/V1 code and available tests. Record actual current state of Help, units, cost types, price editing, purchase/shipping allocation, sorting, partial payments, returns, and permissions.
- Identify existing migrations and ensure test fixtures target only `pos_test`; never reset `pos_dev`.
- Make a concise evidence table: existing, partial, missing, regression/unknown.

Acceptance:
- No files/records changed.
- Current checkout and protected databases are explicitly identified.
- Only one prioritized next task is recommended.

### Phase 1 — Triage production reliability and data-risk issues

**Purpose:** investigate the known production-risk reports independently of new feature work.

Tasks, one at a time:
1. Compare failing preview login with successful canonical production login (deployment commit/config/API/auth behavior), without disclosing secrets or changing production settings.
2. Reproduce and diagnose product-save `Failed to fetch` vs persisted update; make UI feedback/retry behavior safe against duplicate writes.
3. Read-only reconcile suspected test finance/purchase entries, ledger and reporting effects; produce a proposed reversible correction plan for owner approval.
4. Verify the missing `supplier_repayments.purchase_return_id` schema/runtime issue and migration deployment status.
5. Verify P&L classification for capital injections and other non-operating/equity movements.

Acceptance:
- Each issue has reproducible evidence or is explicitly marked unconfirmed/not reproducible.
- No production data/config/schema changes occur without separate approval.
- Any proposed cleanup explains effects on stock, cash, supplier/customer balances, audit, and reports.

### Phase 2 — Master data and unit conversion foundation

Tasks:
- Audit current product dimension fields and Settings master-data framework.
- Define migration/backfill and uniqueness for Jenis + Size + Kualitas without breaking existing SKU IDs/history.
- Extend existing Settings → Units with unit dimension and conversion factor, including kg=1, kuintal=100 kg, ton=1,000 kg, and a separate count dimension for buah/pcs.
- Define precision, rounding, base-unit, and conversion-change behavior.
- Add safe edit/disable/delete rules and server-side permissions.
- Decide how starter CSV templates are validated/imported, or document manual setup if an importer is not justified.

Acceptance:
- Existing products remain accessible and historical rows are unchanged.
- Duplicate combinations and invalid cross-dimension conversions are rejected.
- Standard conversions pass unit tests; inactive/in-use units do not corrupt old records.
- Template preview and repeat-safe behavior are verified if an importer is implemented.

### Phase 3 — Per-SKU prices and landed-cost policy

Tasks:
- Audit existing product purchase/sell price PATCH path, optimistic concurrency (`If-Match`/stale update behavior), price history, permissions, and client error handling.
- Define per-SKU base kg purchase/selling prices and optional unit-specific overrides, including rules for recomputation when a base changes.
- Separate master reference prices from moving-average inventory HPP.
- Audit existing purchase cost types, shipping allocation, posting flow, and reports.
- Add configurable allocation policies (weight, line value, manual, and other justified modes) with Owner defaults and permission-controlled purchase-level overrides.
- Explicitly distinguish inventory-capitalizable acquisition cost from operating expense; avoid double booking.
- Persist immutable posted allocation/cost snapshots and historical price context.

Acceptance:
- Derived and overridden prices work exactly as specified and are audited.
- Unauthorized price/policy changes are rejected by backend.
- Allocation methods reconcile to the original cost amount within documented rounding rules.
- Posted historical purchases/sales are unchanged; moving-average and cost-of-goods-sold invariants still pass.

### Phase 4 — Mixed receiving, sorting, reconciliation, and waste

Tasks:
- Audit inventory/lot/stock movement architecture and choose the least disruptive representation for pending/unclassified receipts.
- Implement/extend receive-as-classified and receive-as-unsorted paths.
- Support a single input producing several grades/SKUs, explicit recorded waste, and remaining unsorted balance.
- Configure variance tolerance and permissions/reasons for corrections.
- Define/document accounting and inventory valuation for loss and split outputs before writing implementation.

Acceptance:
- Pending/unsorted goods cannot be sold as a classified SKU.
- Input quantity reconciles to outputs + loss + remaining quantity, with explicit rounding policy.
- Total inventory value is conserved/adjusted according to the approved policy, with no hidden gain/loss.
- Stock movements are auditable and duplicate sort posting is prevented.

### Phase 5 — Payment workflow integrity

Tasks:
- Audit V1 purchase and sales partial payments, cash/bank movements, supplier/customer balances, refund/reversal, cancellation, and overpayment handling.
- Add only missing behavior; do not rebuild existing split-payment functionality.
- Fix request idempotency/response feedback where duplicate payment risk is evidenced.

Acceptance:
- The example Rp10,000,000 → DP Rp3,000,000 → later payment Rp2,000,000 leaves Rp5,000,000 outstanding.
- Payment status and outstanding amount derive from posted allocations, not manually editable labels.
- Cash/bank/AR/AP movements, reversal, and refresh/retry behave correctly in tests.

### Phase 6 — Returns, quarantine, and supplier settlement

Tasks:
- Audit existing sales/purchase return APIs, accounting events, restocking, supplier repayment, customer refund/credit, and migration state.
- Implement sales-return inspection outcomes: accepted, regraded/custom grade, split outcome, and waste.
- Implement supplier return settlement choices actually required by the current architecture: physical return, cash/bank refund, payable credit, replacement, negotiated credit/discount, or a permitted combination.
- Guard against repeated posting, double restock, duplicate refund/credit, and inconsistent source-document balances.

Acceptance:
- Each return links to the source transaction and has a complete traceable disposition.
- Partial accepted/regraded/waste quantities reconcile to the returned quantity.
- Financial totals and stock totals match the approved event matrix and are covered by regression tests.

### Phase 7 — Permissions, audit, and user-facing clarity

Tasks:
- Audit current capability names and assign minimal new capabilities only where current ones cannot express the policy.
- Test owner vs staff access for product dimensions, price changes, cost policy, sorting variance, returns, waste, and template operations.
- Improve errors for stale edits, failed network calls, permission denial, duplicate submissions, and server commits with lost responses.

Acceptance:
- Backend enforces permissions for all sensitive writes.
- Audit trails include actor/time/reason and before/after values where required.
- Error states are understandable and do not falsely encourage duplicate writes.

### Phase 8 — Page-level Help

Tasks:
- Verify current implementation first; implement only if absent/incomplete.
- Add a consistent Help entry on every required page using the shared existing layout pattern.
- Explain purpose, required fields, normal workflow, permissions, common errors, and safe correction path in clear Indonesian/user-friendly language (and preserve currently supported language/localization patterns).

Acceptance:
- Route-by-route coverage list is tested; all Help controls open the correct content.
- Content describes only existing behavior and labels; no dead controls or fake features.
- No UI redesign and no unreasonable bundle/performance regression.

### Phase 9 — Regression, performance, release, and handoff

Tasks:
- Run focused backend tests, frontend typecheck/build, relevant API/integration tests, and browser smoke tests for the changed workflows.
- Run accounting/inventory invariants: stock never becomes negative unless a specifically approved policy permits it; payment totals reconcile; posted snapshots remain frozen; cost allocation totals reconcile; returns/sorting cannot double-post.
- Verify migrations from a realistic prior schema against isolated `pos_test`; never use production as the test environment.
- Check that prior performance work has not regressed, including navigation and large responses where relevant.
- Create an owner-readable release report: changes, migrations, tests, known risks, rollout/rollback, and any production data cleanup still pending.

Acceptance:
- The required focused tests pass and any unrun tests are explicitly disclosed.
- Production migration/deployment steps have a rollback/recovery approach and a separate approval gate.
- No commit/push/deploy occurs without explicit approval.

## 8. Test matrix (minimum)

| Area | Required examples |
|---|---|
| Product master | Distinct Jenis/Size/Kualitas combinations; duplicate combination rejected; old products stay valid; in-use values cannot be destructively deleted. |
| Units | 1 kuintal = 100 kg; 1 ton = 1,000 kg; kg/kuintal/ton normalize consistently; buah cannot convert to kg implicitly; precision/rounding tested. |
| Pricing | Per-SKU purchase/sell prices; derived vs overridden unit prices; permission checks; stale update behavior; historical transaction snapshots unaffected. |
| Landed costs | Weight allocation; line-value allocation; manual override; rounding; ineligible operating expense; total cost reconciles; no double expense. |
| Sorting | Preclassified receipt; unsorted receipt; multiple output grades; recorded waste; unsorted remainder; excess variance; duplicate submission; cost value distribution. |
| Payments | DP + multiple partial payments + final settlement; split methods; status/remaining balance; refresh/retry; reversal/cancellation; no duplicate cash movement. |
| Sales returns | Quarantine, accept, downgrade, split outcome, waste, refund/customer credit, repeat-return protection. |
| Purchase returns | Return, supplier refund, payable credit, replacement, discount, combined outcome, duplicate protection. |
| Permissions | Owner allowed; staff denied without capability; staff allowed with capability; server-side enforcement. |
| Data/import | Preview, row validation, duplicate detection, repeat-safe apply, delete if unused, deactivate if referenced. |
| Deployment | Preview vs production configuration comparison; correct auth/API target; no secrets in diagnostics; no accidental production write. |
| Accounting | Cash/bank, AR/AP, inventory valuation, COGS, return effects, capital vs revenue, snapshots, and audit events reconcile. |

## 9. Suggested starter templates and policy notes

See `templates/README.md` for field descriptions and cautions. Starter examples include:

- Units: `kg`, `kuintal`, `ton`, `buah`.
- Production cost type examples: direct labor, production electricity, packaging, production ice/cooling, production overhead, other production cost.
- Purchase landed-cost examples: container cost, transport, loading/unloading, ice/cooling, port/handling fees, sorting/handling, other purchase acquisition cost.
- Product attribute examples: Jenis `Layang`; Size `Kecil`, `Sedang`, `Besar`; Kualitas `PP`, `Mco`. These are samples; the Owner must review/edit them.

**Important:** generic `Biaya Operasional` must not be automatically capitalized into fish inventory unless the approved accounting rule says it is directly attributable to acquisition. Keep production costs, operating expenses, and purchase landed costs distinct.

## 10. Decisions that remain open (do not silently decide in code)

Resolve these with a narrow audit or owner decision at the relevant phase:

1. Exact current product data model and safe backfill for Jenis/Size/Kualitas.
2. Whether current inventory architecture supports a receiving lot/pending classification state, or requires a minimal additive model.
3. Approved valuation rule for sorting loss and allocating original cost across grades and waste.
4. Approved default landed-cost allocation setting and which cost types are eligible for capitalization. Weight-based allocation is the recommended target, but existing V1 behavior must remain until explicitly changed.
5. Required quantity precision and permissible weight variance tolerance.
6. Exact refund/credit/replacement accounting event choices according to the current event matrix.
7. Whether the supplied CSVs should become an in-app import feature or remain owner-editable setup templates. Implement an importer only if it is safe and useful, not just because templates exist.
8. Disposition of suspected production test data after read-only reconciliation and owner approval.
9. Whether the preview deployment should be supported as a login target, and how it must receive non-production config.
10. Required languages and exact route coverage for Help based on the current application.

## 11. Definition of V3 done

V3 is not done because it compiles once or because a feature appears on screen. It is done only when:

- Each approved requirement maps to implementation, tests, and acceptance evidence.
- Existing V1 workflows and historical records remain intact.
- Inventory, cash/bank, AR/AP, HPP/COGS, returns, and audit invariants pass.
- Permissions are enforced server-side.
- Starter master data can be customized without destructive hidden defaults.
- User-visible failures accurately describe server outcomes and are safe to retry.
- Production risks have an explicit disposition and production changes have owner approval.
- The agent provides a final changed-files/test-results/known-issues/handoff report.

---

## Change log

- Initial planning draft compiled from tester notes and the user's decisions across prior planning conversations.
- The agent must verify current source and database state before acting; this document intentionally marks unresolved implementation details as verify/open decisions.
