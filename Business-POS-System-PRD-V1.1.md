# Business Management & POS System — Product Requirements Document (PRD) V1.1

| Field | Value |
|---|---|
| **Document** | PRD V1.1 (Finalized) |
| **Product** | Business Management & POS System (web-based) |
| **Status** | Proposed current source of truth — supersedes PRD V1 for all downstream phases |
| **Audience** | Business Owner / Stakeholder / Business-Rules extraction / Architecture |
| **Next step** | Business Rules → Database Architecture → API → UI/UX → Implementation → Testing |
| **Tag legend** | **CONFIRMED** = explicit owner decision carried from the brief. **V1 ASSUMPTION** = a decision required to make V1 implementable, taken because no further owner clarification will occur, with rationale stated. **RECOMMENDATION** = suggested best practice. **OPTIONAL** = toggleable feature. **TBD** = genuinely undetermined but non-blocking; decide at implementation. |

> **Reading note.** Everything labeled **CONFIRMED** is carried from the original owner decisions unchanged. Every requirement that was previously unresolved but must be decided to build a correct V1 is now made explicit and labeled **V1 ASSUMPTION** — it is never presented as a confirmed owner requirement. Where a V1 assumption changes a value (shipping, refund, finished-goods basis), the affected sections were updated so the document tells one internally consistent story.

---

## 1. Executive Summary

A web-based **Business Management & POS System** centralizes a single business's operations: products, purchasing, sales/POS, inventory, production, income, expenses, payments, costing, suppliers, customers, profitability, and reporting.

The primary goal is not merely to record transactions. The owner must be able to understand, in real time:

- current stock (on-hand quantity)
- purchases and sales
- money in and money out (payments received and paid)
- production and shipping costs
- product cost and cost of goods sold (COGS)
- gross profit and net profit
- business performance over time (period comparison)
- detailed transaction history
- who changed important data (audit trail)

### Master principles (non-negotiable)

1. **Financial and inventory history is immutable once a transaction is posted.** Current prices, current settings, and later costing-method changes never rewrite a past transaction. A sale that posted with COGS Rp10,000 in January stays Rp10,000 even if the purchase price later rises to Rp15,000 in February (see §18 Costing). Corrections go through lifecycle reversal and re-entry, never silent in-place edits of posted records (§7, §9).
2. **Every stock-changing event has exactly ONE authoritative inventory effect.** No double application and no double restore (§14).
3. **Business rules are configurable, not hardcoded.** Permissions, categories, cost types, payment methods, costing method, and negative-inventory allowance are adjustable after V1.

V1 is configurable but not over-built: a method-agnostic cost engine (default **moving average cost** — V1 Assumption), a role plus granular-capability permission model with owner delegation, a status-driven transaction core with a clear lifecycle, and clearly separated derived and manual financial ledgers. The system is for the owner first, staff second.

---

## 2. Product Vision

A single professional, easy-to-use web application that is the owner's source of truth for daily operations and long-horizon decisions. The owner opens one screen to see stock, money in, money out, and profit; records a sale or purchase in seconds; and trusts that the numbers are correct and cannot be silently rewritten by later price or settings changes.

---

## 3. Goals

1. Give the owner accurate, current stock value, gross profit, and net profit.
2. Keep transaction history intact regardless of future price, configuration, or costing-method changes.
3. Provide a fast POS / sales-entry flow at a live counter, with correct cash over-tender (change) handling.
4. Support partial and split payments on purchases and sales, keeping payment state separate from transaction lifecycle.
5. Support purchases, sales, production, income, and expenses in one consistent ledger without double counting.
6. Allow the owner to delegate specific permissions (for example stock adjustment, export) to staff and to revoke them.
7. Provide searchable, filterable, sortable, exportable data-management screens.
8. Provide audit history for important data changes.
9. Be configurable in categories, cost types, payment methods, and costing method.
10. Guarantee every stock-changing event has exactly one authoritative inventory effect.

---

## 4. Non-Goals (out of scope for V1)

1. No multi-currency — single unit of account, IDR default.
2. No double-entry general ledger, balance sheet, or trial balance.
3. No permanent recipe/BOM; no timesheets, shifts, or payroll. Production in V1 uses a simple raw-to-finished model (§15).
4. No built-in invoice or receipt templates — PDF export only.
5. No external e-commerce or marketplace integration; no barcode/scanner; no loyalty, promo, or membership engine.
6. No native mobile apps (responsive web only).
7. No tax-authority integration — tax is a cost category only.
8. No full purchase-order (PO) module — but the ordered-versus-received distinction is modeled (§13).
9. No formal AR/AP aging sub-ledger — only derived payables and receivables (§16).
10. No multi-warehouse or multi-location feature in V1 — a single implied stock location. Engineering may seed a nullable location identifier for later, but V1 ships no locations UI.
11. No batch, lot, or expiry tracking; no master-data import/export beyond PDF/Excel reports; no drawer cash-session / X-Z shift close.
12. No formal two-step approval workflow — sensitive actions are performed directly by an authorized user.

---

## 5. Target Users

- **Primary:** Owner — oversight, decisions, configuration, staff management; full access.
- **Secondary:** Staff / cashiers — fast day-to-day entry at the POS under limited, owner-granted permissions.
- **Tertiary (indirect):** suppliers and customers are master records / contacts, not user accounts.

V1 assumption: a small hands-on staff is expected; simple per-user permission assignment suffices.

---

## 6. User Roles

