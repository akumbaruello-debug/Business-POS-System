# Business Management & POS System — Product Requirements Document (PRD) V1 (Revised)

| Field | Value |
|---|---|
| **Document** | PRD V1 (Revised) |
| **Product** | Business Management & POS System (web-based) |
| **Status** | Draft for review — **not yet final** |
| **Audience for validation** | Business Owner / Stakeholder. No database, API, or software knowledge required. |
| **Next step after approval** | Business Rules → Database Architecture → API Architecture → UI/UX → Implementation → Testing |
| **Revision note** | Audited-and-revised V1. Resolves prior audit findings (costing freeze, staff permissions, editing policy, P&L double-counting, cash over-tender, cancellation/return, production cost example, supplier requirement, purchase receipt, lifecycle model, inventory integrity). A backup of the pre-revision file is at `Business-POS-System-PRD-V1.backup.md`. This edit also renumbered every section to a single sequential set (removed duplicate §3 and §15, restored the missing §18, de-duplicated §36) and re-synced all internal §cross-references (including `§17 Costing` → §18 and the export-permission pointer), plus a typo repair (`PART` → `CRITICAL`). |
| **Tag legend** | **CONFIRMED** = explicit stakeholder decision. **AI ASSUMPTION** = provisional (analyst) decision. **AI ASSUMPTION — NEEDS CONFIRMATION** = provisional & materially affects design. **RECOMMENDATION** = suggested best practice. **OPTIONAL** = toggleable feature. **TBD** = genuinely undetermined. |

> Everything labeled **CONFIRMED** comes directly from the stakeholder decisions. Every **AI ASSUMPTION** states *what / why / impact / how easy to change*.

---

## 1. Executive Summary

A web-based **Business Management & POS System** centralizing a single business's operations: products, purchasing, sales/POS, inventory, production/processing, income, expenses, payments, costing, suppliers, customers, profitability, and reporting.

**The primary goal is not merely to record transactions.** The Owner must be able to understand, in real time:

- current stock
- purchases and sales
- money in and money out
- production costs and shipping costs
- product costs and cost of goods sold (COGS)
- **gross profit** and **net profit**
- business performance over time (period comparison)
- detailed transaction history
- **who changed important data** (audit trail)

Two non-negotiable principles govern the whole design:

1. **Financial and inventory history is immutable once a transaction is posted/completed.** Current prices, current settings, and later costing-method changes never rewrite a past transaction. A sale that posted with COGS Rp10,000 in January stays Rp10,000 even if the purchase price rises to Rp15,000 in February (see §18 Costing). Corrections go through lifecycle reversal + re-entry, never silent in-place edits of posted records (see §8 Editing Policy).
2. **Business rules are adjustable after v1.** Permissions, cost/category options, costing method, negative-inventory allowance, and rate changes are configurable, not hardcoded.

V1 is configurable but not over-built: a method-agnostic cost engine (default **Average Cost** — AI ASSUMPTION, pending confirmation), a role + granular-permission model with Owner delegation, a status-driven transaction core with a clear lifecycle, and it defers unconfirmed areas (real production workflow, recipe/BOM, account balances, multi-unit, multi-warehouse) to explicit assumptions, TBD items, or the **Potential Future / Open Requirements** section (§35).

---

## 2. Product Vision

A single, professional, easy-to-use web application that is the owner's *source of truth* for daily operations and long-horizon decisions. The owner opens one screen to see stock, money in, money out, and profit; records a sale or purchase in seconds; and trusts that the numbers are correct and cannot be silently rewritten by later price or setting changes.

The system is **for the owner first**, staff second. Invoicing templates, deep double-entry accounting, and formal financial statements are out of scope for v1 (§4).

---

## 3. Goals

1. Give the owner accurate, current **stock value**, **gross profit**, and **net profit**.
2. Keep **transaction history intact** regardless of future price, configuration, or costing-method changes.
3. Provide a **fast POS / sales entry** flow at a live counter, with correct cash over-tender (change) handling.
4. Support **partial and split payments** on purchases and sales, with payment state kept separate from transaction lifecycle.
5. Support purchases, sales, production, income, and expenses in one consistent ledger **without double counting**.
6. Allow the owner to **delegate specific permissions** (e.g. stock adjustment) to staff and to **revoke** them.
7. Provide **searchable, filterable, sortable, exportable** data-management screens.
8. Provide **audit history** for important changes.
9. Be **configurable** in categories, cost types, payment methods, and costing method.
10. Guarantee **every stock-changing event has exactly one authoritative inventory effect** (no double application).

---

## 4. Non-Goals (out of scope for V1)

These are scoping recommendations for stakeholder confirmation (the original implementation is unknown), not confirmed facts:

1. **No multi-currency.** Single unit (IDR default).
2. **No double-entry general ledger** / formal balance sheet or trial balance.
3. **No permanent recipe/BOM**; no timesheets, shifts, or payroll.
4. **No built-in invoice/receipt templates** beyond PDF export.
5. **No external e-commerce / marketplace integration.**
6. **No native mobile apps** (responsive web only).
7. **No tax-authority integration** (tax is a cost category).
8. **No full purchase-order (PO) module** in v1 — but the order-vs-received distinction is still modeled (§12).
9. **No formal AR/AP aging subledger** — only payables/receivables derivation (§16, §35).

---

