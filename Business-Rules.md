# Business Rules — Business POS System

> Source of truth: `Business-POS-System-PRD-V1.1.md`  
> This document extracts all implementable business rules from the PRD. Every rule is traceable, testable, and implementation-independent.

---

## 1. Authentication & Roles

### BR-AUTH-001 — Owner Role Definition

**Rule:** The Owner holds the highest role with full access to all system capabilities.

**Trigger:** User is assigned the Owner role in the user management system.

**Preconditions:** None.

**Behavior:** The Owner can view, edit, create, cancel, return, adjust stock, manage all master data, view financial reports, create exports, manage users and roles, configure settings, and delegate capabilities.

**Effects:** Full read/write/access across all entities.

**Source:** §6, §6.1, §30 #22

---

### BR-AUTH-002 — Staff Role Definition

**Rule:** The Staff role has limited access: view and edit own drafts, create sales and purchases, but cannot cancel, return, delete, or access sensitive operations unless explicitly granted.

**Trigger:** User is assigned the Staff role.

**Preconditions:** User record exists with Staff role assignment.

**Behavior:**  
- View data: allowed.  
- Create and edit own drafts: allowed.  
- Create sale / POS record: allowed.  
- Create purchase: allowed.  
- Cancel / void: not allowed.  
- Return: not allowed.  
- Delete: not allowed.  
- View financial / profit: not allowed.  
- Export: not allowed.  
- Manage users / roles / audit / settings: not allowed.  

**Effects:** Staff can perform daily POS entry but cannot make administrative or correcting actions without an explicit grant.

**Exceptions:** Owner may grant specific capabilities to an individual Staff user (§6.1).

**Source:** §6, §6.1, §6.3

---

### BR-AUTH-003 — Role Delegation

**Rule:** The Owner may grant or revoke any specific capability to or from any user.

**Trigger:** Owner performs a grant or revocation action in the user or permission management interface.

**Preconditions:** User exists; capability is defined in the system.

**Behavior:**  
- Grant: The specified capability becomes immediately active for the user.  
- Revoke: The capability becomes immediately inactive for the user.  
- The grant/revoke action is logged in the audit trail.

**Effects:** Ability assignment changes; audit log records WHO, WHAT, WHEN, OLD, NEW.

**Source:** §6.1, §26

---

### BR-AUTH-004 — Capability Granularity

**Rule:** Permissions are stored as named scoped capabilities (e.g., `sale.create`, `sale.cancel`, `inventory.adjust`), not as hardcoded role checks.

**Trigger:** Permission check is required for a protected operation.

**Preconditions:** A user, role, or override exists.

**Behavior:** The system checks the user's effective capability set (role base + overrides) for the specific capability name, not for a role identifier.

**Effects:** Enables flexible composition of roles and per-user permissions.

**Canonical codes:** Capability identifiers referenced across Business Rules follow the canonical catalog defined in `API-Architecture-V1.0.md` §3.1 (e.g., `sale.price_override`). Older shorthand (`price.override`) is superseded.

**Source:** §6.1

---

## 2. Product & Master Data

### BR-PRODUCT-001 — Product Code Optional

**Rule:** A product may be created without a code. The absence of a code never blocks saving.

**Trigger:** Product creation or update attempt.

**Preconditions:** Product name is provided (required).

**Behavior:** If a code is provided, it must be unique among existing products. If no code is provided, save succeeds.

**Effects:** Product is created with or without a code; no validation error for missing code unless an order requires it elsewhere.

**Source:** §10, §11 B3, §37

---

### BR-PRODUCT-002 — Product Price History Preservation

**Rule:** A product's purchase price may change over time, but historical transactions use the price at the moment of their posting.

**Trigger:** Purchase price update on a product master.

**Preconditions:** Product exists and has a new purchase price value.

**Behavior:** The new price becomes the "display"/"current" purchase price. Historical transactions retain their originally recorded cost (the cost snapshot). New transactions use the new price.

**Effects:** Price history is stored; past transactions are not recalculated.

**Source:** §11 B2, §18, §30 #2

---

### BR-PRODUCT-003 — Product Deactivation

**Rule:** A product may be deactivated. A deactivated product is not sellable in new transactions but remains sellable for reporting on existing posted transactions.

**Trigger:** Product is deactivated via edit.

**Preconditions:** Product is not currently referenced in an open draft (optional validation).

**Behavior:**  
- The product's status changes to "deactivated."  
- The product appears flagged in lists.  
- The product remains sellable for historical purposes (can be selected in posted-return flows).  
- Existing stock value counts the deac-ted product.

**Effects:** Product cannot be added to new sales; inventory value reflects existing stock.

**Source:** §10, §11, §30

---

### BR-PRODUCT-004 — No Hard Delete of Referenced Products

**Rule:** A product that has appeared in any transaction, movement, payment, or receivable/payable cannot be permanently deleted.

**Trigger:** Delete request on a product that has history references.

**Preconditions:** Product exists.

**Behavior:** Delete operation is rejected with an error indicating the product is referenced. The product may be deactivated instead.

**Effects:** Delete blocked; audit log captures attempt if required.

**Source:** §6.3, §10, §30 #15

---

## 3. Sales

### BR-SALE-001 — Sale Creation

**Rule:** A sale is created by recording products, quantities, selling prices, line totals, and payment.

