# PRD AUDIT — BUSINESS POS SYSTEM

**Audited files (collectively):**
- `Business-POS-System-PRD-V1.md` — **primary / current source of truth** (audited-and-revised V1)
- `Business-POS-System-PRD-V1.backup.md` — pre-revision snapshot (superseded; used only to confirm the revision deltas)
- `PRD-V1-Audit-Report.md` — prior audit that drove the revision (historical; its findings checked against the current text)

**Reader note:** This is audit round 2. Round 1's findings were the *input* to the "Revised" PRD. This document audits the **current revised text**, verifies which round-1 findings are genuinely resolved, and surfaces what **remains** before business-rule/DB extraction. The PRD is in good shape — most open items are *honestly tagged* (`AI ASSUMPTION — NEEDS CONFIRMATION` / `TBD`). The residual work is concentrated into a firm Owner-decision list, not re-auditing.

**Tagging discipline (per instructions):** CONFIRMED / ASSUMPTION / TBD / CONTRADICTION / MISSING are used as the PRD uses them; anything I attribute is marked.

---

## 1. Executive Summary

The revised PRD is **structurally sound and internally consistent where it matters**: costing history is frozen (snapshot-on-movement + snapshot-on-sale-line), lifecycle and payment state are two orthogonal axes, the inventory movement matrix gives one authoritative effect per event (no double-reversal), manual vs derived ledger is split to guard double-counting, and cross-references/numbering were repaired. **Round 1's four blockers are effectively resolved in text.**

The PRD is **not yet final**, and it does not pretend otherwise: §31/§32/§38 explicitly carry the open items, and the document's closing line says it is "Awaiting stakeholder review & confirmation" of the §32 decisions. That honesty is the main reason it is **close** to ready.