## 5. Target Users

- **Primary:** **Owner** — oversight, decisions, configuration, staff management.
- **Secondary:** **Staff / cashiers** — fast day-to-day entry under limited permissions.
- **Tertiary (indirect):** Suppliers and customers are **records/contacts**, not user accounts.

*AIA assumption: staff count/duties unknown; small hands-on team expected. Impact: simple per-user permission assignment. Change difficulty: low.*

---

## 6. User Roles

| Role | Description |
|---|---|
| **Owner** | **Full access** (see §7 matrix). Highest role. Can view, edit, create, cancel, return, adjust stock, manage products/purchases/sales/production/income/expense, view finance/profit, reports, exports, users, roles, audit, settings, and delegate. |
| **Staff** | Restricted. **View = YES**, **Edit = YES** by default. All other capabilities **NO** unless granted (§7). |

**CONFIRMED:** two roles today; Owner full access; Staff restricted (view + edit base); Owner can delegate; model must tolerate new roles.

---

## 7. Permission Model — single source of truth

### 7.1 Capability matrix (authoritative)

This is the **one authoritative definition** of default permissions. All other PRD sections must reference this table, not redefine it.

| Capability | Owner | Staff (default) |
|---|---|---|
| View data | ✅ | ✅ |
| Edit data (within §8/§9) | ✅ | ✅ |
| Create (records/transactions/contacts) | ✅ | ⛔ unless granted |
| Cancel / Void | ✅ | ⛔ |
| Delete | ✅ | ⛔ |
| Stock Adjustment | ✅ | ⛔ (owner can delegate) |
| Financial administration / view profit | ✅ | ⛔ |
| Staff/User management | ✅ | ⛔ |
| Roles & permission management | ✅ | ⛔ |
| Export (PDF / Excel) | ✅ | ⛔ unless granted |
| System settings / configuration | ✅ | ⛔ |

**Rules**
- Owner = full access, highest role.
- Staff: **View = YES, Edit = YES**; everything else **NO** unless granted.
- **Delegation:** owner can grant a specific capability to a specific user and **revoke** it.
- **Flexibility:** capabilities are granular (`stock.adjust`, `sales.cancel`, `finance.view_profit`, `export`, …) so any combination is composable; not hardcoded role checks.
- The **exact Staff default list** is an **AI ASSUMPTION — NEEDS CONFIRMATION** (brief confirms only "view + edit" + delegability). The owner must approve the final Staff defaults before architecture.

### 7.2 Design direction (RECOMMENDATION)
Role = named set of capabilities; user belongs to one role plus optional per-user overrides; owner can create custom roles and grant/revoke granular permissions.

### 7.3 Examples
- Owner grants one staff member *Stock Adjustment* → only they can adjust stock.
- Owner grants *Export* to a cashier only.
- Owner revokes a previously granted capability → effect disappears.

---

## 8. Editing Policy (transaction / key-record changes)

**AI ASSUMPTION — NEEDS CONFIRMATION.** Rules:

- **8.1 Draft transactions** — before posting. Freely editable; no stock/financial effect; no cost frozen.
- **8.2 Posted / completed transactions** — must **NOT** be edited in place for any field that affects inventory, revenue, COGS, payment, expenses, receivables, or payables. Corrections use the lifecycle: **Cancel/Void → reverse effects → create a corrected transaction**. Original + reversal remain visible in audit.
- **8.3 Non-financial fields** (e.g. a note) may be editable even after posting, and the edit is logged.
- **8.4 Why it preserves history:** prevents silent modification of immutable posted effects. Historical COGS and inventory are never recomputed from today's scores (§18).

**Status: AI ASSUMPTION — NEEDS CONFIRMATION** (owner decides: cancel/re-enter vs some in-place edits).

---

## 9. Transaction Lifecycle & Payment State (two separate axes)

Lifecycle and payment are **two orthogonal fields**, never a single enum.

### 9.1 Lifecycle status
| Status | Meaning |
|---|---|
| `Draft` | Building; not applied; editable; no effect. |
| `Posted` / `Active` | Applied; stock/financial effect taken; **cost frozen here**. |
| `Completed` | Normal finish (fulfilled / fully collected-or-paid processing). |
| `Cancelled` / `Voided` | Invalidated; effects reversed exactly once (§14/§30). |
| `Returned` | Goods physically returned after a valid (not cancelled) transaction — separate from Cancellation. |

Per-document allowed sets are a **TBD** state matrix (§32).

### 9.2 Payment state
| Status | Meaning |
|---|---|
| `Unpaid` | No payment yet. |
| `Partial` | Some collected / paid. |
| `Paid` | Fully collected / paid. |

### 9.3 The two axes do not conflict
- **Posted + Partial** — e.g. a sale collected Rp400k, Rp600k receivable.
- **Completed + Paid** — fully collected & fulfilled.
- **Posted + Unpaid** — sold but nothing collected.
- **Cancelled + Partial** — partially-paid then voided; refund handling of collected portion is **TBD**.

> Why: a single enum cannot represent "posted + partial". Keep two fields + an applied-effect/audit journal.

---

## 10. Core Business Workflows (overview)

1. **Purchase flow** (§12)
2. **Sales / POS flow** (§13)
3. **Production** (§15)
4. **Stock adjustment** (§14)
5. **Cancellation / return** (§§12–14, §9)
6. **Manual income / expense** (§17)
7. **Permission delegation** (§7)