| Role | Description |
|---|---|
| **Owner** | Full access by default (matrix §6.1). Highest role. Can view, edit, create, cancel, return, adjust stock, manage products/purchases/sales/production/income/expense, view finance and profit, reports, exports, users, roles, audit, settings, and delegate. |
| **Staff** | Restricted operational role. Defaults ON: view, create sales at the POS, create purchases, and edit own drafts. Every other capability is OFF unless the owner grants it. |

**CONFIRMED.** Two roles today; Owner full access; Staff restricted; Owner can delegate and revoke; the model must tolerate new roles later.

**Clarification (resolves audit CT-1):** a Staff member who operates the POS must be able to record a sale and accept payment — that is the ordinary cashier workflow, not a privileged action. The Create capability is therefore split so `sale.create` and `purchase.create` are default-ON for Staff, while sensitive actions (cancel, void, return, stock adjust, financial view, export, master-data edit, settings, roles, delete) stay OFF unless explicitly granted.

---

## 6.1 Permission Model — single source of truth

This is the one authoritative definition of default permissions. All other sections reference §6 and must not redefine it.

| Capability | Owner | Staff (default) | Notes |
|---|---|---|---|
| View data | ✅ | ✅ | |
| Edit own draft (unposted) records | ✅ | ✅ | Posted records governed by §7 |
| Create: Sale / POS record | ✅ | ✅ | Normal cashier workflow — required (§12) |
| Create: Purchase | ✅ | ✅ | Supports counter / cash buys (§13) |
| Create: Contact (customer / supplier) | ✅ | ⛔ unless granted | Master creation |
| Edit master data (name, category, selling price) | ✅ | ⛔ unless granted | Requires grant |
| Cancel / Void (posted records) | ✅ | ⛔ | Sensitive |
| Return (posted records) | ✅ | ⛔ | Sensitive |
| Stock Adjustment | ✅ | ⛔ unless granted | §14 |
| View financial / profit data | ✅ | ⛔ | Includes P&L, financial reports |
| Manage users / roles / audit / settings | ✅ | ⛔ | Owner only |
| Export (PDF / Excel) | ✅ | ⛔ unless granted | |
| Delete (master-only, per §6.3) | ✅ | ⛔ | |
| System settings / configuration | ✅ | ⛔ | |

**Rules**
- Capabilities are granular named scopes (for example `sale.create`, `sale.cancel`, `sale.return`, `inventory.adjust`, `finance.view_profit`, `export.data`, `product.edit`). Roles group these scopes for convenience; access checks are never hardcoded to a role name.
- The owner has full access by default and may grant or revoke any capability.
- The Staff base set is the table above: view, own-draft edit, sale-create, and purchase-create are ON; all else is OFF.
- Grants and revokes take effect immediately and are logged (§26).

### 6.2 Role and override design (RECOMMENDATION)

A role is a named set of capabilities. A user belongs to exactly one role, with optional per-user grants or revocations layered on top. The owner may create or remove custom roles. No role can confer rights that break the immutability or audit constraints in §7, §9, and §26.

### 6.3 Delete — the definition (resolves audit CT-2)

To respect historical integrity, the following may be **permanently deleted**:
1. **Draft / unposted** transactions that were never posted and therefore never had a stock or financial effect.
2. **Master records with no referencing history** — a product, customer, or supplier that has never appeared in any transaction, movement, payment, or receivable line.

The following may **never** be hard-deleted:
1. Any **posted, completed, cancelled, or returned** financial or inventory transaction.
2. Any master record already referenced by history (movements, payments, transactions).
3. Any applied payment record.

For posted records, use cancellation/reversal/correction instead of deletion (§9). The safe V1 default is **no hard delete of any posted document**. Where useful, the UI can hide or archive a record (a status change) rather than delete it. A used product is deactivated, not deleted (§10).

---

## 7. Editing Policy (transaction and key-record changes)

**V1 ASSUMPTION — the operative policy.**

- **Draft records** are freely editable; they have no stock, ledger, or cost effect.
- **Posted / completed transactions** must not be edited in place for any field that affects inventory, revenue, COGS, payment, expenses, receivables, or payables. Corrections use the lifecycle: Cancel/Void, reverse effects, then create a corrected transaction. The original and the reversal both remain visible in audit (§26).
- **Non-financial / non-inventory fields** (for example a free-text note) may be edited after posting where allowed, and the edit is logged.
- Historical COGS and inventory are never recomputed from today's master data; only new or draft records use current prices and settings (§18).

---

## 8. Transaction Lifecycle and Payment State — two orthogonal axes

Lifecycle status and payment state are two independent fields, never a single enum.

### 8.1 Lifecycle (document status)

| Status | Meaning | Stock & financial effect |
|---|---|---|
| **Draft** | Being built; editable; no effect; cost not frozen | None |
| **Posted** | Applied; stock and financial effect taken; cost frozen here | Yes (once) |
| **Completed** | Normal finish (fully collected, fulfilled, or processed) | Same posting moment (§8) |
| **Cancelled / Voided** | Invalidated after posting; effects reversed exactly once | Yes (reversal) |
| **Returned** | Physical goods returned after a valid transaction | Yes (goods reversed) |
| **Partially Returned** | Some — not all — line items or quantities have been returned on a valid transaction | Yes (returned portion reversed)

V1 merges Posted and Completed into a single posting moment: a document takes effect (stock, ledger, and cost snapshot) at posting, then moves to Completed when fully paid or fulfilled. A cancellation reverses a posted document exactly once.