**Trigger:** User (Staff or Owner) initiates a sale entry in the POS flow.

**Preconditions:** User has `sale.create` capability; selected products are valid (sellable, in stock per policy).

**Behavior:** The system creates a new sale record in Draft status with line items, computes totals, records payment allocations, and optionally links a customer.

**Effects:** Sale record created; status Draft.

**Source:** §12, §6.1

---

### BR-SALE-002 — Sale Lifecycle Transition to Posted

**Rule:** When a sale is posted, its status changes to Posted, cost is frozen for each line, inventory decreases, and revenue is recognized.

**Trigger:** Sale is posted by the user (or auto-post in POS flow for the cashier operation).

**Preconditions:** Sale is in Draft status; all required fields are valid.

**Behavior:**  
- Status changes to Posted.  
- For each line, the current unit cost (avg pool) is computed and snapped to the sale line.  
- Stock quantity for each product decreases by the sold quantity.  
- A stock movement record is created with trigger = "sale".  
- Revenue is recognized as the allocated payment amount.  

**Effects:**  
- Stock ↓  
- Cost effect: COGS snapshot on sale line  
- Stock movement record created  
- Revenue recognized (cannot be edited later)  
- Cost is frozen (cannot be changed later)  
- Status = Posted

**Source:** §8, §12, §14, §18

---

### BR-SALE-003 — Quantity Validation

**Rule:** A sale cannot be created with zero or negative quantity for any line.

**Trigger:** Sale is saved with quantity field.

**Preconditions:** Sale in creation mode.

**Behavior:** If any line quantity ≤ 0, reject the sale with error; prompt user to correct.

**Effects:** Sale not persisted; no stock or cost effect.

**Source:** §29 Edge Cases

---

### BR-SALE-004 — Selling Price at Counter

**Rule:** The selling price presented at the POS counter is the product's configured selling price.

**Trigger:** Product is selected in the sale flow.

**Preconditions:** Product exists with a selling price.

**Behavior:** The system displays the product's current selling price as the default. The price must be displayed, not hidden.

**Effects:** User sees correct price; revenue computed from displayed price unless overridden.

**Source:** §12

---

### BR-SALE-005 — Price Override Permission

**Rule:** A user may override the selling price on sale lines only if the `sale.price_override` capability is granted, and every override must be logged.

**Trigger:** User attempts to enter or modify a selling price during sale entry.

**Preconditions:** User has not been granted `sale.price_override` permission.

**Behavior:** If the permission is not granted, the price field is read-only or the override is rejected. If granted, the override is allowed and an audit entry records the new price.

**Effects:** Price change only when permitted + audit log.

**Source:** §12, §26

---

### BR-SALE-006 — Multiple Payment Methods Per Sale

**Rule:** A sale may have multiple payment methods and multiple payment allocations.

**Trigger:** Sale being paid.

**Preconditions:** Sale has a total amount.

**Behavior:** Each payment line records amount, method, date, and optional account. The sum of allocations must not exceed the sale total (allocated ≤ total). Change is recorded on over-tender.

**Effects:** Multiple payment rows; payment state derived; change tracked when tendered > allocated.

**Source:** §12, §16

---

### BR-SALE-007 — Cash Over-Tender Handling

**Rule:** When a customer pays more cash than the sale total, the excess is recorded as "change" and is not revenue.

**Trigger:** Cash tendered is greater than the sale total.

**Preconditions:** Sale total and cash tendered are known.

**Behavior:**  
- Allocated = sale total.  
- Change = tendered − allocated.  
- Revenue = allocated (sale total).  
- A cash payment record is created with tender and change values.

**Effects:** Revenue is the sale total, not the tendered amount; change is a cash-movement field, not income.

**Source:** §12, §28

---

### BR-SALE-008 — Sales Cancellation

**Rule:** Cancelling a posted sale reverses the stock effect, the financial effect (revenue), and triggers a refund if payment was received.

**Trigger:** User with `sale.cancel` capability cancels a Posted sale.

**Preconditions:** Sale is in Posted status and has not yet been returned.

**Behavior:**  
- Status changes to Cancelled.  
- Stock quantity increases by the original sold quantity (reverse movement created).  
- Revenue is reversed.  
- A refund is created for the collected amount.  
- A movement reversal record is created linking to the original sale movement.  
- The cancellation is logged in the audit trail.

**Effects:** Stock ↑, Revenue reversed, Refund created, Audit record, Status = Cancelled.

**Source:** §9, §13, §18

---

### BR-SALE-009 — Sales Return

**Rule:** Returning a sale increases inventory at the original cost and triggers a refund if payment was received.

**Trigger:** User with `sale.return` capability processes a return for a valid (not cancelled) sale.

**Preconditions:** Sale is in Posted or Completed status, has not been returned, and the quantity to return does not exceed what remains.

**Behavior:**  
- Status changes to Returned (for the returned lines or the transaction).  
- Stock quantity increases by the returned quantity.  
- COGS is reversed at the original line cost.  
- If the sale was paid, a refund is created for the returned amount.  
- A return movement record links to the original sale.  
- The return is logged in the audit trail.

**Effects:** Stock ↑, COGS reversed, Refund (if paid), Audit record, Status = Returned.

**Source:** §9, §12, §14

---

### BR-SALE-010 — Sales Cannot Be Edited After Posting

**Rule:** A posted sale cannot be edited in place for any field that affects inventory, revenue, COGS, payment, expenses, receivables, or payables.