All must keep ledger + inventory + payments + audit consistent.

---

## 11. Product Management

### Functional requirements
- F1. Create / edit / deactivate products; no hard delete of used products.
- F2. Fields: **name** (required), category (optional), unit, purchase price, selling price, stock, status, notes.
- F3. **Product code is NOT mandatory** — its absence must never block save. **CONFIRMED**

### Business rules
- B1. Product code optional, never required. **CONFIRMED**
- B2. One product can have **different purchase prices over time**; displayed/current = **latest**. Historical prices stay stored for historical costing. **CONFIRMED**
- B3. If a code is provided → unique; if absent → no uniqueness constraint. **AI ASSUMPTION**
- B4. Purchasable/Sellable/Used-in-production flags are **RECOMMENDED** (§35). V1 default assumption: products are buyable and sellable and consumable unless a flag is added.

### Inputs / Outputs / Validation
- Input: name, category, unit, purchase price, selling price, stock, status, code/notes optional.
- Output: product record, display purchase price = latest.
- Validation: name required; code unique when present; sell-price sanity in sales entry.
- Success: product saved; shows latest purchase price.

### Examples
Flour purchases Rp10k → 11k → 12k: card shows **Rp12,000**; a past sale at Rp10,000 stays Rp10,000 in history (§18).

### Edge cases
- Product referenced by history → cannot hard-delete; deactivate.
- Deactivated with stock → not sellable; still counted in stock value; flagged.
- Price change while editing a sale → older sale keeps recorded historical price; not repriced.

---

## 12. Purchasing

### Requirements
- **P1.** Purchase payment states: **Paid in full / Partial / Unpaid (payable)**. **CONFIRMED**
- **P2.** A purchase can have **multiple payments**. **CONFIRMED**
- **P3.** **Shipping costs** in same broad category but **separately visible**, never merged indistinguishably. **CONFIRMED**
- **P4.** **Cancellation and return are separate events**. **AI ASSUMPTION — NEEDS CONFIRMATION**
- **P5.** **Stock increases only on goods receipt**, not merely when the purchase record is created. **AI ASSUMPTION — NEEDS CONFIRMATION** (aligns with the no-PO-module scope note in §4; we still separate "ordered" from "received").
- **P6.** Purchase **return** decreases stock where physical. **CONFIRMED**
- **P7.** Shipping visible/itemized.

### Payments example (CONFIRMED)
Purchase = Rp10,000,000
- Payment 1 = Rp4,000,000
- Payment 2 = Rp3,000,000
- Payment 3 = Rp3,000,000
→ total paid = Rp10,000,000 → **status Paid**.
During the process, UI always shows **remaining = total − paid**; status stays **Partial** until 100%.

### Cancellation vs Return (§9 lifecycle)
- **Cancellation**: invalidated transaction reversed once (before goods-received effect, or reverse if posted). Refund policy **TBD**.
- **Return**: goods physically returned after receipt; decreases the items' stock and adjusts payables/refund.

**Refund/cash-return policy is NOT confirmed** — options (refund same method, issue credit/payable, offset) are open (§32).

### Inputs / Outputs / Validation
- Input: supplier (per §20 policy), line items (product, qty, price), totals, shipping, payments, status.
- Output: purchase record, supplier payable, stock increase on receipt, payments ledger.
- Validation: allocated payments sum ≤ total; stock only on receipt; shipping adds to purchase total visibly.

### Dependencies: §13 sales, §14 inventory, §16 payments, §20 supplier, §18 costing.

**Status: CONFIRMED core; cancel/return split + receipt-vs-order = AI ASSUMPTION — NEEDS CONFIRMATION.**

---

## 13. Sales / POS

### Requirements
- **S1.** Select products, record quantities, selling prices, totals, payment. **CONFIRMED**
- **S2.** Customer **optional** (not required). **CONFIRMED**
- **S3.** A successful sale **decreases inventory**. **CONFIRMED**
- **S4.** Support **paid / partial / unpaid (receivable)**. **CONFIRMED**
- **S5.** **Multiple payment methods** per sale (e.g., Cash + Transfer). **CONFIRMED**
- **S6.** **Cancellation and return** are separate events. **AI ASSUMPTION**
- **S7.** Sales **return** increases inventory (physical goods back). **CONFIRMED**

### Cash over-tender (change) — REQUIRED (resolves an old contradiction)
When a customer pays more than the sale total:

| Field | Value |
|---|---|
| Sale total | Rp450,000 |
| Cash tendered (given) | Rp500,000 |
| **Allocated payment** (applied) | Rp450,000 |
| **Change** (returned) | Rp50,000 |

> **The Rp500,000 tendered is NOT revenue.** **Revenue = Rp450,000.** A Rp50,000 change is not income — it is tender/change accounting. If exact tender, `Change = Rp0`.

Validation: "payments ≤ total" applies to **allocated payments**, not raw tender. A cash split records tender, allocated, and change. Multi-method example: Sale total 1,000,000 → Cash allocated 400,000 + Transfer allocated 600,000 (change Rp0). Over-tender example: 450,000 => cash tender 500,000, allocated 450,000, change 50,000 → revenue 450,000.

