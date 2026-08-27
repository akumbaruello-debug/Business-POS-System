# PRD V1 — Audit Report

Audited file: `Business-POS-System-PRD-V1.md`
Auditor basis: the 31 "Original Stakeholder Decisions" only. No modifications were made to the PRD.

---

## Executive Verdict

**NEEDS MINOR REVISION**

The overall architecture direction is sound: per-movement historical cost anchoring, a flexible role+permission model, status-driven (non-destructive) transactions, and configurable categories all line up with the stakeholder decisions. However, **four self-contradictions inside the document itself**, plus several unresolved payment/costing/production rules, must be corrected or explicitly pinned before this file is used as the source of truth for the database architecture. None of the corrections rework the document — they are targeted fixes — hence "minor," not "major."

Two items are true **blockers** regardless: (1) the Costing section contradicts itself on whether historical sale-line cost is *frozen* or *recomputed* when the method changes; and (2) the Staff permission set is stated inconsistently in two different sections.

---

## Critical Findings

### CR-F1 — Costing: "frozen historical cost" vs "recompute by active method" directly contradict one another
- **Location:** §16 (lines 430–438); restated in §2 (line 48) and §3.
- **Problem:** §16 says, in the same subsection, both
  (a) *"Reports either read the stored (historical) cost … the chosen method is used on the sale line captured then"* — i.e. historical freeze; and
  (b) *"If FIFO is chosen later, earlier consumed at 10,000 first. Either way the sale line's cost **is stored and recalculated by method before archiving**."*
  These are opposite contracts. Freeze says a January sale keeps its recorded cost; "recalculated by method" says switching Average→FIFO recomputes historical cost.
- **Why it matters:** This is the single most architecture-bearing decision. It decides whether the `sale_line.cost` column is a *snapshot* (immutable) or a *recomputable* value, and whether a future method switch rewrites COGS and Gross Profit history. Getting this wrong requires re-baselining inventory valuation + every sale line COGS (the PRD itself rates Costing HIGH in §34).
- **Current interpretation:** Ambiguous — the document both freezes and recomputes.
- **Recommended interpretation:** Freeze the sale-line cost at transaction time under the *active* method; switching the costing method only affects **new** transactions and going-forward valuation, never historical COGS. This is the only reading consistent with stakeholder decision #23 ("Historical transactions keep their historical cost"). Store `sale_line.cost` as an explicit snapshot. To change method → generate a `stock_revaluation` event going forward, never retroactive revaluation (see C3).
- **Stakeholder confirmation required:** YES — the costing-method default (Average vs FIFO vs Last) is decision #20 and is still tagged "AI ASSUMPTION." Confirm both the **method** and the **freeze-on-switch policy**.

### C2 — Internal contradiction: can Staff export by default or not?
- **Location:** §7 (line 99, Staff "Cannot … **exports**") vs §8 AI assumption (line 137, Staff default set includes "**run exports**").
- **Problem:** Two sections give the opposite answer for the same default permission.
- **Why it matters:** Permission model feeds the security baseline. A wrong flag lets staff export financial data they should not, or blocks a legitimate cashier function.
- **Recommended interpretation:** Decide once. Recommendation: **Staff cannot export by default** (export leaks financial data); Owner grants it explicitly. Fix §8 to match §7.
- **Stakeholder confirmation required:** YES — the exact Staff default-permission list is open in §30 Q3; this export inconsistency is a sub-case that must be pinned.