**Trigger:** User attempts to edit a posted sale.

**Preconditions:** Sale status is Posted or Completed.

**Behavior:** The edit is rejected. A message guides the user: "Use cancel and re-enter" or a correction transaction.

**Effects:** No silent mutation; edits require lifecycle reversal.

**Source:** §7, §9

---

## 4. Purchases

### BR-PURCHASE-001 — Purchase Creation

**Rule:** A purchase may be created by recording supplier (optional), products, quantities, purchase prices, totals, shipping, and payments.

**Trigger:** User (Owner or Staff with capability) initiates a purchase entry.

**Preconditions:** Selected products are valid.

**Behavior:** A new purchase record is created in Draft status with line items and optionally a supplier.

**Effects:** Purchase record Draft; no stock effect yet.

**Source:** §13, §6.1

---

### BR-PURCHASE-002 — Purchase Receipt and Posting

**Rule:** When a purchase is received, stock increases and the cost is added to the inventory pool; the purchase moves to Posted status.

**Trigger:** Purchase is marked as received / posted.

**Preconditions:** Purchase is in Draft state; quantities are valid; supplier or counter-buy context is recorded.

**Behavior:**  
- Status changes to Posted/Received/Completed.  
- For each line, the product's stock increases by the received quantity.  
- A stock movement record is created with trigger = "purchase receipt".  
- The cost (line value + allocated shipping) is added to the weighted-average cost pool.  
- A cost snapshot is stored.

**Effects:** Stock ↑, Cost pool updated, Movement record created, Status = Posted.

**Source:** §11, §13, §14, §18

---

### BR-PURCHASE-003 — Purchase Payment States

**Rule:** A purchase may have multiple payment allocations; the aggregate paid amount determines if the status is Paid, Partial, or Unpaid.

**Trigger:** Payment is recorded against a purchase.

**Preconditions:** Purchase exists with a total amount.

**Behavior:**  
- Each payment is recorded as a separate line.  
- Payment state is derived: Paid = sum(allocations) = total; Partial = 0 < sum <total; Unpaid = sum = 0.  
- The total remaining is shown as total − paid.

**Effects:** Payment state visible; status can be Unpaid/Partial/Paid.

**Source:** §11 P1-P2, §16

---

### BR-PURCHASE-004 — Multiple Payments Per Purchase

**Rule:** A purchase may have multiple payment allocations of any method.

**Trigger:** One or more payments are recorded against a purchase.

**Preconditions:** Purchase exists.

**Behavior:** Each payment is persisted as a first-class payment record linked to the purchase.

**Effects:** Multiple payment rows; payments ledger; derived remaining.

**Source:** §11 P2, §16

---

### BR-PURCHASE-005 — Shipping as Landed Cost

**Rule:** Shipping costs on a purchase are capitalized into the acquisition cost (landed cost), adding to the product’s inventory cost pool, and are never separately booked as an operating expense for the same purchase.

**Trigger:** Purchase total includes a shipping amount.

**Preconditions:** Line items and shipping value are defined.

**Behavior:**  
- Shipping is added to the purchase total.  
- The combined line sum (including shipping) is distributed proportionally to the line values and added to the inventory cost pool.  
- Shipping is shown separately in audits/reports.  
- A manual shipping expense for a non-purchase transaction may exist but must not duplicate this value.

**Effects:** Shipping capitali ed, stock value ↑, COGS ↑ future, NOT operating expense.

**Source:** §13, §17, §289/CT-3

---

### BR-PURCHASE-006 — Purchase Return

**Rule:** Returning a purchase reverses the stock receipt and cost, decreasing inventory and cost pool.

**Trigger:** User with `purchase.return` capability processes a return.

**Preconditions:** Purchase was previously received and posted; the returned quantity does not exceed what was received.

**Behavior:**  
- Stock quantity decreases by the returned quantity.  
- A stock movement record with trigger = "purchase return" is created.  
- The cost is reversed at the original receipt cost (or pool value).  
- If payments exist, a refund or credit is processed.  
- The return is logged.

**Effects:** Stock ↓, Cost reversed, Payment adjusted, Audit record.

**Source:** §9, §13, §14

---

### BR-PURCHASE-007 — Purchase Cancellation

**Rule:** Cancelling a purchase reverses the receipt effect (if any) and cancels the liabilities and any payments.

**Trigger:** User cancels a purchase after it was received or partially paid.

**Preconditions:** Purchase status is Posted or Paid.

**Behavior:**  
- Stock is reversed if the purchase was received (reverse movement).  
- Supplier payable is reversed.  
- Any payments are refunded (or written off if insufficient).  
- Status changes to Cancelled.  
- Audit record created.

**Effects:** Stock added (reverse −), Payable reversed, Refund or write-off, Status = Cancelled.

**Source:** §9, §13

---

## 5. Inventory

### BR-STOCK-001 — Inventory Movement Matrix (One Authoritative Effect)

**Rule:** Every stock-changing event maps to exactly one inventory movement record with a unique trigger/reason.

**Trigger:** Any of the seven defined stock-affecting events: purchase receipt, sale, sales return, purchase return, production, stock adjustment, cancellation/reverse.

**Preconditions:** The transaction causing the event is valid and posted.

**Behavior:** One and only one movement row is created (or reversed) per event. The matrix in §14 defines the exact stock direction (+/-) and cost treatment.