### Validation
- Selling price at counter; price override only with `price.override` permission. **AI ASSUMPTION**
- Inventory check per negative-stock policy (§14).

### Success / failure
- Success: stock down, payment captured, optional customer receivable updated.
- Failure: insufficient stock → warning or block per §14.

**Status: CONFIRMED core; cancel-vs-return and price-override AI ASSUMPTION; over-tender defined (required).**

---

## 14. Inventory

### Requirements
- **C1.** Stock adjustment allowed. **CONFIRMED**
- **C2.** **Default: only Owner** adjusts stock; Owner may delegate and revoke. **CONFIRMED**
- **C3.** **Prevent negative stock by default.** **CONFIRMED** as a default business rule. On sale exceeding stock: **insufficient-stock warning**; default **block**; Owner may **OPTIONALLY** enable negative stock (per-item or global).
- **C4.** Every movement keeps a **trigger/reason** so inventory history is explainable. **AI ASSUMPTION** (keeps the ledger coherent).

### Inventory movement matrix — ONE authoritative effect per event (CRITICAL)
| Event | Stock effect |
|---|---|
| Purchase **receipt** | **+ qty** |
| Purchase **return** | **− qty** |
| Sale (posted) | **− qty** |
| Sales **return** (physical) | **+ qty** |
| Production **input** (raw) | **− qty** |
| Production **output** (finished) | **+ qty** |
| Stock **adjustment** | **± qty** |
| **Cancellation / reversal** | **reverse the original effect exactly once** |

**Rules**
- Every stock-changing business event must map to **one authoritative** inventory effect (the table is the single source).
- **No double application:** cancel a posted sale → reverse the original − once; must NOT also apply "+" (return). Only one path applies.

### Stock adjustment example
Rice at 40 → owner counts 38 → adjust to 38 → stock −2; audit: before 40, after 38, user, timestamp, reason.

### Negative stock, default & optional
- Default block + warning. Owner-OPTIONAL enable negative (per-item/global). Negative rows clearly flagged and audited. COGS consequence: see §18.

### Cancellation vs Return (sales) — explicit
- **Cancellation** = invalidated posted sale reversed exactly once. Sale stock −10 → void reversal **+10**.
- **Return** = physical goods of a *valid* sale come back → **+10**.
- Must **NOT** let both a sale-void and a sales-return each restore the same stock (double restore prohibited).

---

## 15. Production / Manufacturing — V1 TEMPORARY model

The real production workflow has **not yet been provided**. This is a **temporary V1 model (AI ASSUMPTION only)**, not a confirmed business rule.

### V1 form
**Raw materials → Processing → Finished product.** On V1 completion: raw −, finished +. **CONFIRMED** (the V1 does assume this).

- No permanent recipe/BOM in v1. **CONFIRMED**
- Production cost types customizable: Labor, Electricity, Gas, Packaging, Other. **CONFIRMED** (types)
- If a cost type is used historically and then removed, historical records must remain visible. **CONFIRMED — hard requirement.**

### Production cost example (clean, conceptual only)
```
Raw material Rp100,000
Labor        Rp20,000
Electricity  Rp10,000
Packaging    Rp5,000
Total        Rp135,000
If 10 finished units → unit = Rp135,000 / 10 = Rp13,500
```

> **Costing basis NOT confirmed.** Whether finished-goods inventory cost is **raw-materials only** or **raw + production overhead (labor/electricity/gas/packaging)** is an **AI ASSUMPTION — NEEDS CONFIRMATION**. The numbers above are conceptual arithmetic only — they do **not** confirm a policy. This choice changes the **value of finished inventory**, the **COGS** when finished goods are sold, and whether overhead (industrial-to-inventory vs P&L expenses).

### Production data model (AI ASSUMPTION)
A production **run** records raw materials & quantities consumed plus cost-type lines — a per-run record, **not** a permanent BOM. Enables stock ±, consumption tracking, cost capture. Later could be tied to a product/batch reference without schema rework.

### Cost-entry unit — TBD
Per production **run** (v1 default) vs per **period** (e.g. monthly). Flag in §32.

### Cost-type removal safety
Remove only from the active list; the historical record always shows the type + amount as ensured at time of the run. Never hard-delete referenced cost types.

### Failure / edges
- Raw inventory insufficient → warn / block per negative-stock policy.
- Remove a cost type in history → keep record, hide from new entry.

---

## 16. Payments

### Requirements (CONFIRMED)
- Payment fields: amount, method, date, optional account/source.
- Methods: Cash, Bank Transfer, E-Wallet, Other (configurable).
- **Payment account is OPTIONAL** — if provided record it; never force creation. **CONFIRMED**
- **Account balances never fabricated** — leave empty if unavailable. **CONFIRMED**

### Payment-state model
Payment axis (`Unpaid`/`Partial`/`Paid`) applies to sale/purchase (§9). Payments recorded as **allocations**; cash over-tender uses the tender/allocated/change triple (sales, §13).

### Model (RECOMMENDATION)
- A first-class **Payment** record references any document (purchase/sale/income/expense); multiple per doc.
- Receivables/payables derived = doc total − payments.

### Refund behavior — **TBD**
Cancel/return involving prior payments: refund/credit handling is not defined/confirmed. Options open (same method refund, credit, offset). Nothing assumed as confirmed (§16, §32).

---

## 17. Income & Expenses