### 8.2 Payment state (money axis)

| Status | Meaning |
|---|---|
| Unpaid | Nothing collected or paid |
| Partial | Some collected or paid |
| Paid | Fully collected or paid |

Payment state is derived from the sum of payments against the document total; it is not a manually set field alone.

### 8.3 Orthogonality
- Posted + Partial — for example a sale collected Rp40,000 of a Rp100,000 total leaves Rp60,000 receivable.
- Completed + Paid — fully paid and fulfilled.
- Posted + Unpaid — sold but nothing collected.
- Cancelled + Partial — partially paid, then voided; the collected portion is refunded, the rest written off (§9).

### 8.4 Per-document state matrix

| Document | Lifecycle path |
|---|---|
| Sale | Draft → Posted/Completed → ( Cancelled / Returned ) |
| Purchase | Draft → Posted (Received) → Completed → ( Cancelled / Returned ) |
| Production run | Draft → Posted (raw down, finished up) → ( Cancelled ) |
| Stock adjustment | Draft → Posted (applied) → ( Cancelled ) |
| Manual income / expense | Draft → Posted → ( Cancelled / reversed ) |
| Payment | Exists only within a document; never standalone |

---

## 9. Cancellation, Return, Refund — authoritative mapping

| Event | Lifecycle change | Stock effect | Financial effect |
|---|---|---|---|
| Sale posted | → Posted | Minus quantity, COGS snapshot at posting | Revenue recognized; receivable if unpaid |
| Sale cancelled | → Cancelled | + quantity back (reverse original once) | Revenue reversed; refund collected amount |
| Sale returned (physical) | → Returned | + quantity at original line cost | Refund collected amount, or write off |
| Purchase received | → Posted/Completed | + quantity | Supplier payable; payments recorded |
| Purchase returned | → Returned | Quantity at original receipt cost, minus | Payable reduced / supplier refund |
| Purchase cancelled | → Cancelled | Reverse the received effect, once | Payable reversed; refund any amount paid |

### Definitions

- **Cancellation** is the full invalidation of an entire posted document. It reverses the whole stock effect and returns or collects the summed amount. A document is cancelled once; no double reversal.
- **Return** is a physical-goods reversal of a valid document. It can be partial (some lines or quantities). The money-back on the paid portion is a refund.
- **Refund** is money returned to a customer (or to the business on a purchase) when a previously paid line or document is cancelled or returned. A refund is never income; it is a cash outflow that reverses a previously collected cash inflow (§16, §19).
- Cancellation, return, and refund are distinct. Cancellation and return never restore the same stock twice.

### Refund policy (V1 ASSUMPTION)
- Default: refund using the same payment method as originally received (cash returns to cash, transfer returns to transfer). When that is not possible, record it as a refund cash outflow in the payment journal.
- For a partial-paid cancellation or return: refund only the collected portion and write off the remaining receivable or payable.
- Refunds appear in the payment/cash journal; they are not revenue, not COGS, and not an income category in the P&L.
- The default "Refund" income category is removed (§17). Refunds do not belong on the income ledger.

---

## 10. Product Management

### Functional requirements
- F1. Create, edit, and deactivate products; no hard-delete of used products.
- F2. Fields: name (required), category (optional), unit, purchase price, selling price, stock, status, notes.
- F3. Product code is NOT mandatory — absence must never block saving. **CONFIRMED.**

### Business rules
- B1. Product code optional, never required; if present, unique. **V1 ASSUMPTION** (uniqueness enforced when present).
- B2. A product can have different purchase prices over time; the displayed/current purchase price is the latest, and historical prices remain stored. **CONFIRMED.**
- B3. Purchase price (master/current) is a display or reference value, distinct from unit cost (the costing value); see glossary §38.
- B4. A product is sellable, purchasable, and usable in production by default unless a flag is added. Flags are optional (§39).

### Master-data edit safety
- Editing a product price affects only new drafts and the master's current price. It never rewrites a posted transaction or an existing stock-movement cost (§18).

---

## 11. Purchasing

### Requirements
- P1. Purchase payment states: Paid in full, Partial, Unpaid (payable).
- P2. A purchase can have multiple payments.
- P3. Shipping costs are in the same broad category but separately visible, never merged indistinguishably. **CONFIRMED.**
- P4. Cancellation and return are separate events. **V1 ASSUMPTION** (§9).
- P5. Stock increases only on goods receipt, not when the purchase record is created. **CONFIRMED.**
- P6. Purchase return decreases stock where physical. **CONFIRMED.**
- P7. Shipping is visible and itemized.

### Payments example (CONFIRMED)
Purchase = Rp10,000,000. Payments of 4,000,000 + 3,000,000 + 3,000,000 = 10,000,000 → status Paid. The UI always shows remaining = total minus paid; status stays Partial until 100%.

### Purchase lifecycle
Draft → Ordered (no stock effect) → Received/Posted (+stock, cost snapshot) → Completed (fully paid) → ( Cancelled / Returned )

- A purchase not yet received has no stock effect. Cancelling it simply voids the record and any unallocated payment.
- In V1 each purchase line is received in full. Partial receiving or splitting a PO is a future feature.

### Shipping cost — accounting treatment (resolves audit CT-3)

**V1 ASSUMPTION: shipping on a purchase is part of the acquisition (landed) cost, not an operating expense.**