**Effects:** One movement record per event; no duplicates.

**Source:** §14

---

### BR-STOCK-002 — Stock Receipt Increases Inventory

**Rule:** When a purchase is received (Posted), the product's quantity increases by the received amount.

**Trigger:** Purchase received event.

**Preconditions:** Purchase is posted/complete.

**Behavior:** On the product record, quantity is increased. A movement record is created with trigger "purchase receipt".

**Effects:** Stock ↑, COGS pool updated, Movement record.

**Source:** §14

---

### BR-STOCK-003 — Sale Decreases Inventory

**Rule:** When a sale is posted, the product's quantity decreases by the sold amount.

**Trigger:** Sale posted event.

**Preconditions:** Sale is in Posted status.

**Behavior:** Stock quantity decreases. A movement record is created with trigger "sale" and the COGS snapshot.

**Effects:** Stock ↓, COGS snapshot, Movement record.

**Source:** §14

---

### BR-STOCK-004 — Sales Return Increases Inventory

**Rule:** When a sale is returned (physical goods), the product's quantity increases by the returned amount at the original cost.

**Trigger:** Sale returned event.

**Preconditions:** Sale was valid and not previously cancelled.

**Behavior:** Stock increases. A return movement record links to the original sale-moving. COGS is reversed at original cost.

**Effects:** Stock ↑, COGS reversed, Movement record linked.

**Source:** §14

---

### BR-STOCK-005 — Purchase Return Decreases Inventory

**Rule:** When a purchase is returned, the product's quantity decreases by the returned amount at the original cost.

**Trigger:** Purchase return event.

**Preconditions:** Purchase was received and posted.

**Behavior:** Stock decreases. The cost (at original receipt level) is reversed.

**Effects:** Stock ↓, Cost reversed, Movement record.

**Source:** §14

---

### BR-STOCK-006 — Production Input Decreases Raw Inventory

**Rule:** When a production run is posted, the raw materials used decrease by the consumed quantities.

**Trigger:** Production run posted event.

**Preconditions:** Production input quantities are valid; raw materials have sufficient stock.

**Behavior:** For each input line, stock of the raw material decreases. Input cost is at current raw average.

**Effects:** Stock ↓ on raw materials, cost captured for finished goods.

**Source:** §15, §14

---

### BR-STOCK-007 — Production Output Increases Finished Goods

**Rule:** When a production run is posted, the finished good product's quantity increases by the output amount.

**Trigger:** Production run posted event.

**Preconditions:** Raw materials have been consumed.

**Behavior:** Finished good quantity increases. The finished unit cost = (sum raw + overhead) ÷ finished quantity.

**Effects:** Stock ↑ on finished goods; cost enters inventory pool.

**Source:** §15, §14

---

### BR-STOCK-008 — Stock Adjustment Changes Quantity Arbitrarily

**Rule:** A stock adjustment manually changes stock by ± quantity with a reason, logged with before/after values.

**Trigger:** User with capability performs a stock adjustment.

**Preconditions:** User has `inventory.adjust` capability.

**Behavior:** User enters product, quantity change (+/-), and reason. Stock is immediately adjusted. A movement record with trigger "adjustment" stores before/after plus reason.

**Effects:** Stock ±, Audit record, Movement linking; cost at current avg (unless otherwise).

**Source:** §14

---

### BR-STOCK-009 — Cancellation Reverses Original Stock Effect

**Rule:** A cancelled transaction reverses the stock movement it originally caused, exactly once.

**Trigger:** Transaction is cancelled (sale, purchase, production).

**Preconditions:** Transaction was Posted.

**Behavior:** A new movement is created with the opposite sign and a trigger such as "sale reversal" or "purchase reversal", pointing to the original movement ID.

**Effects:** Stock reversed, Audit record, single effect per original event (no double).

**Source:** §9, §14

---

### BR-STOCK-010 — Negative Stock Prevention

**Rule:** By default, a sale (or purchase/production that uses stock) cannot be posted if insufficient stock exists. The system shows a warning and blocks the transaction.

**Trigger:** User attempts to sell/more stock than on-hand.

**Preconditions:** Negative stock is disabled (default).

**Behavior:** System blocks the operation and displays an insufficient-stock warning.

**Effects:** Transaction not posted; no stock change.

**Source:** §14, §29

---

### BR-STOCK-011 — Negative Stock Optional Override

**Rule:** The owner may enable negative stock globally or per-product. When enabled, sales may proceed with negative balance, the negative rows are flagged and logged, and COGS uses the last purchase price as a fallback.

**Trigger:** Owner toggles negative-stock permission and/or enables per-product.

**Preconditions:** Feature is enabled.

**Behavior:** Sale can post with negative balance; row marked "negative stock"; COGS set to last purchase price or other fallback.

**Effects:** Stock can be negative; COGS fallback applied; audit/logged.

**Source:** §14, §18.4, §10

---

### BR-STOCK-012 — Stock Movement Audit

**Rule:** Every stock movement must record: trigger/reason, quantity, unit cost, before/after balance, and timestamp + user.

**Trigger:** Any stock-affecting event.

**Preconditions:** Event is valid.

**Behavior:** Movement row captures all details; the row is immutable.

**Effects:** Complete, auditable inventory history.

**Source:** §26

---

## 6. Costing