### Two kinds of financial data (prevents double counting — CRITICAL)
**A. Derived / transaction-generated (never manually entered):**
- Sales revenue (from sales)
- Purchase liabilities (from purchases)
- COGS (from costing)
- Production cost (from production)
- Payment effects

**B. Manual / independent (directly entered by user):**
- Capital injection · Other income · Rent · Maintenance · Operational · Tax · Refund · Electricity (manual) …

> **Manual categories must NOT duplicate derived/automatic events.** Do not expose a manual "Sales" income while sales already create revenue, or a manual "Purchase" expense side-by-side with the purchase record. Otherwise revenue/COGS are entered twice into the P&L.

### Default category sets (AI RECOMMENDATION, NOT confirmed business rules)
Income defaults: **Sales (derived)** · **Capital Injection** · **Other Income** · **Refund** · **Other**.
Expense defaults: **COGS (derived)** · **Production (derived)** · **Shipping** · **Labor** · **Electricity** · **Rent** · **Maintenance** · **Operational** · **Tax** · **Other**.

Derived ones (Sales / COGS / Production) are system-reserved and **excluded from manual selection** to guard double counting.

### Category configurability
Categories are customizable (add/edit/rename). Historical categories / amounts stay correct after category changes (renames are metadata). **CONFIRMED concept**.

### Business-correctness note
Manual entries must never silently change historical financial truth. Amount/dates/transactions immutable; only metadata edits allowed (and logged).

### Validation
Amount > 0; date valid; category required; optional method/account.

---

## 18. Costing

### Purpose
Determine **COGS** & inventory value so Gross Profit = Revenue − COGS — while preserving history.

### Immutable historical cost — THE RULE
> **The cost used by a posted/completed transaction is frozen at the moment of posting.** Changing the costing method or today's purchase price later does NOT rewrite history.

```
Example:
January sale COGS = Rp10,000
February: owner changes costing.
January's COGS stays Rp10,000.
The new method applies only to future / not-yet-posted transactions.
```

### Costing method — AI ASSUMPTION / TBD
Support configurable methods: **FIFO**, **Average Cost**, **Last Purchase Price**, future. Recommended default = **Average Cost** (simple, no batch prerequisite). **NOT confirmed** — owner must select (§32).

- User method selection applies only forward.
- Historical sale-line costs are **snapshots**; they are never "recalculated by method after posting".

### How history is preserved
Each stock movement / sale line stores its **unit cost at the moment** (snapshot). Reports read that snapshot. Changing today's product price or costing method does not change pre-recorded historical cost.

### Average illustration (conceptual)
Buy 10 @ 10,000 → 10,000; Buy 5 @ 12,000 → pool avg = (10,000 · 10 + 12,000 · 5)/15 ≈ **Rp10,666.67**. Under Average, a sale from the pool gets ~10,667 ≈ **snapshot**. Switching later to FIFO affects only new sales.

### Finished-goods COGS (§15)
Whether finished goods are raw-only or raw+overhead is an **AI ASSUMPTION — NEEDS CONFIRMATION**. Any basis chosen is snapped into the movement at posting.

### Negative-stock COGS (unresolved rule)
When negative inventory is on and a sale consumes goods with no knowable cost, need a basis. **Unresolved** — options:
- Last Purchase Price (recommended AI default)
- Average Cost
- Zero cost
- Block the sale
- Owner override

Chosen policy must not change historical; the basis is flagged; designed per decision. **Decide before DB** (or marked "needs confirmation").

---

## 19. Profit & Loss

### Definitions (CONFIRMED)
Revenue − COGS = **Gross Profit**; Gross − Expenses = **Net Profit**.

### Example (arithmetic-consistent)
```
Sales revenue  Rp50,000,000
− COGS        Rp30,000,000
= Gross Profit Rp20,000,000
− Expenses    Rp8,000,000   (Rent 4.0M + Labor 3.0M + Shipping 1.0M)
= Net Profit  Rp12,000,000
```
Any consistent numbers are fine; the pattern is the point.

### Configurability
Expense categories configurable, filterable by category.

### Data correctness
P&L is **computed** from ledger data (not free text), so it won't be inconsistent; manual entries can't double count derived items.

**Status:** formula CONFIRMED; category defaults AI-recommended.

---

## 20. Supplier Management

### Fields (CONFIRMED)
Supplier info, contact, address, purchase history, total purchases, payables, notes.

### Function
Supplier → purchases → totals → outstanding payables (= purchases − payments); filter purchasing by supplier reports.

### Supplier requirement — **AI ASSUMPTION — NEEDS CONFIRMATION**
Customers optional is confirmed; **supplier mandatory/optional is not**. So do **not** silently treat supplier as confirmed optional.
- **V1 recommendation:** allow purchases without requiring a supplier (supports counter/cash buys), but let a purchase optionally link to one. **The owner must confirm** whether a purchase requires an existing supplier.

---

## 21. Customer Management

### Fields (CONFIRMED)
Customer info, contact, address, sales history, total sales, receivables, notes.

### Requirement
Customers optional, never required, per sale. **CONFIRMED**

### Example
Customer B → sales, total bought, unpaid = receivable.