- Shipping and purchase-related incidental costs are added to the purchase total and distributed proportionally to the line values among the received lines, so they become part of the inventory cost basis (COGS pool, §18).
- The shipping amount is always shown separately and labeled; it is never merged invisibly into line prices.
- Because it is capitalized, there is no separate operating "Shipping" expense for the same purchase in the P&L (§19). The only shipping that may appear as a manual expense is shipping not attached to a purchase, such as postage on a sold order. The two forms are never combined.
- Validation: purchase total = sum of line values + shipping (visible).

### Inputs, outputs, validation
- Input: supplier (optional, §20), line items (product, quantity, price), totals, shipping, payments, lifecycle.
- Output: purchase record, supplier payable, stock increase on receipt, payment ledger.
- Validation: allocated payments must not exceed the purchase total; stock applies only on receipt; quantity and price must be positive.

---

## 12. Sales / POS

### Requirements
- S1. Select products, record quantities, selling prices, totals, and payment.
- S2. Customer is optional; never required.
- S3. A successful sale decreases inventory.
- S4. Support paid, partial, and unpaid (receivable).
- S5. Support multiple payment methods per sale (for example cash plus bank transfer).
- S6. Cancellation and return are separate events. **V1 ASSUMPTION** (§9).
- S7. Sales return increases inventory (physical goods returned). **CONFIRMED.**
- S8. Staff / cashiers can record a sale at the POS; the flow is enabled for the default Staff set (§6.1).

### Cash over-tender (change) — REQUIRED
| Field | Value |
|---|---|
| Sale total | Rp450,000 |
| Cash tendered (given) | Rp500,000 |
| Allocated (applied) | Rp450,000 |
| Change (returned) | Rp50,000 |

> The Rp500,000 tendered is NOT revenue. Revenue = the allocated payment (Rp450,000). The Rp50,000 change is tender/change accounting, not income. With exact tender, change is Rp0.

Validation: "payments do not exceed total" applies to allocated payments, not raw tender. A cash split records tender, allocated, and change. Example: a sale of Rp450,000 with Rp500,000 tendered produces allocated 450,000, change 50,000, and revenue 450,000.

### Selling price and discounts (V1 ASSUMPTION)
- The default selling price comes from the product's configured selling price.
- A price override is allowed only with the `sale.price_override` permission, and every override is logged (§26).
- Discounts are optional in V1 (a line or order-level flat amount or percent). A discount reduces revenue (revenue = selling price minus discount) but does not change COGS; it reduces gross profit. When the discount feature is off, no discount field is shown.

### Success / failure
- Success: stock down, payment captured, optional customer receivable, revenue recognized.
- Failure: insufficient stock → warning or hard block per the negative-stock rule (§14).

---

## 13. Inventory (Stock)

### Requirements (CONFIRMED)
- C1. Stock adjustment is allowed.
- C2. By default only the owner adjusts stock; the owner may delegate and revoke.
- C3. Negative stock is prevented by default. Overselling beyond stock shows an insufficient-stock warning; default behavior blocks the sale; the owner may optionally enable negative stock per item or globally.
- C4. Every movement keeps a trigger/reason and is audited.

### Inventory movement matrix — ONE authoritative effect per event (CRITICAL)

| Event | Stock effect | Cost effect |
|---|---|---|
| Purchase receipt | + quantity | Added to weighted-average pool (§18) |
| Purchase return | − quantity | Reverse at original receipt cost |
| Sale (posted) | − quantity | COGS at current average, snapped |
| Sales return (per) | + quantity | Re-enters at original duty cost |
| Production input (raw) | − quantity | Raw materials at their average |
| Production output (finished) | + quantity | Finished cost = raw + overhead (§15) |
| Stock adjustment | −/+ quantity | Equal to current pool value, logged |
| Cancellation / reversal | Reverse original effect exactly once | Reverse original cost effect once |

**Rules**
- Every stock-changing business event maps to one authoritative inventory effect (the table above is the single source).
- No double application: cancel a posted sale, reverse the original decrement once; never also apply a return increment. Only one path applies.

### Negative stock (default and optional)
- Default: block plus warning.
- The owner may optionally enable negative stock per item or globally; negative rows are clearly flagged and audited.
- COGS for negative-stock sales follows §18.4.

### Cancellation vs Return (sales) — explicit
- Cancellation reverses a posted sale exactly once (a sale of −10 becomes +10 on reversal).
- Return = physical goods of a valid sale come back → +10.
- The system must never let both a sale-void and a sales-return restore the same stock.

---

## 15. Production / Manufacturing — V1 temporary model

The full production workflow is not yet defined. This is a temporary V1 model and a V1 ASSUMPTION.

- Model: raw materials → processing → finished product. On posting, raw decreases and finished product increases. (V1 assumption carried from the original.)
- No permanent recipe/BOM in V1. **CONFIRMED.**
- Production cost types are configurable: Labor, Electricity, Gas, Packaging, Other. **CONFIRMED.**
- Removing a cost type that is historically used must keep the historical records (type and amount). **CONFIRMED — hard requirement.**
- **Finished-goods cost basis — V1 ASSUMPTION:** finished unit cost = (sum of raw-material cost consumed + sum of production cost-type amounts) divided by finished quantity. Overhead is therefore capitalized and eventually becomes part of the finished-good COGS on later sale. (A raw-materials-only basis is available as a future config change; V1 uses raw plus overhead.)