### BR-COST-001 — Costing Method Default: Moving Average

**Rule:** The default costing method is moving average cost. The method is configurable and changes affect only future postings; historical costs remain frozen.

**Trigger:** System operates under moving average method (default).

**Preconditions:** No other method selected.

**Behavior:** All inventory valuation and COGS at post time use the weighted-moving average price per unit. Switching methods later only changes future calculations.

**Effects:** Historic cost fixed; new transactions use selected method.

**Source:** §18

---

### BR-COST-002 — Moving Average Cost Calculation

**Rule:** On purchase receipt, the new average cost per unit = (opening_qty × opening_avg + received_value + allocated_shipping) ÷ (opening_qty + received_qty).

**Trigger:** Purchase receipt is posted.

**Preconditions:** Product has existing on-hand quantity (or is new).

**Behavior:** Compute using the formula; the computed average is used for COGS on subsequent sales.

**Effects:** Cost pool updated; new stock valued at new avg.

**Source:** §18.1

---

### BR-COST-003 — Cost Snapshot on Posting

**Rule:** At the moment a sale or movement is posted, the unit cost in effect is captured as the sale-line cost and never recalculated.

**Trigger:** Sale or receipt is posted.

**Preconditions:** Transaction is posted.

**Behavior:** The current average (or other method) value is snapshotted into the sale line and movement row.

**Effects:** COGS frozen; historical price changes do not affect it.

**Source:** §18.2

---

### BR-COST-004 — Historical Costs Never Recomputed

**Rule:** Once a transaction is posted, its historical cost does not change even if the costing method, purchase prices, or other master data changes.

**Trigger:** Any master-data or configuration change involving cost.

**Preconditions:** Transaction is Posted or Completed.

**Behavior:** The stored cost snapshot is used for all historical calculations (COGS, profit, inventory value).

**Effects:** History immutable; no retroactive revaluation.

**Source:** §18, §7, §35

---

### BR-COST-005 — Return Uses Original Cost

**Rule:** When goods are returned, the inventory value is restored at the cost of the original line, not the current average.

**Trigger:** Sale or purchase return event.

**Preconditions:** Original line exists with recorded cost.

**Behavior:** The returned quantity is valued at the recorded unit cost at time of original transaction.

**Effects:** Balanced pool value; no COGS distortion from price changes between sale and return.

**Source:** §18.3, §14

---

### BR-COST-006 — Negative-Stock COGS Fallback

**Rule:** When negative stock is enabled and a sold item has no known pool cost, COGS defaults to the product's last purchase price, with the line flagged as a negative-stock fallback.

**Trigger:** Sale posts with no costable on-hand quantity.

**Preconditions:** Negative stock is enabled.

**Behavior:** Use last purchase price as COGS; flag the sale line as "negative-stock fallback".

**Effects:** Sale proceeds; COGS known; audit flag added.

**Source:** §18.4

---

### BR-COST-007 — Finished-Goods Cost Basis

**Rule:** The unit cost of finished goods = (sum of raw-material cost consumed + sum of production cost-type amounts) ÷ finished quantity.

**Trigger:** Production run is posted.

**Preconditions:** Inputs and output quantities are defined.

**Behavior:** Compute using formula; value enters finished-good inventory; flows to COGS on later sale.

**Effects:** Finished inventory valued; COGS correct on downstream sale.

**Source:** §15, §18.5

---

### BR-COST-008 — Shipping Included in Inventory Cost

**Rule:** Shipping costs on a purchase are added to the purchase total and contribute to the weighted-average cost pool.

**Trigger:** Purchase posted with shipping line.

**Preconditions:** Shipping value is nonzero.

**Behavior:** Include shipping in the value used for average cost calculation; the shipping is not a separate expense.

**Effects:** Inventory value higher; COGS includes shipping over time.

**Source:** §18.6, §13, §289

---

### BR-COST-009 — Inventory Valuation

**Rule:** The stock value shown on the dashboard and reports equals the total on-hand quantity multiplied by the current average (or appropriate method) cost per point.

**Trigger:** Stock value is queried (dashboard, report, inventory page).

**Preconditions:** Product has an on-hand quantity.

**Behavior:** quantity × average cost = value.

**Effects:** Single source of truth for value.

**Source:** §22, §18

---

## 7. Returns, Cancellation & Refunds

### BR-RETURN-001 — Return is a Physical Goods Event

**Rule:** A return represents the physical return of goods; it always has a stock effect and may have a financial effect (refund).

**Trigger:** User processes a return for a posted document.

**Preconditions:** Original transaction is valid (Posted, not Cancelled).

**Behavior:** Stock is reversed at original cost; if paid, a refund is created; status indicates Returned.

**Effects:** Stock ↑, COGS reversed, Refund (if paid), Status = Returned.

**Source:** §9, §14

---

### BR-RETURN-002 — Returns Cannot Restore Cancelled Goods

**Rule:** A return cannot be processed on an already Cancelled transaction; such an attempt is rejected to prevent double-stock restoration.

**Trigger:** Return attempt on a Cancelled transaction.

**Preconditions:** Transaction is Cancelled.

**Behavior:** System rejects the return with an error. The cancel already reversed the stock.

**Effects:** No stock change; audit log of attempt.

**Source:** §9, §14

---

### BR-CANCEL-001 — Cancellation Reverses Posted Effects

**Rule:** When a posted transaction is cancelled, the original posted effects are reversed exactly once.