### C3 — Editing a sale/purchase that has already been posted can silently rewrite the historical cost the PRD promises to protect
- **Location:** §27 edge table ("Editing a sale (privileged): *old movement reversed → new movement*") + §21 Data Management lists "Edit" as a core capability.
- **Problem:** The doc allows a privileged user to edit the *quantity/price/cost* of an already-paid transaction, which reverses and re-creates the stock movement. That is a rewrite of irreversibly a posted, cost-anchored transaction — it conflicts with the immutability promise in §2, §3, and §23.
- **Why it matters:** A re-created movement can double-count or lose an audit line, and it changes COGS for that sale against the freeze rule.
- **Recommended interpretation:** Editing is **not** allowed on posted/lifecycle-Paid transactions or on inventory-affecting lines. Corrections must be a **new compensating transaction** (cancel + re-enter, or a dedicated correction/void) — never edit-in-place of a posted entry. Only *unposted/anchor-created* records (status Draft) may be edited freely.
- **Stakeholder confirmation required:** Yes (this is a business-policy call on how the Owner wants corrections handled).

---

## High Findings

### H1 — Gross/Net Profit can be double-counted by mixing the automatic P&L with manual entries
- **Location:** §17 (P&L "computed"), §15 (manual Income/Expense), §5 (income/expense defaults include "Sales", Purchase", "Refund").
- **Problem:** P&L "Revenue" is said to be *derivable* from sales; **and** manual income allows a category literally named "Sales." If a user records a sale *and* a manual "Sales" income entry, Revenue is counted twice; similarly "Purchase" as an expense can double-count against COGS (goods value already enters through inventory → COGS).
- **Recommended interpretation:** Split the ledger — **automated** revenue/COGS from sales/purchases (never manually enterable) and **manual** income/expense reserved for non-operational items (Oware, Capital Injection, Other). Reserve dated income/expense categories excluding "Sales" and "Purchase" COGS; keep "Refund" only for money refunds, distinct from inventory-return events.
- **Stakeholder confirmation required:** Yes — confirm the category list and that manual entry cannot overwrite derived revenue.

### H2 — Payment model undefined for cash over-tender ("change") and contradicts the ≤ total validation
- **Location:** §11 example ("received 500k on a 450k sale"), §26 validation ("sum of payments cannot exceed total").
- **Problem:** A POS tender-over-payment is normal (customer gives 500k on a 450k bill). The validation blocks payment > total, but the example documents "record change/flagged." These conflict; the "change" mechanism is unspecified.
- **Recommended interpretation:** Separate the **received tender** (cash note) from the **payment amount** (allocated, ≤ total) from **change** (tender − payment). Payments sum ≤ total; change and tender are recorded distinctly on cash splits. Never record raw tender as a "payment" exceeding the total without a defined change flow.
- **Stakeholder confirmation required:** Yes (how cash over-tender and mixed-change are captured — a POS is the face of the app).

### H3 — Sale cancellation does not explicitly state stock is restored (double-reversal risk)
- **Location:** §12 movement table lists only "Sale −"; §9 says "cancel invalidates a transaction" and "return reverses physical goods," but there is **no explicit stock rule for a sale cancellation**.
- **Problem:** If a sale is created (stock decremented) and then Cancelled, with no explicit "cancelled sale returns stock to inventory," the stock can stay negative/undercounted-and then a later "return" decrements again → double application (grid item #5 of the audit).
- **Recommended interpretation:** Every lifecycle transition maps to an explicit, single movement opposite to the original: Cancel→one inversion movement; Return→the goods-inversion movement; never both on one event; guard against cancelling a returned (or already-cancelled) transaction.
- **Stakeholder confirmation required:** Partly — confirm that a **cancelled** sale (vs returned) does restore stock if goods were delivered and returned physically; define the cancel-vs-return distinction specifically for sales.

### H4 — Finished-product unit cost from production is undefined and the worked example is broken
- **Location:** §13 example line 345: *"flour 5kg … sugar 2kg (Rp,00) … finished +10 cakes @ cost = (50k+50k+..)/10"*.
- **Problem:** (a) the "Rp,00" price is corrupted; (b) the formula is unparseable; (c) more importantly the PRD does not state **which components determine finished-goods COGS**: raw-material cost only, or raw-materials + labor/electricity/gas/packaging (the "production cost" types). This determines the value added to finished inventory and its subsequent COGS on sale.
- **Recommended interpretation:** Define finished-unit cost explicitly, e.g. *finished_unit_cost = (Σ raw-material costs + Σ production-cost-type amounts) / finished_quantity*, and correct the example to a real number (e.g., ¥ = 10 cakes). This is a placeholder "AI assumption" model, so mark clearly as such, and tag "production" being unconfirmed continues.
- **Stakeholder confirmation required:** Yes — hold as an assumption, but must be an *explicit* one with a correct numeric example.

### H5 — COGS for negative-stock sales is undefined
- **Location:** §12 negative-stock option, §16 .
- **Problem:** When negative inventory is enabled, a sale can consume goods of which nothing on hand is available; there is no cost to assign, yet GP must compute.
- **Recommended interpretation:** Define policy — on a negative stock sale use (recommended) the product's **last purchase price** as the fallback COGS basis, or (allowable) zero-cost with an auditor flag; never allow a silently unaccounted (NaN) line.
- **Stakeholder confirmation required:** Yes, when choose to enable negative stock.

### H6 — Supplier is only "assumed optional"; for purchases the PRD is ambiguous, but the brief only confirms *customers* optional
- **Location:** §4 supplier "Optional at purchase time … by analogy" (AI ASSUMPTION); §22 P&L input "supplier (optional?)".
- **Problem:** §5 purchasing states stakeholding "paid/partial/unpaid" but doesn't confirm supplier required. This "by analogy" assumption could surprise.
- **Recommended:** confirm whether every purchase needs a supplier or can be a counter/cash buy. This affects purchase-list reporting filters.
- **Stakeholder confirmation required:** Yes (simple).

---

## Medium Findings

### M1 — Broken/duplicated section numbering and cross-references
- **Location:** numbering in headers — there are **two** `## 11` (Purchasing, Sales); final section jumps `34` → `36` (no `35`); the Exec summary (§2 line 45) references "see §17 Costing," but **Costing is §16** and §17 is Profit & Loss.
- **Why it matters:** This doc is meant to be the *single source of truth* for DB/API/UI work. A wrong cross-ref (e.g., pointing implementers at "§15 Inventory" when inventory is §12) actively misdirects.
- **Recommended:** Renumber §11 sales → and re-sync all cross-refs; there is no §15 referred as Inventory in several places.

### M2 — Cross-reference errors throughout
- **Location:** §10 Sales deps: "Inventory §15, Customer §21, Costing §18" — all wrong (Inventory §12, Customer §19, Costing §16); §21 Purchasing deps "Supplier §21" — should be §20; cost . §12 and §25 refer to "§15" for negative stock — negative stock is §12. etc.
- **Why:** as M1.
- **Recommended:** audit and correct all internal links.

### M3 — Transaction lifecycle is scattered; no single "lifecycle" state model
- **Location:** across §9, §10, §12, §27.
- **Problem:** There is no consolidated "transaction lifecycle" defining for every document type: its status axis; e.g., Purchase: Draft → Open/Received → Paid/Partial/Unpaid → Completed → (Cancelled | Returned). The doc conflates *payment status* (Paid/Partial/Unpaid) and *lifecycle status* (Active/Cancelled/Voided/Archived) as if one; these are two orthogonal axes and should be modeled separately.
- **Recommended:** add an explicit lifecycle diagram/state matrix per entity (Sale, Purchase, Production, Stock Adjustment), separating the payment axis from the status axis, and list which transitions are allowed and what movement each triggers.

### M4 — Cancellation of a fully-paid purchase: where does the refunded cash/go go?
- **Location:** §27 edge case "Cancel of fully-paid purchase → payments returned to a 'returns/cash-out line' or reversed **depending on config**."
- **Problem:** Money flowing "out" on refund must be modeled explicitly — this isn't just a config switch; it affects cash-flow / P&L reports.
- **Recommended:** define a Cancellation/Refund journal entry (a sales-return money-out or a "refund expense") any choice MUST not distort GP/NP.

### M5 — No "Received / in-transit" state on purchases
- **Location:** §16 purchase flow ends "stock increases" but has no intermediate "ordered vs received" distinction.
- **Problem:** A P.O. created → not yet received; if it is cancelled before receiving, does stock change? The doc says "stock reversal only if stock actually received," which implies a received-check exists — but it isn't modeled explicitly.
- **Recommended:** add a purchase status axis: ordered / received / completed; stock movement attaches only to *received* (and its reversal on return).

### M6 — Partial return of a partial-paid transaction needs explicit paired money+inventory handling
- **Location:** §27 "Partial payment then return → return net of paid" but no rule for *partial goods* return against *partial payment *.
- **Recommended:** a return line must specify quantity returned, money refunded (out of the amount already paid), and the receivable/payable remaining, all recomputed. Two-numbers keep consistent.

### M7 — Low-stock threshold is TBD and unbound
- **Location:** §25 "threshold set per product OR global LOW-buffer (TBD)."
- **Worth flagging** because the default affects the dashboard "low-stock" list and reorder. Simple decision.

### M8 — Product "unit" with conversions is unmodeled
- **Location:** product has a *unit* field only; there's no handling of purchase unit ≠ sale unit ≠ production unit (e.g., buy by 1 bag 25kg but sell by kg).
- **Recommended:** confirm single base-unit per product for all transactions in v1, or flag multiple-unit conversion as a missing feature. (See POTENTIAL PROD below.)

### M9 — No explicit product "sellable/active" rule enforcement
- §9 states only "deactivated product is not sellable" but not whether stock is blocked, whether it can still be purchased, etc. Recommend explicit sellable/buyable flags.

---

## Low Findings

Stein straightforward, low-impact fixes:
- §11 example "Payment 2 slower than the counter" is a very odd phrasing typos — clarify.
- Appendix P&L example is arithmetically inconsistent with §17: Appendix lists "Sales 50M − COGS 8M = GP 20M − 9M = 11M" (50−8 ≠ 20), while §17 uses COGS 30M. Fix appendix to match.
- Typos/glitches: "asyn concept" (§29), "historically stored workforce sale" (§12 acceptance), "„caseless" (V1 §1 "caseless per filters"), ".sudh gov.." rendering — no single **overall tagging of $&. . ." Several broken tokens ("Rp,00", "…issued and thus").
- §12 C4 trigger is called "CONFIRM (asyn concept)" — the word "concept" is listed as CONF.

---

## Potential Missing Requirements

> Listed as *potential missing* — NOT added to the PRD. They should be discussed and accepted/rejected by the Owner before the DB.

1. **Volume / decimal handling for money and rounding** — no rounding rule for float sums (to near 100/500 for cash) or numeric tolerance, directly affects reports.
2. **Sales/purchase discounts (per line and order, promo, coupon)** — POS typically needs at least a per-sale discount; not present. Costs/COGS recomputation when discount applied needs rule.
3. **Cash-session / shift closing (X/Z reports)** — a real shop POS needs per-staff/per-shift totaling and a drawer-opening record; the PRD has none.
4. **Receipt/invoice line for cancelled transactions** on printing (non-goal for templates, but the refund slip is often needed).
5. **Stock "valuation warning" focus/deploy** on item location-level (no warehouses/locations), so "blank" location is a single-warehouse model — if the Owner has multiple sites, this is a real gap.
6. **Multiple units of measure & conversion** (see M8) e.g., purchase by kg, sale by pkg unit.
7. **Sales price history / price list** — only purchase price is stored historical; selling price of the product has nothing — applied to purchase cost is fine in sales, but if the Owner wants multi de "display selling price history" that doesn't exist.
8. **Receivable/payable reporting graceables** — payment status is present, but real collection/reminders (ageing buckets, scheduled reminders) is a V2 growth.
9. **Cash-out giving compression & rounding** (see M above for motion).
10. **Reason/notes standard validation** — not always modeled isn't optional; it's a field. But for inventory and money events a "reason" is only "where appropriate" (per requirement) — good to keep consistent.
11. **Import/export of master data (products, customers)** — useful onboarding but future; not mentioned explicitly.
12. **Backup/restore and data-backup requirement** — audit section mentions "preserve history" but no explicit scheduled backup — worth listing.
13. **Transaction document numbers / numbering scheme** (invoice/purchase numbers) not mentioned — important for traceability; consider a simple document-number generator.

These are each low cost to *decide* now; several could be turned on/off via OPTIONAL.

---

## Contradictions (consolidated, independent of above severity)

- **Costing:** freeze vs recalculate (C1) — the deepest.
- **Staff export default** — §7 vs §8 (C2).
- **Cash over-tender** example vs validation (H2).
- **P&L numeric example** — §17 says COGS 30M; Appendix says 8M (Low/L).
- **"Produce finished path cost formula"** as described isn't coherent (H4).
- **§12 C4 tag** — mid-sentence contradicts of "definition choice" vs stated single requirement. (§12 line says "Invmc complete if using recommendation").

---

## Recommended Changes (priority order)

1. Resolve **C1** — pin cost method + freeze policy; store sale-line cost as a snapshot (immutable). Block DB design until done.
2. Resolve **C2** — fix Staff default permission list (including export) to one source of truth. Put it in the permission model section only, and reference it everywhere else.
3. Enforce **C3** — no in-place editing of paid/active financial transactions; use cancel/return/correction entries instead.
4. **H1** — separate automatic revenue/COGS from manual income/expense; remove "Sales"/"Purchase" from manual defaults (or else rule that manual entries cannot be the derived).+ 
5. **H2** — define cash over-tender as receive/alloc/change, and relax the ≤ total validation to apply only to *payments* (not tender).
6. **H3** — explicit per-event movement table for *every* stock-touching transition incl. cancellation, with a "movement inversion guard."
7. **H4/H5** — define finished-product COGS formula and negative-stock fallback cost.
8. **M1/M2,** — fix numbering no. 35, duplicated §11, and all cross-references.
9. **M3** — add a per-entity lifecycle (state machine) section separating "payment axis" from "status/lifecycle axis."
10. **M4/M5/M6** — model refund/cancel cash-out, purchase received status, and partial return money+stock pairing.
11. Decide the *Potential Missing* items that matter (esp. units & locations, discounts, session / X-Z, sale req IDs) in the next review.
12. After edits, sign off the final scope of "Costing Method and History freeze" — then hand to DB architecture.

---

## Final Decision Checklist — items genuinely needing stakeholder approval

(Removed anything already sufficiently defined — e.g., product code optional, latest-purchase-price display, partial/multi-payment, owner-default stock adjust are all confirmed do **not** require re-confirmation.)

1. **Costing method for v1 + freeze-on-switch policy** (C1) — *blocker*.
2. **Staff default permission set**, in particular **export** (C2) — *blocker* to the permission table.
3. **Correction policy**: will the Owner be allowed to edit a paid transaction in place, or must corrections be New cancel/return/correction entries? (C3).
4. **Manual vs derived ledger**: can manual income/expense include "Sales"/"Purchase" (→ double-count guard)? (H1).
5. **Cash over-tender / change handling** at POS & validation. (H2).
6. **Sale-cancellation outcome for stock** (goods returned vs not) and cancel/return semantics. (H3).
7. **Production finished-goods COGS basis** — raw materials only or + labor/gas/electric/etc. (H4).
8. **Negative-stock fallback cost** when the toggle is used. (H5).
9. **Supplier required or optional** on purchases. (H6).
10. **Are Warehouses/locations and units-of-measure conversion in scope?** (P5/P1) should be decided for V1 to avoid short/breaks later.
11. **Low-stock threshold rule** (per-product vs global). (M7).
12. **Product sell-only / buy-only flags** or universal buyable+sellable. (M9).

*(#3, #5, #6, #9, #10 are real business decisions; the rest shape data design before coding.)*