### Worked example (conceptual)
```
Raw material      Rp100,000
Labor             Rp  20,000
Energy            Rp  10,000
Packaging         Rp   5,000
Total             Rp 135,000
If 10 finished units → unit cost = 135,000 / 10 = 13,500
```

### Cost-entry unit — V1 = per production run
A run record holds inputs (product, quantity, cost taken at average), finished product and quantity, and cost-type lines. This keeps a flexible shape; a per-period (monthly) cost-batch option is deferred.

### Cost-type removal safety
Remove a type only from the active list; recorded historical lines always keep the type and amount as they were at the time of the run. Never hard-delete a referenced cost type.

### Failure and edges
- Insufficient raw inventory → warn or block per the negative-stock rule (§14).
- Removing a cost type used in history → keep the historical record with value; hide from new entries.

---

## 16. Reconciliation — Payments, Receivables, Payables

**CONFIRMED:** payment record fields: amount, method, date, and optional account/source. Methods: Cash, Bank Transfer, E-Wallet, Other (configurable).

- A payment account is optional; if provided, record it; never force account creation. **CONFIRMED.**
- Account balances are never fabricated; leave blank when unknown. **CONFIRMED.**
- A Payment references one document (sale, purchase, income, or expense); a document may have multiple payments.
- Receivables and payables are derived: document total minus total payments.
- For over-tendered cash, the payment is recorded as tender, allocation, and change accordingly (§12).
- Refunds follow §9 (cash outflow mirroring the original method; only the collected portion; never income).

---

## 17. Income & Expenses (manual and derived)

### Two kinds of financial data (CRITICAL — prevents double counting)
A. **Derived (transaction-generated, never manually entered):**
- Sales revenue (from sales)
- Purchase liabilities (from purchases)
- COGS (from costing)
- Production cost (from runs)
- Purchase-side shipping (part of inventory cost, §13)
- Payment effects, receivables, payables

B. **Manual (directly entered):**
- Capital injection, other income
- Rent, labor (non-production), maintenance, operational, tax, electricity (non-production), extra shipping not attached to a purchase, other

> Refund is not a manual income category. It was removed from the income defaults; refunds are lifecycle and cash-out events (§9). There is no manual "Sales" income (it is derived) and no manual "Purchase" expense (the item value and COGS handle it).

### Default category sets (RECOMMENDATION / V1)
- Income (manual): Capital Injection, Other Income, Other.
- Expense (manual): Rent, Labour (non-production), Electricity (non-production), Maintenance, Operational, Tax, Extra shipping (not attached to a purchase), Other.
- Derived (reserved by the system): Sales revenue, Purchase, COGS, Production, Purchase-shipping.

### Configurability
- Categories can be added, edited, or renamed. Renames are metadata; historical amounts stay correct after a rename. **CONFIRMED.**
- Manually entered amounts, dates, and transactions are immutable once posted; only metadata changes are allowed and are logged (§7).

### Validation
- Amount greater than zero; date valid; category required; method/account optional.

---

## 18. Costing — the complete model

### The invariant
> Once a transaction or event is posted, the historical cost used by that transaction is frozen (snapshotted) at the moment of posting. Later purchases, price changes, or costing-method changes never rewrite that history. Historical COGS, profit, and inventory value are derived from the stored snapshots and movements, never recomputed from today's live price.

### 18.1 Costing method (V1 ASSUMPTION): moving average cost
- On a **purchase receipt**, the new moving-average unit cost is: `(opening on-hand × opening average) + received line value + allocated shipping` ÷ `(opening on-hand + received quantity)`. The received line value is line quantity × line price.
- On a **sale**, the COGS per unit is the current moving-average value at the moment of posting, snapshotted onto the sale line.
- On **production input**, raw material is valued at its average. On production output, finished goods are valued by the formula in §15.
- The method is configurable and forward-only. Switching to FIFO or last-purchase-price affects only new transactions; history is never recomputed.

### 18.2 Cost snapshot storage
- Each sale line stores unit cost, quantity, total COGS, the costing method in effect, and a transaction reference. It is never recalculated after posting.
- Each inventory movement stores its cost snapshot. Reports read snapshots, never live prices.

### 18.3 Return cost
- Goods returned re-enter inventory at the original duty cost of the returned quantity. Purchase returns reverse the original cost at receipt. **V1 ASSUMPTION.**

### 18.4 Negative-stock cost (V1 ASSUMPTION)
- When negative stock is off, an overselling sale is blocked, so no fallback is needed.
- When the negative-stock option is on and a sale consumes goods with no known pool cost, the COGS is set to the product's last purchase price, and that line is flagged and logged as a negative-stock fallback. It is never silently zero.
- The fallback policy is configurable (last-purchase-price by default, average, zero, or block), but V1 defaults to last-purchase-price with a flag.

### 18.5 Finished-goods cost
- Finished unit cost = (raw consumed + overhead) ÷ finished quantity, per §15. V1 ASSUMPTION.

### 18.6 Shipping and costing
- Shipping is allocated pro-rata by line value and added to the product's pool so it flows into weighted cost and future COGS. It is not an operating expense (§13).

---

## 19. Profit & Loss

- **Revenue** = the sum of allocated payments on posted sales, with returns reversing revenue. Cash "change" is not revenue.
- **COGS** = the sum of COGS (posted sales) minus the COGS reversal from sales returns.
- **Gross profit** = Revenue − COGS.
- **Other expenses** = derived COGS plus production cost plus manual expenses (including any shipping not attached to a purchase). No double counting with capitalized shipping.
- **Net profit** = Gross profit − other expenses.
- **Refunds** appear in the payment/cash journal as cash outflow; they are not revenue or expense and do not change COGS.