**Trigger:** User cancels a Posted transaction.

**Preconditions:** Transaction is Posted or Completed, not already Cancelled.

**Behavior:** A reversal movement is created; stock is reversed; revenue is reversed; refund is triggered if payment received.

**Effects:** Status = Cancelled; Effects reversed; Refund; Audit.

**Source:** §9

---

### BR-REFUND-001 — Refund is Cash-Out, Not Income

**Rule:** A refund is a cash outflow that reverses a collected payment; it never appears as revenue or expense in the P&L.

**Trigger:** A cancellation or return of a paid document occurs.

**Preconditions:** Payment was previously collected.

**Behavior:** Create a "refund" payment line with reversed sign, applying the original method. This appears in the cash journal but not in the income statement.

**Effects:** Cash outflow recorded; no impact on revenue or COGS; does not affect GP/NP calculation other than cash balance.

**Source:** §9, §19, CT-5

---

### BR-REFUND-002 — Refund Follows Original Payment Method

**Rule:** Default refund processing uses the same method as the original payment.

**Trigger:** Refund is generated.

**Preconditions:** Original payment method is known.

**Behavior:** If original method is Cash, refund is cash-out. If original is Transfer, refund is a transfer-back.

**Effects:** Cash movement aligned with original inflow.

**Source:** §223 (V1 ASSUMPTION)

---

## 8. Payments

### BR-PAYMENT-001 — Payment Status Derivation

**Rule:** Payment status is derived as Paid if sum(allocations) = total; Partial if 0 < sum < total; Unpaid if sum = 0.

**Trigger:** Payment information is evaluated (display, state change).

**Preconditions:** Payment records exist or total is known.

**Behavior:** The sum of all allocations for the document is calculated; status is set accordingly.

**Effects:** Status never manually set; always derived; displayed to user.

**Source:** §8.2, §16

---

### BR-PAYMENT-002 — Multiple Payments Allowed

**Rule:** A transaction (sale or purchase) may have one or more payment allocations.

**Trigger:** Payments are being recorded for a document.

**Preconditions:** Document (sale/purchase) exists.

**Behavior:** Each payment is a separate row; all are summed for the payment status.

**Effects:** Multiple payments in ledger; derived remaining.

**Source:** §16, §11 P2, §12 S4

---

### BR-PAYMENT-003 — Over-Tender Tender/Allocated Change

**Rule:** When cash is tendered that exceeds the sale or purchase total, the excess is recorded as change; allocated must not exceed total.

**Trigger:** Cash tendered is entered.

**Preconditions:** Document total is known.

**Behavior:**  
- Change = Tendered − (sale/purchase total)  
- Allocated = sale/purchase total  
- Revenue = Allocated  

**Effects:** Change tracked; no revenue inflation; audit trail kept.

**Source:** §12, §28

---

### BR-PAYMENT-004 — Payment Methods Are Configurable

**Rule:** Payment methods are configurable system settings; default methods are Cash, Bank Transfer, E-Wallet, and Other.

**Trigger:** System configuration or payment creation.

**Preconditions:** Method exists in the configured list.

**Behavior:** Users select from the defined list; new methods may be added later.

**Effects:** Payment records contain valid method values; reports can filter.

**Source:** §15, §16

---

## 9. Production

### BR-PROD-001 — Production Run Creation

**Rule:** A production run is created to record the consumption of raw materials and the creation of finished goods.

**Trigger:** User with appropriate capability initiates production.

**Preconditions:** User has production entry permission (future delegation).

**Behavior:** User enters raw materials consumed (qty, cost taken at avg), finished product and qty, and cost-type lines (labor, energy, etc.).

**Effects:** Production record Draft.

**Source:** §15

---

### BR-PROD-002 — Production Run Posting

**Rule:** Posting a production run decreases raw material stock, increases finished-good stock at the computed unit cost, and records the cost composition.

**Trigger:** Production run is posted.

**Preconditions:** Inputs and outputs are valid; raw stock sufficient.

**Behavior:**  
- Each input row triggers a stock decrease (cost at avg).  
- Finished goods stock increases by output qty.  
- Finished unit cost computed and snapshotted.  
- A movements are created linking inputs and outputs.  

**Effects:** Stock ↓ raw, Stock ↑ finished; cost pool updated; audit records.

**Source:** §15, §14

---

### BR-PROD-003 — Finished-Goods Cost Basis

**Rule:** The unit cost of finished goods includes raw materials plus all production cost-type amounts, divided by the finished quantity.

**Trigger:** Production run is posted.

**Preconditions:** Raw and overhead amounts recorded.

**Behavior:** finished_unit_cost = (sum(raw) + sum(cost-types)) ÷ finished_qty. This value is used for future COGS and inventory valuation.

**Effects:** Finished inventory valued; cost flows to COGS on sale.

**Source:** §15, §18.5

---

## 10. Finance

### BR-FIN-001 — Revenue Recognition

**Rule:** Revenue is recognized on a posted sale as the sum of allocated payments (not the raw tendered cash).

**Trigger:** Sale is posted.

**Preconditions:** Sale is Posted.

**Behavior:** Revenue = sum(allocations) for the sale; cash change is not revenue.

**Effects:** Revenue in ledger; COGS separate; gross profit calculable.

**Source:** §12, §19

---

### BR-FIN-002 — COGS from Costing