However, three genuine **residual contradictions** and several **business-level ambiguities** remain that the revision did not resolve (they were not in round 1's scope), and these affect **financial/inventory truth or the data model**. Highlights:

1. **Shipping cost has no defined P&L path** — it is added to the purchase total (§12/§28) *and* listed as a manual "Shipping" expense (§17) and subtracted in the P&L example (§19). If both apply, shipping is double-counted; if only the first, the second is wrong. **CONTRADICTION, needs Owner decision.**
2. **"Staff can't create" vs "Staff = cashier at the POS"** — the permission matrix defaults Staff `Create = ⛔`, but the role is described as a live-counter cashier who must *record* sales (§5/§6/§13). A cashier that cannot create a sale is incoherent by default. **CONTRADICTION, needs Owner decision.**
3. **Owner "Delete" capability is defined but the model is non-destructive** — §7 grants Owner `Delete`, while §8/§26/§29 say posted financial/inventory records are never edited in place and should be Active/Cancelled/Voided/Archived. **What the Owner may actually hard-delete is not defined.** **Ambiguity, needs scope decision.**
4. **"Refund" is listed as an *Income* category** (§17) even though refunds on cancelled/returned purchases/sales are cash *outflows* (§8/§16 mark refund policy TBD). **CONTRADICTION / premature hardcode.**
5. **A caption mismatch:** §12×§30 tag the *same* purchase-receipt rule as "AI ASSUMPTION — NEEDS CONFIRMATION" (§12 P5) *and* "CONFIRMED" (§30 #5). One source of truth needed.

**Verdict at a glance: 🟡 READY AFTER OWNER DECISIONS.** The core behavior is defined tightly enough that the data model is nearly determined; ~10 blockers remain, all of which are genuine business decisions the PRD already lists in §32. Once the Owner answers them (and pins the 3 residual contradictions above), the document is ready for Business-Rule extraction.

---

## 2. Requirements Inventory

Extracted by category from the current PRD. This is an inventory, not a rewrite. All tagged CONFIRMED are explicit stakeholder decisions.

### 2.1 Core features
- Login + roles (Owner full / Staff restricted) with delegation & revocation — **CONFIRMED** (§6, §7, §33)
- Dashboard: stock/income/expense/profit/stock-value/best-seller/low-stock with date filters (Today/Week/Month/Year/Custom) — **CONFIRMED** filters; metric list proposed (§22)
- Single source of truth: all screens derive from authoritative transaction data (§22/§23, §30 #24)

### 2.2 Supporting features
- Product **master**: name req, category opt, unit, purchase price, selling price, stock, status, notes; code optional (§11)
- Supplier & Customer masters with histories & payables/receivables (§20, §21)
- Notifications & warnings: low-stock, insufficient-stock, below-cost sale, payment reminders, irreversibility confirm (§27)

### 2.3 Data management features
- Search / filter / sort / detail / period-compare across modules — task-oriented, "beyond CRUD" (§24)
- **Edit policy**: drafts editable; posted not edited in place for any inventory/revenue/COGS/payment field — `cancel → reverse → re-enter` (§8); non-financial fields editable + logged (§8)
- **Soft-delete philosophy** / non-destructive statuses (§26, §30 #23)

### 2.4 Transaction features
- Lifecycle axis: `Draft / Posted / Completed / Cancelled / Returned` (§9), payment axis `Unpaid / Partial / Paid` (§9) — two orthogonal fields; per-document allowed-state sets **TBD** (§9/§32)
- Cancellation **≠** Return (separate events) — ASSUMPTION (§12/§13, needs confirmation)
- Cash over-tender (change) model: tender / allocated / change, revenue=allocated (§13)

### 2.5 Inventory / stock
- Stock-adjustment, owner-only default, delegable (§7/§14)
- Inventory movement matrix — one authoritative effect per event (receipt/return/sale/adjustment/production in/out/cancellation) (§14)
- Negative stock: default **block** + warning; Owner-Option to enable (per-item/global); flagged (§14)
- Movement history with trigger/reason (§14, §26)
- Stock value (from costing) (§18, §22)

### 2.6 Purchasing
- Paid/Partial/Unpaid, multi-payment per purchase (§12)
- Shipping visible & separately identified; adds to purchase total (§12, §28)
- Receipt-based stock increase (§12); purchase return ↓ stock (§14)
- Supplier optional/required **TBD** (§20)
- Cancellation/return split, refund policy **TBD** (§12)

### 2.7 Sales / POS
- Select products / qty / prices / totals / payment; optional customer (§13, §21)
- Sale → ↓ inventory; sales return → ↑ inventory (§14)
- Multi-method payment per sale (§13); cash over-tender (§13)
- Price override gated by `price.override` (§13)
- Cancellation/return split § (§13)

### 2.8 Financial features
- **Derived ledger** (sales rev, purchase liabilities, COGS, production cost, payment effects) vs **manual ledger** (capital-injection/other income/rent/maintenance/ops/tax/…) with double-count guard (§17)
- Payment records: amount/method/date/optional-account; account balances *not fabricated* (§16)
- Refund behavior **TBD** (§16)
- P&L: Gross = Revenue − COGS; Net = Gross − Expenses (§19)

### 2.9 Reporting / analytics
- Sales, purchases, income/expense, inventory value+movement, production w/ cost, P&L; filters incl. date/category/product/supplier/customer/payment-method/status/expense-type/cost-type; period compare (§23)
- PDF + Excel export, respects filters (§25)

### 2.10 User / role / permission
- Role + granular capability model; Owner full; Owner delegates & revokes; custom roles (recommended) (§7)
- Staff default = view+edit base; export/stock-adjust/sensitive **NO** unless granted (§7)
- Audit logs: WHO/WHAT/WHEN/PREVIOUS/NEW/reason, append-only (§26)

### 2.11 Warehouse
- No warehouses in V1; single (implicit) warehouse/location; multi-warehouse deferred (§4, §35 #4). **TBD whether single holds.**

### 2.12 Product / material
- Product optional code, unit, price history (display=latest) (§11); raw materials & finished goods appear only in the V1 production model (§15)

### 2.13 Production / process
- V1 temporary model: raw → finished, raw ↓ finished ↑, per-run cost lines (Labor/Electricity/Gas/Packaging/Other), cost-type removal keeps history (§15)
- **No BOM/recipe** in V1 (§15); costing basis raw-only vs raw+overhead **TBD** (§15/§18)

### 2.14 Export
- PDF + Excel; content spans sales/purchases/inventory/income/expense/production/profit/reports (§25); Staff `NO` unless granted (§7)

### 2.15 Audit / history
- Who/what/when/before/after/reason on create/edit/cancel/return in stock adjust/permission/settings (§26)
- Immutable posted history — the governing principle (§1/§3/§18)

### 2.16 Other
- Notifications in-app only V1 (§27); on request settings (categories, cost types, payment methods, costing method) (§3/§16)

---

## 3. Contradictions

Residual spots after revising. Each: ID / RAD + location / A vs B / conflict / impact / recommended / Owner-confirm?

### CT-1 — Staff can't create, but Staff is the live POS cashier
- **Source:** §6 role table (Staff "restricted; view+edit base"), §5 (Secondary: Staff/cashier "fast day-to-day entry"), §7 matrix (`Create (records/transactions/contacts) ⛔ unless granted`)
- **A:** Warning §7 — Staff `Create` = ⛔ by default
- **B:** §5/§6/§13 — Staff are cashiers at the counter whose job is to record sales
- **Conflict:** A $ used literal POS sale record (a *create* operation) is not permitted by default, so the system cannot serve its stated "cashier does day-to-day entry" workflow without an explicit `sales.create` default grant.
- **Impact:** if left, a fresh Staff login cannot make the primary sale; real doc. Mis-pinned defaults will either block the primary workflow or leak sales-entry to a granted-only power model the Owner did not visualize.
- **Resolution:** decide the **default** Staff grants more precisely: at minimum grant `sales.create` (or a "POS operator" capability). Recommendation: grant `sale.create` + `sale.view` + `sale.edit-own-draft` by default; keep cancel/void/return/refund/permissions/export/finance-stock-adjust **OFF**.
- **Owner confirmation:** YES.

### CT-2 — Owner "Delete" capability vs non-destructive-only model
- **Source:** §7 matrix `Delete = ✅`(Owner); §8 (no in-place edits of posted); §11 (no hard-delete of used products); §26 ("should not be hard-deleted; use Active/Cancelled/..."; "Deletion privileged + logged")
- **A:** Owner **has** a Delete permission (§7)
- **B:** §8/§11/§26 say records are non-destructive; posted/inventory records aren't edited in place, products referenced by app history can't be hard-deleted
- **Conflict:** "Delete" exists but the target set of what-documented records are removable is undefined. If a posted **sale** falls under §7 Delete-on-Owner, that conflicts with the freeze and the double-effect guard.
- **Impact:** ambiguous enforcement; or an Owner post "delete" of a sale silently resets inventory time and COGS — destroying the immutability promise.
- **Resolution:** scope Delete precisely: allowed **only** for (a) draft transactions, (b) never-referenced master data (products/customers/suppliers) with no history, (c) mistakes within an approved grace window (recommend off in V1). Posted/receipted financial + inventory records → status transitions only. Recommendation: **no hard delete of any posted document in V1**.
- **Owner confirmation:** YES.

### CT-3 — Purchase shipping: capitalized cost vs operating expense (double-count risk)
- **Source:** §12 P3/P7 (shipping part of purchase, separately visible), §28 (Purchase total = Σ lines + shipping), §17 (default expense category **"Shipping"**), §19 (P&L example subtracts Shipping in Expenses)
- **A:** Shipping is capitalized into **purchase total** → increases payable and can flow into inventory/cost basis (§12/§28)
- **B:** "Shipping" is a manual/derived **expense** line and is subtracted in the P&L example (§17/§19)
- **Conflict:** the same shipping money can be booked **twice** — once in purchase/stock value and once in operating expense → GP or net-profit distortion or inventory over/under-valuation depending on which side is honored.
- **Impact:** direct correctness bug on Stock value, COGS, Gross and Net profit. **On the data model:** whether freight lives in the purchase capture vs the expense/journal changes what P&L reports read.
- **Resolution:** pick one, and make it explicit + configurable:
  - **Option A (recommended, cost-capitalization):** shipping on a purchase is part of the landed acquisition cost → goes **into** inventory cost basis (not an operating expense; no separate manual "Shipping" expense when attached to a purchase).
  - **Option B:** shipping is an operating expense in P&L → it should **not** be loaded into stock value and should use a separate price, not the purchase total.
  - In both, keep the "visible separately" requirement; just define the journaling destination and prevent double use.
- **Owner confirmation:** YES — this is a financial-correctness decision.

### CT-4 — Purchase-receipt stock rule tagged both CONFIRMED and ASSUMPTION
- **Source:** §12 P5 tag "**AI ASSUMPTION — NEEDS CONFIRMATION**"; §30 #5 tag "**CONFIRMED**"
- **A:** §12 says "Stock increases **only on goods receipt**" is an assumption pending-Confirmation
- **B:** §30 #5 says same rule "Purchase ↑ stock on receipt … CONFIRMED"
- **Conflict:** tag mismatch; the authority matrix says §7 is the single-source (permission) and §30 is "Business Rules Consolidated" — a rule can be CONFIRMED and ASSUMPTION at once.
- **Impact:** an architect may index §30 as CONFIRMED and design receipt-conditional facade; if the Owner had meant "stock on purchase-creation," wrong impact.
- **Resolution:** align tags to a single statement (recommended: mark **receipt-based** CONFIRMED, because it is consistent with "no-PO-module but keep order-vs-received distinction," §4) — or move §12's phrasing from "NEEDS CONFIRMATION" to the confirmed line. Update §31 accordingly.
- **Owner confirmation:** redirected (rule is nearly self-evident); recommend confirm-and-align, not open-ended.

### CT-5 — "Refund" listed as an *Income* category
- **Source:** §17 default income set: "Sales (derived) · Capital Injection · Other Income · **Refund** · Other"
- **Conflict:** A refund to a customer/purchase cancel is an **outflow** (§8/§16 mark refund policy TBD); labeling a refund an *income* category heads the double-count guard the wrong way.
- **Impact:** if a user records a sales-refund as "income/Refund", net profit is inflated by that same money (reverse of intended). Misaligns with §13/§12 which treat returns as money/stock corrections.
- **Resolution:** do not predefine "Refund" as a default **Income**; keep refunds as lifecycle/counter-events described by the pending refund policy (§16, §32 #4). If a manual category is needed, put it on the **expense** side (e.g. "Refund issued"), or better: leave it out of V1 defaults until the refund policy is confirmed.
- **Owner confirmation:** YES (tie to refund-policy decision).

### CT-6 — *minor*: "Edit data" scope for Staff undefined
- **Source:** §7 `Edit data (within §6/§7) | ✅` for Staff; §8/§9 bound it to transaction editing; but the matrix does not say **which entities** (products? selling price? purchase price? settings?) fall under "Edit"
- **Impact:** if "Edit" includes product pricing/cost fields edit, a cashier could change margins/stock file; if scoped too small, the "edit" base is nearly useless.
- **Resolution:** enumerate the entities/capabilities of Staff "Edit" (master data edits: Yes/No; pricing: Owner-only; settings: Owner-only).
- **Owner confirmation:** YES (part of the Staff-defaults decision CT-1 scope.)

---

## 4. Ambiguities (business-wise)

Each: what PRD says / interpretations / reasonable / affects DB+logic / Owner opt?

### AM-1 — "Average Cost" mechanics beyond the toy example
- **Says:** §18 Average illustration (10@10k + 5@12k → ~10,666), method-agnostic engine, historical snapshot; default = Average (**NOT confirmed**)
- **Interpretations:** (a) classic moving-average unit cost — sale consumes at current avg, holdings go on avg unchanged; (b) weighted-avg recalculated after every in/out including returns/adjusting; (c) a "rolled" avg store with a flag on negative.
- **Most reasonable:** (a) moving-average unit cost stored per lot/movement, with snapshot-on-sale; and a defined handling when a **return** restores stock at a cost different from the current avg (choose: return re-enters at the original line cost — recommended, keeps the layer coherent).
- **Affects DB/logic:** Yes — the algorithm (esp. after returns/negative-cost) must be pinned to a process; the PRD's freeze contract keeps it switching risk low, but an unspoken return-handling rule can still surprise.
- **Owner confirm:** yes for default method; return-at-original-cost is confirm-maintain MVP.

### AMB-2 — "Stock value" KPI on the Dashboard
- **Says:** §22 dashboard lists **stock value**; §18 costing determines inventory value.
- **Interpretations:** value at (a) last purchase price (simple), (b) current avg cost, (c) frozen/weighted snapshot. Under negative stock, unstable.
- **Most reasonable:** it should be **computed from the costing engine** (the same snapshot basis the reports use), i.e., moving-average cost basis, not live price. Flag that dashboard and reports show the same number (single source, §22).
- **Owner confirm:** resolves with costing-method choice.

### AMB-3 — "Income vs revenue" terminology on the dashboard/ledger
- **Says:** §1 "money in and money out"; §17 income = manual + derived-sales.
- **Issue:** the exec summary's "income" could mean (cash receipts) or (revenue earned). Higher-level: the system has no defined catch-all "cash in hand" (out of scope, balances not fabricated). Recommend the dashboard label "Revenue / Expenses / Net" rather than pretending "Cash balance."
- **Owner confirm:** low; a terminology/reporting gloss is enough.

### AMB-4 — "Sold goods"/"purchased goods"
- **Says:** sales ↓ / purchase ↑ (confirmed); returns reverse (confirmed).
- **Issue:** For **cancelled** transactions the money-reversal is TBD; the *inventory* part is explicit (§14) but the *financial* side (cash/credit back) is pending. This is the refund-policy hole (§8, §16, §32#4).
- **Owner confirm:** YES (combined in the refund-policy decision).

### AMB-5 — Selling-price basis + line discounts
- **Says:** sales take selling price at counter; price override gated; §32#12 "Discounts / price override" open; §35#2 discounts → deemed V1.1–V2.
- **Interpretations:** (a) keep product's configured selling price (no free-form per-line price) → override flagged; (b) allow free-form price per line as standard; (c) allow line/order discount fields.
- **Most reasonable:** v1 = default selling price; price override allowed **only** with permission and logged; optional flat line/order discount as a config toggle. Decision affects POS entry UI + pricing column.
- **Owner confirm:** YES (it is open Q12 already), but not DB-blocking if kept as a config toggle.

### AMB-6 — Low-stock threshold scope
- **Says:** per-product, configurable; default **TBD** (§27, §32#9).
- **Interpretation:** per-product default vs a global default. Recommend a global default + per-product override.
- **Affect:** dashboard low-stock list only; not DB-blocking.
- **Owner confirm:** low/neutral; align template.

### AMB-7 — Cost-entry unit today for production (per run vs per period)
- **Says:** per-run (v1) default vs per-period (monthly) TBD (§15).
- **Affects DB:** mostly yes — per-run = cost lines attached to run rows; per-period = a separate cost batch. Different data shape. But the PRD's per-run V1 nicely survives a later change.
- **Owner confirm:** YES (part of the production flow decision).

---

## 5. Missing Business Rules

Acknowledge those the PRD fully covers (delete-positive rules: immutability/freeze, no-double-application, derived/manual ledger split, draft-vs-posted editing, owner only stock-adjust delegable, optional negative stock, config categories, purchaser/sales returns, cost-type history persistence). The following are genuinely **MISSING** or under-specified:

### M-1 — Shipping cost journal destination (→ derives from CT-3)
- Not defined whether purchase shipping is endorsed to **inventory cost** or **operating expense**. **MISSING** (research demanded by CT-3).

### M-2 — Refund / cash-out on cancel & return — actual flows
- §8/§16/§32#4 mark it TBD but no skeleton. **MISSING:** for a cancelled or returned transaction that was partially/fully paid, what happens to the paid money — same-method refund, store credit, offset against other receivable/payable, or finished as a "Refund" outflow line. **Owner decision required.**

### M-3 — Delete & edit authority boundaries
- Which record types are hard-deletable, by whom, and whether invoices/transactions ever hard-delete (see CT-2). **MISSING.**

### M-4 — Costing-return / negative-stock cost mechanics
- §18 gives the negative-stock fallback **options** but no selected default; and no rule for a **return line re-entering stock at a cost** (does it restore the original line unit cost? current avg?). **MISSING.**

### M-5 — Finished-goods cost basis (raw-only vs raw+overhead)
- §15/§18 flag it; **default not chosen**. Consider "build-first later-change" → recommend `raw materials only` for V1 (simplest, least distortion) with overhead booked as P&L expenses, flag for change. **Owner decision required.**

### M-6 — Purchase "additional costs" beyond shipping
- Customs, handling, freight-in beyond shipping not modeled; shipping note but no rule for how extra landed costs attach to units. Out-of-scope is acceptable **if** documented. **MISSING** (could be added to shipping field, non-blocking).

### M-7 — Discounts & below-cost sale handling
- Below-cost sale `warning` (§27#4) exists but no rule (allowed or blocked?). Discounts deferred to V1.1. **MISSING** a V1 decision: allow below-cost (warning) or block. Note if cost method = avg, "cost" to compare = unit snapshot.

### M-8 — Partial line-return money+stock pairing
- §16/§29 hint; not a concrete rule: "returning half of a paid line" — how much money backs to the customer vs receivable, how stock + cost layer updates. **MISSING** until the refund policy (M-2) and cost-reentry (M-4) are pinned.

### M-9 — Stock-count / physical-count two-step
- Adjustment allowed, but a **two-step count → confirm** flow is not specified (PCR-style "list what you see, then post the difference"). Not required for V1 but commonly expected — mark optional, not blocker. **OPTIONAL.**

### M-10 — Off-hour / "below-cost warning" is a config flag, not hardcoded — fine as is.

### (None questionable to invent more.)

Capture of the confirmed core is complete and CAPITALIZED; the above are the gaps that feed the Owner-decision list.

---

## 6. Assumptions

Every place the PRD leans on an assumption. Tagged by risk to DB/financials/inventory/history/permissions/reporting.

| ID | Assumption | Where | Why it's an assumption | Risk | Suggested temp interpretation | Owner? |
|----|-----------|-------|------------------------|------|-------------------------------|--------|
| AS-1 | **Default costing = Average Cost**; method-agnostic engine, forward-only | §18, §1, §32#1 | not confirmed; affects COGS & inventory value | 🟡 MEDIUM (architecture supports any; the *default* value is high-impact) | Proceed with moving-average default; allow switch | YES |
| AS-2 | **Single warehouse** in V1 | §33, §35#4 | business not confirmed as single site | 🟡 MEDIUM (adds a location dimension if multi) | Model stock against one implicit `warehouse = default local`, but add `warehouse_id` nullable column upstream-aware | YES (low now, flag for future) |
| AS-3 | **Supplier optional/required on purchase** | §20 | not confirmed; "by analogy" been lifted | 🟡 MEDIUM (reporting/filter only) | allow purchases without supplier; nullable FK | YES |
| AS-4 | **Average-cost return handling** (return re-enters at original line cost) | §18 | not stated | 🟡 MEDIUM | adopt return-at-original-cost | YES (fold into AMB-1/M-4) |
| AS-5 | **Negative-stock COGS fallback** not chosen (last-price recommended) | §18, §26 | user optional; affects costing | 🔴 HIGH (only if/ when negative-stock toggle is used) | default `Last Purchase Price` flagged | YES |
| AS-6 | **Finished-goods cost = raw-only or raw+overhead TBD** | §15, §18, §32 | affects finished-goods COGS + inventory value + overhead P&L | 🔴 HIGH | default `raw-only` for V1 (recommended), overhead → P&L | YES |
| AS-7 | **Refund policy TBD** (cash out flow) | §8,§16,§32 | refund $ of paid cancel/return undefined | 🔴 HIGH (financial correctness) | until a confirmed: treat cancel/return of paid loops as a **refund-cash-out journal line** sized to the amount previously collected (do NOT count as revenue/expense) | YES |
| AS-8 | **Remove of posted is never edited in place / finance immutable** | §8,§9,§18 | This is now the core designed invarient — confirmed principle | 🟢 LOW (it is the safety assumption the whole design sits on; already owner-facing) | retrofit | Confirm once (this thread is correct) |
| AS-9 | **Staffs can edit product pricing/master** unless scoped | §8 | permission not pinned | 🟡 MEDIUM | restrict by default; Owner-only pricing edits | YES (in CT-6) |
| AS-10 | **"Profit" defined purely as died GP/NP, no "cash balance" snapshot** | §19, §22 | no real balance (out of scope) | 🟢 LOW | dashboard shows Revenue–Expenses, not a drawer balance | NO (adopt) |
| AS-11 | **Unit = single base unit** for all transactions | §26 | product has unit but no UoM conversion | 🟡 MEDIUM (buy kg vs sell pkg) | V1 single base unit | YES (low; flagged future) |
| AS-12 | **IDR single currency** | §3 | confirmed assumption | 🟢 LOW | keep | NO (already) |
| AS-13 | **Cost types removal = metadata keep** | §31 | confirmed check | 🟢 LOW | — | NO |

High-risk assumptions are AS-5, AS-6, AS-7 (financial) and AS-2/AS-3/AS-4 (data-model). All are already on the §32 Owner list or easily folded in. None is hidden — the PRD marks them.

---

## 7. Owner Decisions (practical, prioritized)

Only genuine business decisions. Technical topics are excluded (engine can be method-agnostic already).

### 🔴 MUST DECIDE BEFORE DATABASE DESIGN

**OD-1. Costing method default for V1 + freeze-on-switch policy.**
- Why: drives COGS, stock value, GP — the costing engine is method-agnostic but the default determines numbers now, and "switch only forward" must be itself confirmed.
- Affected: PRD §18 / business logic / DB (cost snapshot) / reporting.

**OD-2. When is a sale/purchase "posted" and thus cost-frozen?** (draft → posted moment vs completed)
- Why: the freeze contract pivots on the posted marker; decide whether *draft* to *post* is a single click (typical POS) or a two-step confirm.
- Affected: lifecycle, cost, editing policy, audit.

**OD-3. Negative-stock fallback cost basis** (Last Purchase / Avg / Zero / Block / Owner-override).
- Why: only in one driver but changes COGS correctness when the toggle is on.
- Affected: costing, inventory value, reporting.

**OD-4. Finished-goods cost basis: raw-material-only OR raw + production overhead.**
- Why: sets finished-goods inventory value, its COGS, and whether overhead is in inventory or P&L.
- Affected: production model, costing, inventory value, P&L.

**OD-5. Editing policy confirmation: `cancel/void → reverse → re-enter` for any posted document; no in-place edit of posted financial/inventory.** (Confirm §8 as s)
- Why: this is the immutability core; confirm so engineers can build the reversal-vs-edit path (which affects DB: reversal rows + audit guarantees).
- Affected: DB (movement/audit), editing policy, audit.

**OD-6. Shipping cost destination on a purchase** — part of inventory cost (capitalize) VS an operating expense. (Resolve CT-3.)
- Why: prevents double/journal distortion; affects inventory valuation, COGS via avg pool, and P&L; DB/ledger placement.
- Affected: purchase capture, inventory, P&L, reporting.

**OD-7. Refund/cancellation cash policy** on cancelled or returned paid transactions (same-method refund / store credit / offset / "refund" outflow line).
- Why: defines money flows for the reversal paths; otherwise a cancelled paid sale has no defined money outcome.
- Affected: transaction lifecycle, journal, reporting.

### 🟡 SHOULD DECIDE BEFORE IMPLEMENTATION

**OD-8. Staff default permission set, including: can a Staff user **create sales** at the POS? Which exact capabilities (sale.create / edit own draft / view) default ON?** (Resolve CT-1, CT-6.)
- Why: pins the security baseline and the very first coupon (a cash receipt sale record) — a wrong default either blocks the core POS or over-grants.
- Affected: permissions, POS workflow, audit.

**OD-9. The exact Owner "Delete" scope** — which records can be permanently deleted (vs only status transitions). (Resolve CT-2.)
- Why: delete scope drives DB soft-delete/marker design and the audit contract.
- Affected: DB, audit, editing policy.

**OD-10. Staff non-state master edit scope** (products / pricing / categories) — Yes/No per field.
- Affected: permissions, audit, product/cost integrity. (Follows CT-6.)

**OD-11. Production cost-entry unit — per run (recommended) or per period (monthly).**
- Affected: production data shape.

**OD-12. Discounts / price override behavior in V1.** Confirm: default selling price, price override permission-gated & logged, and whether per-line/order discount is a V1 option or deferred to V1.1.
- Affected: sales entry, revenue calculation, permission model, reporting.

**OD-13. Return-handling cost rule** — returning stock re-enters at original line cost (recommended) vs current avg.
- Affected: inventory cost coherent.

**OD-14. Low-stock threshold — global default + per-product override (recommended).**
- Affected: dashboard low-stock, notifications (not DB-blocking).

**OD-15. Supplier: required or optional per purchase.** (Recommended: optional, counter buys.)
- Affected: purchase input/validation, supplier reporting.

### 🟢 CAN DECIDE LATER

- OD-16. Money rounding / decimal mode (§5 #1) — config-only.
- OD-17. Transaction/invoice document numbering (traceability).
- OD-18. POS cash-session / X-Z drawer close (workflow; scheduling on whether V1.M1).
- OD-19. Backup & restore responsibilities/schedule (ops, not product scope).
- OD-20. Receivable/payable aging + reminders (V2), selling-price history (V2).
- OD-21. Warehouse/multi-location & unit-of-measure — deferred; but design the `storage_location_id` and keep the current "default location" so future multi-site does not re-model (a *schema* recommendation, engineer-owned).

---

## 8. Terminology Issues (evidence-based, not decided)

Terms the PRD uses but does not define a fixed meaning for — with noted colliding uses:

| Term | Where problematic | Evidence / concern |
|------|-------------------|--------------------|
| **"product"** | vs **"item"** vs **"line"** | "product" (master), "items" (purchase/sale lines: "line items"), "raw material", "finished product / finished goods". All the pieces exist; a **single mini-glossary** is missing. Distinguishable concepts (master vs line vs production-role), but not pinned → risk of a code glossary mismatch. Recommend a one-line Definitions section in the PRD-review copy. |
| **"income"** | revenue vs cash-in | §1 "money in / money out" is casual; §17 income = manual category + "Sales (derived)". No field is "cash in hand" (out of scope), so "income" is ambiguous across reports. Recommend the dashboard/reporting language use Revenue / Expenses / Costs explicitly, and call the derived revenue "Revenue", not "income", when possible (§17 already does). |
| **"Refund"** | income vs cash-out | Listed as an Income default (§17) yet a refund-cone is a cash *out*. Fix as CT-5. |
| **"stock"** | on-hand | Used consistently for physical count; "inventory" sometimes swap for "inventory value." Acceptable; flag only for glossary. Contains "on-hand quantity." |
| **"shipping"** | cost vs expense | §12/§28 treat as added cost; §18 set as expense; §19 subtracts. Already CT-3 — the terminology mirrors the unfinished accounting decision. |
| **"posted/completed" vs "active"** | "Active" is listed as a synonym of "Posted". | §9 says `Posted` / `Sequential`; elsewhere "Active / Cancelled / ..." (§26). Low risk but call posting = applying effects; the exact synonym should be pinned (8). |
| **"cost" / "unit cost" / "purchase price"** | separate concepts | "purchase price" (currentProduct), "unit cost" (COGS snapshot), "stock value". The delta is real; ensure report/the model the PRD's "purchase price = latest" is distinct from the unit-cost snapshot. Recommend keeping them distinct named fields. |
| **"process/production"** | "process cost" vs "production cost", "run" | Ambiguity in §5/§15-17 ("process cost"/"shipping cost" in the task prompt's example list). Flag: base-finance notes (cost types) vs cost-engineering (finished-goods cost basis). same root as AMB. Conflict | 

Full glossary to be finalized in Business-Rule step — provided the Owner's nod on concepts above (esp. shipping, refund, valuation).

---

## 9. Scope Classification

Classified by the "build-first, change-later" directive — a feature being correct is enough to keep; nothing removed for *being hard*.

### CORE / MUST HAVE (primary workflow)
- Auth/roles, Owner/Staff + grant/revoke **§6/§7**
- Product process (optional code, price history) **§11**
- Sale/POS: line entry, multi-payment, cash over-tender, optional customer **§13**
- Purchase, stock on receipt, multi-payment, shipping visible **§12**
- Inventory movement model + adjustment, negative-stock flag optional **§14**
- Costing (snapshot, method-agnostic, forward-only) **§18**
- P&L **§19**
- Payments/journal, partial/paid/unpaid **§16**
- Supplier & customer masters, optional **§20,§21**
- Audit (who/what/when/before/after) **§26**
- Search/filter/sort/period-compare + PDF/Excel export **§24,§25,§23**
- Dashboard with date filter **§22**

### IMPORTANT / SHOULD HAVE
- Low/medium-stock warnings (& threshold config) **§27**
- Below-cost sale warning (config) **§27**
- Production V1 (raw→finished, cost-line, history persistence) **§15**
- Transaction document numbering (audita traceability) (§35#7) — should be modeled, cheap

### OPTIONAL / NICE TO HAVE (defer w/o blocking)
- Discounts & price override V1.1 (if not part of core POS ordering) — the PRD's found "can be V1.1"
- Cash-session / X-Z close (if additive)
- Two-step stock count
- Master-data import/export
- Selling-price history (V2)

### UNCERTAIN (mentioned, needs pinning before closing)
- Refund/cancellation policy flows (§8/§16) — **needs the OD-7 decision**
- Negative-stock COGS basis (§18) — **OD-3**
- Production costing basis (§15) — **OD-4**
- Discounts/price-override exact behavior and scope — **OD-12**
- Supplier-required on every purchase? — **OD-15**
- Multiple warehouses / UoM — explicitly future, but decide whether the data model seed for `location`/`warehouse_id`.
- Owner-delete scope — **OD-9**

---

## 10. Database-Impact Analysis (impact analysis, NOT an ERD)

These requirements each carry a consequence the data model must absorb. Purpose: call out *why* each matters to the eventual schema — no attributes are designed here.

| Requirement | Impact on the schema (why it matters) |
|---|---|
| **Historical cost frozen as snapshot** | Every sale-line and stock-movement must store its own `unit_cost` + `cost_method + condition at time` (a `cost_snapshot`). Affects: sale_line, stock_movement, batch/valuation tables. This is the single most schema-driver in the whole PRD. |
| **Two orthogonal states (lifecycle + payment)** | Cannot be one enum → the design must keep `lifecycle_status` and `payment_state` as separate columns on sale/purchase, plus an `applied-effects journal`. Affects: re, sale, purchase, and a `journal/movement` table. |
| **Movement-inventory matrix** | Every inventory-changing timer → one `stock_movement` row that carries `±qty, prior & new on-hand, trigger`. Guarantee no double-apply. Affects: movement ledger + integrity constraints. |
| **Reorder/ P&L derived vs manual dividender** | Two diverged flows, but a unified journal/ledger to hold derived entries + manual entries while preventing duplicates. Menas unique guard keys + category flag `is_derived`. |
| **Partial / multi-payment & split-method** | Payments are 1st-class rows (a polymorphic FK to any doc-type), plus receive/alloc/change on cash splits. Prevents over-payment bugs. |
| **Cash over-tender (change)** | Payment rows need `tender`, `alloc`, `change` semantics so `allocated ≤ total` while tendered can exceed. |
| **Returns & cancellations as events** | Movement `reversal` reference (points at the original movement) rather than delete. Preserves one-to-one-effect & audit. |
| **Permission model w/ delegation** | `role` (set of capabilities), `user_role`, and **user_capability override** join. Avoids hard-coded role logic. |
| **Audit log** | Append-only `audit` table, immutable: who/what/when/before/after/reason. Must never be updated by an edit. |
| **Soft-delete / non-destructive** | Status transitions over delete; a `DATA records` model VIP. Master-level audit. Deletion in no hard-removal. |
| **Pricing history** | multi-period price per product (store purchase-price history, later selling-price history). |
| **Production run & cost-lines** | Inputs (raw), finished outputs, cost by type; flexible shape for raw-only or raw+overhead since the basis is still open. |
| **Stock valuation & warehouse** | A nullable `location_id` from the start (even with one location) so a future multi-warehouse lands without shifting free. |

This list is architectural awareness, not a verdict — the DB stays blocked until §7 decisions are closed.

---

## 11. Critical Risks

### 🔴 Critical
1. **Cost/valuation contract break** if posted cost is mutated by a config change (mitigated by snapshot design; must stay enforced). **PAIRED** with Owner DECISIONS OD-1/OD-5/OD-6.
2. **Shipping double-journaling** (CT-3) — inventory-vs-expense double count → wrong GP/NP + inventory value.
3. **Refund/cancel flow unmodeled** (OD-7) — a cancelled paid sale has no numeric cash-out story; P&L & payable/receivable unreliable until pinned.
4. **Production/workscule/overhead basis** (OD-4) + **negative-stock cost basis** (OD-3) — both shift inventory value and COGS on a single open choice.
5. **Staff “can't create” default vs cashier flow** (CT-1) — if the wrong default packages, core POS record is broken for the primary secondary user.

### 🟡 Important
- **Per-module business rules**: staff default set (CT-1/OD-8), Owner-delete scope (OD-9), master-edit scope (OD-10), return-initial cost (OD-13), supplier required (OD-15). Each shapes the data/UI but not destructive deep.
- **Repvor V1 scope runtime**: warehouses/UoM/location column, doc numbering, reports list exact set (sizing) — cheap to settle, shouldn't be left until DB freeze.
- **Tag inconsistency** (§12 vs §30 on purchase receipt) — trivial but confuses authority (CT-4).
- **"Refund" as income** (CT-5) will distort P&L if a user uses it.

### 🟢 Low Risk
- Money rounding mode, per-currency IDR only.
- Below-cost warning config.
- Notifications scope (in-app only).
- Report list **exact set** at sizing (confirmed in principle).
- Backup/restore ops.

---

## 12. Final Verdict

**🟡 READY AFTER OWNER DECISIONS**

The PRD is **not yet ready to hand** for Business-Rule extraction **exactly** because of the decisions that are explicitly flagged, plus three contradictions introduced in the "cut" that the revision did not resolve (shipping, staff-who-uses-POS, delete-scope). The **core behavior is defined** (freeze, movement matrix, lifecycle vs payment, ledger split) and is architecturally sound — nothing here requires re-architect.c model to run. That is precisely the state for **"ready after the Owner answers the definitive list."**

The block-to-Business-Rules gate is **OD-1..OD-7** (costing method+frezen, posting boundary, negative-cost, finished-costs, correct edit/reverse-entry policy, the purchase-shipping path, and refund cash-out). Any one of them, answered, drops risk on a meaningful axis; the rest (OD-8..OD-15) can follow within the same review session.

It is **not a "ready-to-code"** verdict: the feature list is large and correct, but the presence of the blocker decisions prevents the DB/business-logic extraction from being performed without **making high-risk assumptions** (in particular, shipping basis, costing default, refund flow, and a couple of model shapers).

---

### NEXT STEP
1. **Freeze this audit.** Owner reviews §7 (decision list) — in particular the 7 MUST items and the permission trio (Staff-create, Owner-delete, Supplier-opt).
2. **Once the Owner answers (§7 decisions), produce PRD V1.1** that:
   - Bakes in the chosen costing method + freeze policy,
   - Resolves CT-1 (Staff/POS), CT-2 (delete scope), CT-3 (shipping cost), CT-4 (tag alignment), CT-5 (dedicated "Refund" income),
   - Fills OD-7 refund flow, OD-4 finished-goods costing, OD-3 negative stock, OD-12 discount/price-override,
   - Adds a short glossary (§9 terminology) and pins the purchase-item receipt-tag.
3. **Then** business rules → DB architecture → API → UI → implementation → test. **Do NOT start DB/coding before the 7 MUST items are signed.**
4. Optional engineering note for the DB design step (not now): include `warehouse.location_id`, `cost_snapshot` on movements, and polymorphic payment. Present it only after Owner decisions.

— Audit prepared. **Audit first. Decisions second. Architecture third.**