Example (consistent):
```
Revenue        Rp 50,000,000
− COGS         Rp 30,000,000
= Gross profit Rp 20,000,000
− Other exp    Rp  8,000,000   (rent 4.0M, labour 3.0M, manual ship 1.0M)
= Net profit   Rp 12,000,000
```

The P&L is computed from ledger data (a single source), so it is never inconsistent and double counting is blocked (§17).

---

## 20. Supplier Management

**CONFIRMED:** supplier information, contact, address, purchase history, total purchases, payables, and notes.

**Supplier requirement (V1 ASSUMPTION):** a supplier or details stated in a PRD did not confirm that every purchase needs a supplier. A purchase without a supplier (a counter/cash buy) is allowed; it shows as unassigned in reports while the purchase and payable still record fully.

---

## 21. Customer Management

**CONFIRMED:** customer information, contact, address, sales history, total sales, receivables, notes.
**Customer optional** (CONFIRMED) — a customer is not required on any sale. A receivable exists only when a sale is posted with less than its full amount collected.

---

## 22. Dashboard

- Live at-a-glance overview. Proposed V1 metrics (TBD list): total sales, purchases, income, expenses, stock value, gross profit, net profit, best-sellers, and low stock.
- Charts: sales trend over time; breakdown by category or product.
- Filters: **CONFIRMED** Today / This Week / This Month / This Year / Custom.
- Single source of truth: the dashboard reads authoritative transaction data; no separately maintained totals. **CONFIRMED philosophy (RECOMMENDED).**

---

## 23. Reports

- Capabilities (CONFIRMED): search, filter, sort, view details, compare periods, export.
- Filters: date range, category, product, supplier, customer, payment method, payment status, lifecycle status, expense type, cost type.
- V1 report list (sizing TBD): sales by product/period, purchases by supplier, income/expense by category/day, inventory value + movement, production with cost, and P&L.
- All reports read the single source of truth (snapshot data).

---

## 24. Data Management

Not plain CRUD tables. Every screen answers "what the user is trying to do." Provide list, filter, search, sort, edit, detail, and period-comparison across modules. Editing follows §7 and §9. Consistent UX.

---

## 25. Export

- Scope (CONFIRMED): PDF and Excel (XLSX) export of sales, purchases, inventory, income, expenses, production, profit, and reports.
- Respect active filters where possible, and include a filter summary (RECOMMENDATION).
- Export permission follows §6 (Staff defaults to no unless granted).

---

## 26. Audit & History

- Purpose: capture WHO, WHAT, WHEN, PREVIOUS, NEW, and an optional REASON for important changes.
- Events: record creat afterwards posted, edit, cancel/void, return, stock adjustment, permission change, settings change, deactivate. **CONFIRMED.**
- Audit is an append-only log; the WHO/WHAT/WHEN/PREVIOUS/NEW fields are required on these events.
- Financial/inventory records are never deleted; use status (active/cancelled/voided/archived). Any deletion (master-only) is privileged and logged (§6.3).

---

## 27. Notifications & Warnings

1. Low-stock / out-of-stock list. **V1 ASSUMPTION:** default threshold is a config value with a per-product override; no hardcoded constant.
2. Insufficient stock on sale/purchase/production: warning to warning versus hard block per the negative-stock rule (§14).
3. Below-cost sale warning: default show warning, not block. (V1)
4. Payment reminders: recommended; in-app only in V1.
5. Irreversible action confirmation: prompt before cancel/void/delete.

---

## 28. Validation Rules

- Amounts and quantities are positive; decimals allowed per unit, integers where natural.
- Dates are valid and reasonable.
- Payment allocation ≤ total; a cash tender may exceed with change.
- Purchase total = sum of lines + shipping.
- Payment state is derived.
- Required: product name, contact name, and so on.

---

## 29. Edge Cases

| Case | Behavior |
|---|---|
| Duplicate product lines | Merge lines (recommended) or reject |
| Partial-payment then return | Return stock; refund collected portion; write off the rest |
| Cancel a fully paid purchase | Reverse stock once if received; refund/credit accordingly |
| Price change between cart and save | Lock price or re-read and confirm mismatch |
| Deactivated product with stock | Not sellable; counted in stock value; flagged |
| Negative stock enabled | Allowed and flagged; COGS fallback (§18.4) |
| Quantity 0 / negative | Reject |
| Partial multi-payment | Remaining shown; becomes Paid at 100% |
| Editing a posted sale | Cancel / re-enter per §7; in-place rejected |
| Price override | Permission-gated and logged |
| Missing supplier / customer | Allowed; shown blank or unassigned |
| Void double-reversal | Prevented by the movement matrix (§14) |
| Delete a history-referenced record | Disallowed; use deactivation (§6.3) |
| Return of an already cancelled or returned document | Reject (no double effect) |

---

## 30. Business Rules — Consolidated