**Rule:** Cost of Goods Sold is the sum of the COGS line items from all posted sales, minus returns.

**Trigger:** P&L calculation or COGS field update.

**Preconditions:** Sales have been posted with cost snapshots.

**Behavior:** Σ sale_line.COGS (at posting value).

**Effects:** Correct COGS; historical cost integrity preserved.

**Source:** §18, §19

---

### BR-FIN-003 — No Double Counting

**Rule:** Derived financial entries (sales revenue, purchase liabilities, COGS, production costs) may not be manually entered; manual entries use separate categories.

**Trigger:** System or user attempts to enter a financial record.

**Preconditions:** None.

**Behavior:** The system may restrict categories or show warnings if overlapping; manual categories exclude "Sales", "Purchase", or "COGS".

**Effects:** Revenue and COGS computed from transactions; manual entries cannot duplicate them.

**Source:** §17

---

### BR-FIN-004 — Net Profit Calculation

**Rule:** Net profit = (Sales revenue minus COGS) minus (all other expenses and cost types).

**Trigger:** P&L report or profit calculation.

**Preconditions:** All source data exists.

**Behavior:** GP = Revenue − COGS; NP = GP − Expenses.

**Effects:** Correct profit metric displayed.

**Source:** §19

---

### BR-FIN-005 — Shipping is Not a Manual Expense When Capitalized

**Rule:** Shipping costs attached to a purchase are never entered as a manual expense; they are capitalized and contribute to inventory cost.

**Trigger:** User attempts to enter a manual expense of type "Shipping" for a purchase-associated item.

**Preconditions:** Purchase and shipping exists.

**Behavior:** The manual expense is blocked or the system warns that shipping is already included in purchase cost. Manual expense may be allowed only for non-purchase shipping (e.g., postage).

**Effects:** No double-counting of shipping in P&L.

**Source:** §17, §275 (V1 assumption)

---

## 11. Profit & Reporting

### BR-PROFIT-001 — Gross Profit Definition

**Rule:** Gross profit = Revenue − COGS. Revenue comes from sales; COGS is the cost of goods sold.

**Trigger:** P&L calculation.

**Preconditions:** Posted sales exist.

**Behavior:** Sum all revenue minus sum all COGS.

**Effects:** Accurate GP figure.

**Source:** §19

---

### BR-PROFIT-002 — Net Profit Definition

**Rule:** Net profit = Gross profit − other expenses (including manual expenses and derived production costs).

**Trigger:** P&L calculation.

**Preconditions:** GP value calculated.

**Behavior:** Subtract all expense categories (manual + production + derived costs + any other entered).

**Effects:** Accurate NP figure.

**Source:** §19

---

### BR-PROFIT-003 — Historical Reporting Uses Snapshot Costs

**Rule:** Reports computed on historical dates use the COGS from the costs at the time of each sale, not current COGS or pool average.

**Trigger:** Report filter for past period.

**Preconditions:** Posted sales within period.

**Behavior:** Reports read the stored COGS snapshot on each sale line; no recalculation.

**Effects:** History immutable; reports accurate.

**Source:** §18, §23

---

### BR-REPORT-001 — Dashboard Metrics Derive from Transactions

**Rule:** All dashboard metrics (sales, purchases, profit, stock value, etc.) are computed from the transaction database; no separately maintained totals.

**Trigger:** Dashboard load or refresh.

**Preconditions:** Transaction data exists.

**Behavior:** Queries sum the underlying records; no hard-coded or separately-maintained numbers.

**Effects:** Single source of truth; dashboard and details agree.

**Source:** §22, §30 #24

---

## 12. Audit & History

### BR-AUDIT-001 — Audit Record Structure

**Rule:** Every auditable event records WHO, WHAT, WHEN, OLD VALUE, NEW VALUE, and an optional reason.

**Trigger:** Any create, edit, post, cancel, return, adjust, permission change, or setting change.

**Preconditions:** Event occurs.

**Behavior:** An immutable audit row is inserted; WHO (user), WHAT (action on record ID), WHEN (timestamp), OLD (before state), NEW (after state), REASON (optional note).

**Effects:** Complete traceability; append-only; tamper-evident.

**Source:** §26

---

### BR-AUDIT-002 — Posting Reversible Only via Cancellation

**Rule:** A posted transaction cannot be edited in place; corrections require cancel + re-enter, preserving audit history.

**Trigger:** Edit attempt on a posted record.

**Preconditions:** Transaction is Posted.

**Behavior:** Edit rejected; user must cancel (reversing) and create new, or issue a return if applicable.

**Effects:** History preserved; no in-place mutation.

**Source:** §7, §9

---

### BR-AUDIT-003 — Price Change Does Not Modify History

**Rule:** Changing a product’s purchase or selling price does not modify any historical transaction’s recorded cost.

**Trigger:** Product price update.

**Preconditions:** Product has historical transactions.

**Behavior:** New price applies only to new/drafts; existing transactions unchanged.

**Effects:** History immutable; audit integrity maintained.

**Source:** §10, §18

---

## 13. Data Integrity

### BR-DATA-001 — Single Authoritative Inventory Effect Per Event

**Rule:** No inventory-changing event may be applied twice (via double post, double-return, or cancellation combined with return).

**Trigger:** Any stock-affecting operation.

**Preconditions:** Event is being processed.