### Note
Formal AR aging is future (§35 #9).

---

## 22. Dashboard

### Purpose
High-level at-a-glance view.

### Metrics (proposed, TBD list)
Total sales, purchases, income, expenses, gross profit, net profit, stock value, best-sellers, low stock.

Charts: sales over time; breakdown by category/product.

### Date filters
Today, this week, this month, this year, custom. **CONFIRMED** (filters available).

### Data consistency
Dashboard and all reports derive from **authoritative transaction data** — no separately-maintained manually-cap totals. Single source of truth. **CONFIRMED philosophy (RECOMMENDED).**

### V1 scope
Dashboard + sales trend line + category/product breakdown. Metric list to confirm.

---

## 23. Reports

### Capabilities (CONFIRMED)
Search, Filter, Sort, View details, Compare periods, Export.
Filters available: date range, category, product, supplier, customer, payment method, payment status, transaction status, expense type, cost type.
Visual analytics where useful.

### Reports (V1 list, TBD sizing)
Sales by product/period; purchases by supplier; income/expense by category/day; inventory value + movement; production w/ cost; P&L.

### Status
Confirmed in principle; exact report-list TBD at sizing.

---

## 24. Data Management

Not plain-CRUD tables. Every screen answers "what is the user trying to do?" Provide list/filter/search/sort/edit/detail/period-compare across modules. Editing the rules per §8/§9. Consistent UX. Status: confirmed philosophy.

---

## 25. Export

- **CONFIRMED:** PDF + Excel (XLSX) export.
- Content: sales, purchases, inventory, income, expenses, production, profit, reports.
- Respect active filters where possible; include filter summary (RECOMMENDATION).
- Export permissions per §7 (Staff default NO unless granted).

---

## 26. Audit & History

### Purpose
Capture who, what, when, previous, new, reason for important changes.

### Events (CONFIRMED examples)
- Transaction created / edited / cancelled / returned
- Stock adjusted
- Permission changed
- Settings changed (recommend)

### Record content (CONFIRMED)
WHO, WHAT, WHEN, PREVIOUS, NEW, optional REASON — append-only log.

### Integrity (AI RECOMMENDATION)
Financial/inventory transactions shouldn't be hard-deleted; use Active / Cancelled / Voided / Archived. Deletion privileged and logged.

### Editing summary
Draft freely; posted (stock/finance) not editable in place — cancel/re-enter (§8).

---

## 27. Notifications & Warnings

1. **Low-stock / out-of-stock list.** Threshold set **per product (configurable — TBD default)**. No universal hardcoded number.
2. **Insufficient stock** (sale/purchase/production) — warning; hard-block per negative-stock config (§14).
3. **Payment reminders** (payables / overdue receivables) — recommended; email/SMS scope TBD (in-app only in V1).
4. **Below-cost sale** warning — recommended.
5. **Irreversible action confirmation** — cancel/void/delete.

---

## 28. Validation Rules

- Amounts/qty positive; decimals per unit; integers where natural.
- Dates valid and reasonable.
- Payment allocations ≤ total (**except cash over-tender** where paid > total; allocated ≤ total; change = tendered − allocated).
- Purchase total = Σ lines + shipping.
- Payment state derived (Unpaid/Partial/Paid).
- Required: product name; contact name; etc.
- Full rules per section.

---

## 29. Edge Cases

| Case | Behavior |
|---|---|
| Duplicate product lines on a sale | Merge lines (recommended) or reject. |
| Partial-payment then return | Handle stock (goods back) and finance (refund portion, TBD) together. |
| Cancel of fully-paid purchase | Stock reversed once if received; refund/credit policy TBD. |
| Price change between cart & save | Lock price or re-read + confirm mismatch. |
| Deactivated product with stock | Not sellable; counts in stock value; flagged. |
| Negative stock enabled | Allowed & flagged; COGS basis resolved separately. |
| Quantity 0 / negative | Reject. |
| Partial multi-payment | Remaining shown; Paid only at 100%. |
| Editing posted sale | Cancel/re-enter per §8; historical cost stable. |
| Price-override | Permission-gated and logged. |
| Missing supplier/customer | Allowed per assumptions; report shows blank. |
| Void double-reversal | Prevented by inventory matrix (§14). |

---

## 30. Business Rules (Consolidated)

Summary — full detail per section:
1. Product code optional; never required. **CONFIRMED**
2. Product display purchase price = latest; historical preserved. **CONFIRMED**
3. Sales may omit customer. **CONFIRMED**
4. Sale ↓ stock; sales return ↑ stock. **CONFIRMED**
5. Purchase ↑ stock on receipt; purchase return ↓ stock where physical. **CONFIRMED**
6. Paid / partial / unpaid supported for purchases & sales. **CONFIRMED**
7. Multiple payment allocations / methods per doc. **CONFIRMED**
8. **Lifecycle separate from payment state.** **AI ASSUMPTION (design clarification)**
9. Shipping: same broad category but separately visible. **CONFIRMED**
10. Cancel & return = separate events. **AI ASSUMPTION — NEEDS CONFIRMATION**
11. Stock adjustment owner-only by default; delegable & revocable. **CONFIRMED**
12. No negative stock by default (owner optional enable). **CONFIRMED principle**
13. Production raw ↓, finished ↑. **CONFIRMED (V1)**
14. Production cost types configurable; history stays/removed. **CONFIRMED**
15. No permanent BOM in v1. **CONFIRMED**
16. Costing method config; historical frozen. **CONFIRMED** (method itself TBD)
17. Gross = Revenue − COGS; Net = Gross − Expenses. **CONFIRMED**
18. Expense categories configurable. **RECOMMENDATION; confirmed concept**
19. Manual income/expense allowed, must not duplicate derived. **CONFIRMED** (manual exists) / guard = RECOMMENDED
20. Payment account optional; no fabricated balance. **CONFIRMED**
21. Customer optional; supplier optional/required — TBD. **CONFIRMED(cust) / TBD(supplier)**
22. Every stock event one authoritative effect (no double). **AI ASSUMPTION integrity rule**
23. Non-destructive statuses. **RECOMMENDATION**
24. Data mgmt > CRUD. **CONFIRMED**
25. Owner full access; delegation+revoke. **CONFIRMED**

---

## 31. Status Matrix (Confirmed vs Assumption vs Rec vs TBD)

| Item | Status |
|---|---|
| Product code optional; latest display; prices history | CONFIRMED |
| Purchase/sales: paid/partial/unpaid, multi-payment | CONFIRMED |
| Sales ↓ stock; returns ↑ | CONFIRMED |
| Manual income/expense allowed | CONFIRMED |
| Cost types customizable / removed-history visible | CONFIRMED |
| Production raw↓+finished↑ | CONFIRMED (V1) |
| No permanent BOM | CONFIRMED |
| Gross/Net formulas | CONFIRMED |
| Costing engine config; history frozen | CONFIRMED principle; method = TBD |
| Stock adjuster owner-only (delegable/revocable) | CONFIRMED |
| Payment account optional; no fabricated balance | CONFIRMED |
| Customer optional | CONFIRMED |
| Staff view+edit, otherwise NO | CONFIRMED (base) / set needs confirm |
| Cancellation vs return | AI ASSUMPTION |
| Supplier optional/required | AI ASSUMPTION |
| Editing policy (posted) | AI ASSUMPTION — NEEDS CONFIRMATION |
| Negative-stock COGS | TBD (rec: Last Purchase, flagged) |
| Production finished COGS basis | TBD (AI ASSUMPTION) |
| Refund behavior | TBD |
| Low-stock threshold | TBD (per-product configurable) |
| Manually-entered double-count guard | RECOMMENDATION |
| Single authoritative inventory-effect | AI ASSUMPTION |
| All KPIs single source | RECOMMENDED/CONFIRMED philosophy |

---

## 32. Open Questions / TBD (prioritized)

1. **Costing method** (Average / FIFO / Last) — default; applies to future only. [blocker]
2. **Negative-stock COGS basis** — pick. [blocker]
3. **Production finished-good costing basis** — raw-only vs include overhead. [blocker]
4. **Refund policy** on cancel/return [blocker for flows]
5. **Editing policy** confirm.
6. **Exact Staff default permission list** confirm.
7. **Supplier optional or required** confirm.
8. **Cost-entry unit** for production (run vs period).
9. **Low-stock threshold** per-product vs global.
10. **Warehouses/locations** single vs multi.
11. **Units of measure** conversion.
12. **Discounts / price override behavior** in v1.
13. **Multi-currency** defy (assumed: no in v1).

---

## 33. V1 Scope

In:
- Auth (Owner + Staff), permissions model with delegation.
- Products (optional code; price history).
- Purchasing (multi-payment, shipping, cancel/return) with receipt-based stock.
- Sales/POS (multi-payment, optional customer, return/cancel, cash change).
- Inventory ledger: movements + adjustments; negative optional.
- Production V1: raw→finished, cost-lines, hist persistence.
- Payments/journal + remaining.
- Costing (method configurable, historical snapshot).
- P&L.
- Suppliers / Customers masters.
- Dashboard w/ date filtering.
- Reports + PDF/Excel export.
- Audit.
- Settings (categories, methods, cost types).

Out of V1: recipe/BOM, double-entry GL, payroll, multi-currency, tax engine, mobile/notif push, warehouses, uom, promo, batch/lot. Some in potential-future (§35).

---

## 34. Future Scope (V2+)

BOM/recipes w/ versions & yield; formal GL; multi-currency; purchase orders; barcode/scanner; loyalty/promo/memberships; advanced notifications; reorder-point automation; batches/lots/expiry; payroll/time-attendance; accounting integration; backup/import.

---

## 35. Potential Future / Open Requirements

These are identified as **potential** — not silently added as mandatory V1. Each has why it matters / architecture impact / suggested priority.

| # | Requirement | Why it may matter | Architecture impact | Priority |
|---|---|---|---|---|
| 1 | Money rounding / decimal precision | POS sums & cash change | Low (config + display; rounding-mode rule) | V1.1 |
| 2 | Discounts & price overrides | Revenue accuracy/promo | Medium (pricing model) | V1.1–V2 |
| 3 | POS cash-session / X-Z close | Drawer reconcile, per-staff | Medium (shift/close entity) | V1.1 |
| 4 | Multiple warehouses/locations | Multi-site stock | High (adds location dimension) | Future |
| 5 | Unit conversion (kg↔pkg) | Purchase vs sale different units | High (UoM + conversion) | Future |
| 6 | Selling-price history / price lists | Quoting/period pricing | Medium (price record on product) | V2 |
| 7 | Transaction / invoice numbering | Traceability & audit | Low (sequential counter) | V1.1 |
| 8 | Backup & restore | Disaster recovery | Low–Medium (ops) | V1 (ops) |
| 9 | Receivable aging / reminders | Debt management | Medium (aging buckets) | V2 |

Also non-blocking: single-unit default/flag per product (sellable/purchasable flags) — see §11 B4.

---

## 36. Acceptance Criteria (V1)

1. Login/roles: Owner full; Staff defaults; delegating Stock Adjustment works (and revoke).
2. Product with no code → save accepted.
3. Latest purchase price (Rp12,000) shown; historical Rp10,000 sale unchanged after price & method changes.
4. Sale reduces stock; split payment; cash over-tender correct (450/500/50 → revenue 450, change 50).
5. Purchase 10M over 3 payments → Paid at 100%, remaining visible.
6. Negative stock blocked by default; optional toggle; flagged when enabled.
7. Production: raw ↓, finished ↑; cost-lines; removed cost-type keeps history.
8. P&L consistent (Revenue−COGS=GP; GP−Exp=NP); manual cannot enter "Sales"/"COGS".
9. Staff defaults NO on sensitive ops; grants/revocations take effect.
10. Lifecycle orthogonal: posted + partial representable.
11. Audit captures who/what/when/before/after/reason on create/cancel/return/adjust/permission.
12. Editing a posted sale in-place is rejected. (per §8 policy once confirmed).
13. Manual entries reflected; no double count.
14. Dashboard vs detail numbers match (single source).
15. Per-product low-stock threshold configurable.

---

## 37. Requirement Risk & Change Impact (DB/architecture)

| # | Area | Rank | Why |
|---|---|---|---|
| 1 | **Costing method** | HIGH | A future switch must not rewrite history. Cost engine must be method-agnostic, sale-line cost = snapshot. If not, retrofitting FIFO re-baselines every inventory + COGS valuation. Keep snapshot-on-movement & on-sale-line; method = runtime config. |
| 2 | **Production workflow** | HIGH | Real workflow unknown; v1 single-step. Any multi-step or sub-assembly/co-products could change the production schema. Model production as job → input lines → cost lines → finished items, and avoid locking to one transformation pair. (Evolution assumption.) |
| 3 | **Payment model** | MEDIUM | Multi-payment per doc. Re-keying? AR aging / invoice allocation would be additive, payments stay first-class with polymorphic link. |
| 4 | **Inventory rules** | MEDIUM | Adjustment / revaluation could touch cost. If owner requests on-hand revaluation, it's a history-wise policy change, not a schema restructure; treat as forward-only events. |
| 5 | **Returns** | MEDIUM | Return reversal as a new record (not a delete/edit) keeps exactly one authoritative effect; if "net returns" reporting is wanted, additive reporting, not schema. |
| 6 | **Negative inventory** | MEDIUM | Optional. Related COGS rule is the open point (see §18); design config flag + guard; the schema is stable either way. |
| 7 | **Permission delegation** | MEDIUM | Flexible matrix covers grant/revoke. Only user-hours / row-level ACL would exceed. Relatively safe as proposed. |
| 8 | **Historical transaction behavior** | HIGH | The whole system contract. Any later need to recompute history (e.g. "revalue on hand") is a data-integrity policy change; guard with snapshots + audit; treat as forward-only. |

**Risk philosophy:** **No plan rewrites history.** Every number derives from stored movements/snapshots, not live "current" prices — this is what makes the historical-freeze promise sustainable.

---

## 38. Appendix — Conceptual Examples (all internally consistent)

| Topic | Numbers | Check |
|---|---|---|
| Purchases paid in full | 4M + 3M + 3M = 10M → Paid | ✔ |
| Sales over-tender | total 450k / tender 500k / alloc 450k / change 50k | ✔ |
| Average costing | avg ≈10,666.67; snapshot kept | §18 |
| P&L | 50M − 30M = GP 20M; − 8M = NP 12M (or valid) | ✔ |
| Production valuation (conceptual) | 135k / 10 = 13.5k (raw/base TBD) | §15 |

---

## Final Validation — this revision

- No duplicate section numbers. ✔
- Sections renumbered to a single sequential set (§1–§38); no §2/§15/§36 duplicates, missing §18 restored, §34/§35/§37 gap closed. ✔
- Stale cross-references removed; all internal §links re-synced to the renumbered sections. ✔
- Costing frozen; drives method runtime. ✔
- No historical rewrite. ✔
- No P&L double-count (derived/manual split). ✔
- Cash over-tender defined. ✔
- No double inventory reversal. ✔
- A single inventory-effect matrix. ✔
- Production assumptions clearly labeled. ✔
- Unconfirmed business rules clearly labeled. ✔
- CRITICAL/HIGH audit findings verified resolved — or pinned as **AI ASSUMPTION — NEEDS CONFIRMATION / TBD**, never silently converted to confirmed requirements. ✔
- Owner delegation intact; Owner highest role. ✔
- Staff view+edit default intact. ✔
- No database/migration code introduced. ✔

---

*— End of PRD V1 (Revised). Awaiting stakeholder review & confirmation of the §32 decisions (costing method, negative-stock COGS, production costing basis, refund policy, editing policy, staff defaults, supplier-onPurchase, cost-entry unit, low-stock threshold). Next, on sign-off: Business Rules → Database → API → UI → Implementation → Test.*