1. Product code optional, never required. **CONFIRMED**
2. Displayed purchase price = latest; history preserved. **CONFIRMED**
3. Sale may omit customer. **CONFIRMED**
4. Sale ↓ stock; sales return ↑ stock. **CONFIRMED**
5. Purchase ↑ stock on receipt; purchase return ↓ stock. **CONFIRMED**
6. Paid/partial/paid plus multiple payment methods for sales and purchases. **CONFIRMED**
7. Lifecycle status is separate from payment state. **V1 ASSUMPTION**
8. Shipping visible and capitalized to purchase/inventory cost. **CONFIRMED / V1** (§13, §18)
9. Cancellation ≠ Return ≠ Refund. **V1 ASSUMPTION** (§9)
10. Stock adjustment owner-only by default; delegable and revocable. **CONFIRMED**
11. Negative stock blocked by default; owner may enable. **CONFIRMED / V1**
12. Production raw ↓, finished ↑; finished cost = raw + overhead. **CONFIRMED(V1)/V1** (§15)
13. Cost types configurable; historical persistence. **CONFIRMED**
14. Costing method configurable; history frozen; default = moving average. **V1 ASSUMPTION**
15. Gross = Revenue − COGS; Net = Gross − other expenses. **CONFIRMED**
16. Expense categories configurable. **CONFIRMED concept**
17. Manual entries can never duplicate derived entries. **CONFIRMED**
18. Payment account optional; balances never fabricated. **CONFIRMED**
19. Customer optional; supplier optional. **CONFIRMED (cust) / V1 (sup)**
20. Every stock event has one effect (no double). **V1 ASSUMPTION (integrity)**
21. Posted records are non-destructive (no hard delete). **V1 ASSUMPTION**
22. Owner has full access; capabilities delegable. **CONFIRMED**
23. Refund is a cash outflow, not an income. **V1 ASSUMPTION**

---

## 31. V1 Assumptions (explicit list)

These decisions make V1 implementable given that no further owner clarification will occur. Previously items were TBD or ambiguous; now each is a stated assumption.

| # | Assumption | Why / effect |
|---|---|---|
| A1 | Costing method = moving average cost, forward-only | Simple, method-agnostic engine; history frozen |
| A2 | Returns re-enter stock at the ORIGINAL line cost | Keeps the average pool and value coherent |
| A3 | Shipping is capitalized into inventory cost, not an operating expense | One accounting path; no double count; inventory value accurate |
| A4 | Refund = cash-flow journal event, never income | Fixes the "refund as income" classification error |
| A5 | Cancellation and return are separate; both reverse stock exactly once | Guarantees no double effect (§14) |
| A6 | Negative-stock default block; COGS fallback = last-purchase price (flagged) | Coherent, auditable fallback |
| A7 | Finished-goods cost = raw + overhead ÷ quantity, captured per run | Capitalizes into inventory, consistent with §15 |
| A8 | Product fully versatile by default (sell/purchase/production) | Simple; flags available later |
| A9 | Supplier optional on purchase | Supports counter / cash buys |
| A10 | Low-stock threshold = global default + per-product override | Flexible, no hardcoded constant |
| A11 | Production cost measured per run (not per period) | Settles the data shape for V1 |
| A12 | Single stock location; V1 (apply a nullable location key to the model) | Multi-location deferred but schema forward-compatible |
| A13 | Stock value KPI from the costing basis (average), not the live sales price | Single-source correctness |
| A14 | Discounts and price overrides are permission-gated and configurable | V1 keeps POS simple; revenue reduced by discounts |

---

## 32. Open / TBD Items (non-blocking; decide at implementation)

- The exact global default low-stock threshold number.
- The precise subset of items in the V1 report list (§23) at sizing time.
- Rounding mode for money sums and cash change (config-level; display-only).
- Whether sequential document numbering is enabled by default (recommended for traceability; optional).
- Notification channels and delivery timing (in-app only in V1).
- Residual UI detail items, final field validation strings, and label/an-WGU details.

None of these blocks business-rule or database extraction.

---

## 33. Scope

**In V1:** sales/POS and purchases with multi-payment, optional codes, price history, over-tender/change, return/cancel; inventory ledger with adjustments; the V1 production model; costing (average + freeze); P&L; customer and supplier masters; manual income/expenses with double-count guard; dashboard; reports; PDF/Excel export; append-only audit log.

**Out of V1:** BOM/recipe, general ledger, payroll, multi-currency, tax engine, mobile/push integrations, warehouse/UOM conversions, batch/lot/expiry, POS cash-session (X-Z), imports beyond reports, formal approvals, microservices.

**Future / V2+ (§33):** BOM with versions and yield, formal ledger, multi-currency, purchase orders, barcode/scanner, loyalty/promo/membership, advanced notifications, reorder automation, batch/lot/expiry, payroll, backup/import, locations and units of measure, selling-price history, receivable aging/reminders, X-Z close, money rounding mode, and document numbering.

---

## 33. Database: forward only

This PRD defines business rules only; it does not design the database, the ERD, or SQL. To make the later Business-Rules-to-ERD step low-risk, the rules above already pin: cost snapshots on movements and sale lines; polymorphic payments; a nullable location identifier; soft, non-destructive statuses for posted records; and a derived-versus-manual ledger split.

---

## 34. Requirement / Risk Summary

| Area | Rank | Why |
|---|---|---|
| **Costing method** | High | Must be method-agnostic; snapshots on movement and sale; a future switch is forward-only |
| **Production model** | High | Unknown real workflow; V1 single-step, raw→finished with per-run cost lines |
| **Returned-cost handling** | Medium | Returns re-enter at original cost; defined so the pool stays correct |
| **Payment model** | Medium | Multiple payment allocations; tender vs allocated vs change |
| **Negative inventory** | Medium | Config flag + last-purchase fallback; schema stable under both |
| **Permission / delegation** | Medium | Granular grants and revokes; row-level security out of scope |
| **Historical behavior** | High | The whole contract: snapshots + audit; never recompute history |