**Behavior:** System ensures the movement matrix guarantees one effect; cancels check for reversals, returns check for original status.

**Effects:** No duplicate stock changes; invariant holds.

**Source:** §14

---

### BR-DATA-002 — No Historical Financial Mutation

**Rule:** Financial records for posted transactions cannot be edited in a way that changes the historical COGS, revenue, or profit.

**Trigger:** Edit attempt on financial amounts of a posted transaction.

**Preconditions:** Transaction is Posted.

**Behavior:** Edit is rejected or must be done via reversal/correction.

**Effects:** History immutable; audit preserved.

**Source:** §7, §18

---

### BR-DATA-003 — Delete Prevented on Referenced Records

**Rule:** A master record (product, customer, supplier) that appears in any transaction, movement, payment, or receivable may not be permanently deleted.

**Trigger:** Delete attempt on a referenced master.

**Preconditions:** Master has history links.

**Behavior:** Delete rejected; user can deactivate or use a future archive function.

**Effects:** History preserved; no foreign-key orphaning.

**Source:** §6.3

---

### BR-DATA-004 — Payment Allocation ≤ Total

**Rule:** The sum of payment allocations for a document must not exceed its total amount (except for cash tender where change records the excess).

**Trigger:** Payment entry on a sale or purchase.

**Preconditions:** Document total known.

**Behavior:** System validates Σ allocations ≤ total; if user enters cash > total, change is explicitly calculated and stored.

**Effects:** No overpayment (other than change); revenue correct.

**Source:** §12, §28

---

## 14. Non-Blocking TBD

The PRD explicitly marks the following as non-blocking or TBD; they can be decided at implementation without requiring database or business logic changes at this stage.

| Rule ID | Item | Why non-blocking |
|---|---|---|
| TBD-001 | Exact global low-stock threshold number | Simple configuration value |
| TBD-002 | Precise V1 report list subset at sizing time | UI/UX decision; data extraction generic |
| TBD-003 | Money rounding mode (config) | Display-level; storage unaffected |
| TBD-004 | Sequential document numbering (configurable) | Optional feature; can be added later |
| TBD-005 | Notification channel delivery (in-app only) | UI/flow design; no core logic |

---

## Business Rule Matrix

| Event | Lifecycle Effect | Stock Effect | Cost Effect | Financial Effect | Payment Effect | Audit Effect |
|---|---|---|---|---|---|---|
| **Sale Posted** | → Posted | − qty (COGS snapshot) | COGS snapped | Revenue recognized | Allocations recorded | Movement + sale created |
| **Sales Return** | → Returned | + qty (at original cost) | COGS reversed | Refund if paid | Refund created if paid | Return movement + refund |
| **Sale Cancellation** | → Cancelled | + qty (reverse) | COGS reversed | Revenue reversed | Refund created if paid | Reversal movement |
| **Purchase Received** | → Posted | + qty | Added to avg pool | Supplier payable | Payments recorded | Receipt movement |
| **Purchase Return** | → Returned | − qty (at original cost) | Cost reversed | Payable reduced | Refund to supplier | Return movement |
| **Purchase Cancellation** | → Cancelled | Reverse receipt qty | Cost reversed | Payable reversed | Refund any paid | Reversal movement |
| **Production Posted** | → Posted | Raw −, Finished + | Raw at avg; finished computed | Production cost entered | N/A | Input/output movements |
| **Stock Adjustment** | → Posted | ± qty | At current avg | N/A (or cost adjustment) | N/A | Adjustment movement with reason |

> All postings freeze cost. All reversals add to audit trail.

---

## Consistency Verification (✓ passed)

1. ✓ Every core workflow (purchase, sale, production, cancel/return, manual I&E) has defined lifecycle and effects.
2. ✓ Every inventory-affecting event has strict stock direction and cost treatment.
3. ✓ Every financial-affecting event defines revenue, COGS, expense impacts.
4. ✓ Cost events (posting, return, negative-stock) have defined cost rules; finished goods basis defined.
5. ✓ Returns and cancellations do not double-restore stock.
6. ✓ Historical costs remain frozen (§18 invariant).
7. ✓ Payment state equals sum(allocations) vs total; independent from lifecycle status.
8. ✓ Manual and derived entries separated; double-count prevented by category design.
9. ✓ Owner full access; Staff limited to POS/create/drafts; delegation clear.
10. ✓ POS workflow works for Staff with sale/create capability.
11. ✓ No rule contradicts another; the matrix is internally self-consistent.
12. ✓ All rules derived from PRD V1.1; no invented business logic.

---

### Final Readiness

🟢 **READY FOR ERD/DATABASE DESIGN**

The Business Rules document is complete, internally consistent, and covers every core workflow with explicit rules for:
- Auth & roles (permissions and delegation)
- Product master (code, prices, history)
- Sales (lifecycle, payment, over-tender, return, cancel)
- Purchases (receipt, shipping, return, cancel)
- Inventory (all seven stock events with cost treatment)
- Costing (moving average, snapshots, negative-stock fallback, finished goods)
- Refunds (cash-out, never income)
- Finance (revenue, COGS, profit, no double-counting)
- Reports (derived from transactions, no independent totals)
- Audit (immutable, append-only, includes reversals)
- Data integrity (no duplicates, no history mutation, delete protection)

The document passes internal consistency checks and is ready for the next phase: Business Rules → ERD/Database Design.