**Risk philosophy:** No plan rewrites history. Every number derives from stored movements and snapshots, not from live current prices and settings.

---

## 35. Acceptance Criteria (V1)

1. Roles: Owner full access; the Staff base set includes sale-create and purchase-create; grants/revocations take effect.
2. A product with no code saves successfully.
3. The latest purchase price is shown, and a prior sale's cost stays at Rp10,000 after price and method changes.
4. A sale reduces stock; split payment; over-tender runs (revenue 450,000, change 50,000).
5. A purchase of 10M in three payments shows Paid at 100% with remaining visible.
6. Negative stock is blocked by default; when enabled it is flagged and COGS uses last-purchase-price.
7. Production decreases raw, increases finished, records cost lines, and keeps history after a cost type is removed.
8. P&L is consistent; the manual ledger cannot duplicate derived entries; refunds never appear as income.
9. Staff defaults are limited as described; a granted capability appears, a revoked one disappears.
10. Lifecycle and payment are orthogonal (posted + partial is representable).
11. The audit log captures who/what/when and the before/after for create/cancel/return/adjust/permission.
12. Editing a posted sale in place is rejected.
13. No double counting; stock value on the dashboard matches detail reports.
14. Shipping is capitalized (shown in stock and COGS) and is not duplicated in the P&L.
15. A used product cannot be hard-deleted.

---

## 36. Appendix — Worked Examples

| Topic | Numbers | Check |
|---|---|---|
| Purchase fully paid | 4M + 3M + 3M = 10M → Paid | ✓ |
| Sale over-tender | total 450k, tender 500k, allocated 450k, change 50k → revenue 450k | ✓ |
| Average costing | 10 order@10k, 5 order@12k → pool avg ≈ 10,666.67 (snapshot) | ✓ |
| Return wiring | returned 10 units re-enter at original line cost | ✓ |
| Finished cost | raw + overhead 135k ÷ 10 finished = 13,500 | ✓ |
| P&L | 50M − 30M = GP 20M; − 8M = NP 12M | ✓ |
| Refund | 40k paid on a 100k sale, then cancelled → refund 40k, write off 60k | ✓ |

---

## 36. Final Internal Audit (this document)

**Business**
- Every core workflow (purchase, sale, production, adjustment, cancel/return, manual income/expense) has a defined lifecycle and a defined stock and financial effect (§§8–19).
- Cashier POS creation is enabled (§6, §12).
- Purchases, returns, cancellations, and refunds are each defined with stock and financial effects (§9).
- Refund is defined as a cash outflow, never income (§9, §19).

**Costing**
- Costing method = moving average, forward-only, history frozen (§18).
- Return cost = original line cost (§18.3).
- Negative-stock fallback = last-purchase-price, flagged (§18.4).
- Finished-goods basis defined (§15).
- Shipping not double-counted (§13, §18).

**Finance**
- Revenue distinct from cash/change (§12 over-tender; §19).
- Refund distinct from expense/income (§19).
- COGS, gross profit, and net profit defined (§19).
- Derived vs manual entries cannot double count (§17).

**Permissions**
- Owner behavior defined; Staff/POS defined; delegation defined; sensitive actions restricted (§6).

**History**
- Posted records auditable; corrections non-destructive; price/cost history preserved (§18, §26).

**Terminology**
- Terms consistent with the glossary (§38); no concept used with two contradictory meanings.

---

## Terms / Glossary (final)

- **Product / Item / SKU:** the atomic product record. Interchangeable terms for the master product. "Product line" or "line item" means the occurrence of that product on a document (line of a sale, purchase, or production).
- **Stock / on-hand:** the physical quantity of a product. **Inventory value (stock value):** stock × unit value (cost basis), never using the live sales price.
- **Draft / unposed:** record not yet in effect; freely editable; no cost frozen.
- **Posted:** committed transaction with stock, ledger, and cost snapshot applied; thereafter immutable.
- **Completed / Paid:** a finished, fully paid/fulfilled (may share the posting moment).
- **Purchase price:** the product master's latest recorded price (display reference).
- **Unit cost / average cost:** the costing-basis value of on-hand stock at a moment; the cost posted for COGS. Distinct from the "purchase price".
- **Revenue:** the allocated amount earned from a posted sale; change is not revenue; a return reverses revenue.
- **Income:** manual money categories plus derived revenue (labeled "Revenue/Income" in UI).
- **Expense:** manual operating spend; distinct from refund.
- **COGS:** cost of goods sold, derived from the costing basis.
- **Gross profit:** Revenue − COGS. / **Net profit:** Gross profit − other expenses.
- **Refund:** money returned to a customer or supplier on a paid-post transaction; a cash outflow, never income.
- **Return:** the physical goods reversal (a stock movement).
- **Cancellation:** full invalidation of a posted document (reverses once).
- **Shipping cost / freight:** in the purchase it is folded into acquisition cost; off-purchase it is a manual expense; always shown separately.
- **Process cost / production cost:** production-run cost-type amounts (labor, energy, gas, packaging) that in V1 are capitalized into finished inventory.
- **Posted vs Draft:** distinct states defined above; used consistently throughout.

(*This glossary is the single source for terminology in the document. Any wording elsewhere that conflicts is superseded by this section.*)

---

*— End of PRD V1.1 —*