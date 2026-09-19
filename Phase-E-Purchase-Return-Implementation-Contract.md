# Phase E Purchase Return Implementation Contract

**Research task: inspect authoritative docs + actual code + DB schema. No code, no migrations, no doc changes, no commits, no generated files.**

---

## 1. Authority & Conflicts

**Authority order (highest → lowest):**

1. **`schema.sql`** — actual DB DDL + triggers (ground truth for enforcement).
2. **`V1.9-Accounting-Event-Matrix.md`** — accounting event definitions + INV invariants (§4).
3. **`Database-Design-V1.0.md`** §5.4–§5.5, §8.4–§8.5 — accounting model rules + derived balance formulas.
4. **`Business-Rules.md`** — BR-PURCHASE-006/007 (stock behavior; silent on partial-payment SREC split).
5. **`Backend-Architecture-V1.0.md`** §15.7, §15.8 — service-layer algorithm specification.
6. **`API-Architecture-V1.0.md`** §9.4, §15.12.10–10.12.11 — API-level behavior.
7. **`openapi.yaml`** — external API contract (schemas + endpoints).
8. **`backend/app/services/purchases.py`** — actual implementation (authoritative for what currently runs).
9. **`backend/app/services/supplier_repayments.py`** — actual implementation.
10. **`backend/app/repositories/purchases.py`** — actual SQL queries.
11. **`frontend/app/(protected)/purchases/[id]/page.tsx`** — current UI state.

**Conflicts found and resolved:**

| # | Conflict | Source A | Source B | Resolution |
|---|----------|----------|----------|------------|
| 1 | **Return-created SREC: does a partially-paid purchase return create a `supplier_repayments` row?** | API-Architecture §15.12.10 step 5: "If paid: insert `supplier_repayments`" (ambiguous — "paid" = fully paid only?) | DB-Design §5.4 line 1037: "when a return occurs on a paid purchase, the system automatically records an INSERT into `supplier_repayments`" (same ambiguity) | **See §3.** The candidate rule (AP reduction = `min(return, remaining AP)`, excess = SREC) is not stated explicitly anywhere. DB-Design §5.4 explicitly says: "If purchase unpaid: AP reduced … If purchase paid: Supplier Receivable increase." No document states what happens at *partial* payment. This is a **business decision required** (see §13, Decision 1). |
| 2 | **`supplier_repayments` schema comment claims a cross-row trigger on `amount`** | `schema.sql:1253` COMMENT: "Cross-row trigger: `amount ≤ (Σ purchase_repayments − Σ supplier_repayments received)` (INV-04)" | `schema.sql` trigger list (lines 1690–2201): no such trigger exists for `supplier_repayments`. Only CHECK constraints `ck_srep_amount_pos`, `ck_srep_received_nonneg`, `ck_srep_received_le_amount`, `ck_srep_snapshot_nonneg` exist. | **The comment is documentation-only; the actual enforcement is application-layer** via `SupplierRepaymentService.create_repayment` (see §7). The same stale comment exists on `refunds` (schema.sql:1209). |
| 3 | **SREC formula in `SupplierRepaymentService`** | `SupplierRepaymentService` line 3 + line 167: `SREC = outstanding - Σ received_amount` | DB-Design §8.4 line 1170: `Supplier Receivable (total) = Σ supplier_repayments.amount - Σ supplier_repayments.received_amount` | **Database Design controls.** The service formula is **incorrect** for return-generated SREC (see §6, §7). This is a **technical gap** (see §13, Decision 2). |
| 4 | **`cancel_return` bypasses the `purchase_returns` update trigger** | `schema.sql:2188-2190`: `trg_purchase_returns_bump_version` → `fn_bump_version_and_updated_at()` which sets `NEW.updated_at := NOW()` | `purchase_returns` table (schema.sql:833–851) has **no `updated_at` column** | **Actual code works around it** via `cancel_return` repo method (purchases.py:701–730) which sets `session_replication_role = replica` to bypass the broken trigger. This is a **schema defect** (see §8). |
| 5 | **`add_payment` SREC deduction** (`max_payable` formula) | `purchases.py:1374-1377`: `max_payable = max(0, total - existing_paid - srec_received)` | DB-Design §8.4 line 1171: `AP (per purchase) = Σ purchase_payments - Σ supplier_repayments (paid_received)` | **Consistent.** The service code matches the DB-Design formula (AP is reduced by both payments and supplier-repayment receipts). No conflict. |
| 6 | **"Return is paid → create supplier_repayments with `amount = total_value_returned`"** | API-Architecture §15.12.10: "amount = total_value_returned" | DB-Design §5.4: "amount = Σ line_value_returned" | Both equal (same quantity). No conflict. |

---

## 2. Final Accounting Rules

**Definitions (per DB-Design §8.4 + M6):**

- **AP (Accounts Payable, liability 2010):** `purchase_total - Σ purchase_payments`. Never negative (INV-03).
- **SREC (Supplier Receivable, asset 1300):** `Σ supplier_repayments.amount - Σ supplier_repayments.received_amount`. Never negative (INV-03).
- **Cash:** `Σ cash_movements.amount`. Never negative (INV-03, enforced by `trg_cash_movements_balance_check`, schema.sql:1668–1687).

**Return accounting (Event 15, V1.9 §3.3):**

- **If purchase unpaid (paid = 0, remaining AP = total):**
  ```
  Dr  2010 Accounts Payable          return_value
  Cr  1200 Inventory                    return_value
  ```
  AP ← `total − paid − return_value`. SREC unchanged. No `supplier_repayments` row.

- **If purchase paid (paid = total, remaining AP = 0):**
  ```
  Dr  1300 Supplier Receivable       return_value
  Cr  1200 Inventory                    return_value
  ```
  AP = 0. SREC ← `return_value`. A `supplier_repayments` row is created with `amount = return_value`, `received_amount = 0`.

- **If purchase partially paid (paid < total, remaining AP = total − paid > 0):**
  - `AP_reduction = min(return_value, remaining_AP)`
  - `SREC_created = max(0, return_value - remaining_AP)`
  - Journal: `Dr 2010 AP (AP_reduction) + Dr 1300 SREC (SREC_created) / Cr 1200 Inventory (return_value)`
  - If `SREC_created > 0`: create `supplier_repayments` row with `amount = SREC_created`, `received_amount = 0`, `reason = 'purchase_return_credit'`.
  - If `SREC_created = 0`: no `supplier_repayments` row.

**This candidate rule (AP reduction + SREC split) is technically compatible** (M6 §8) with all invariants, schema CHECK constraints, and precedents (Event 12, Event 15). However, **no authoritative document explicitly states this partial-payment split** for returns. The documents only cover the binary "paid vs unpaid" case.

---

## 3. Partial-Payment Scenarios

Formula per return:
- `remaining_AP_before = max(0, total − paid)`
- `AP_reduction = min(return_value, remaining_AP_before)`
- `SREC_created = max(0, return_value − remaining_AP_before)`
- `final_AP = remaining_AP_before − AP_reduction`
- `final_paid = paid` (cash unchanged)
- `final_SREC = SREC_created + Σ prior return_created SREC`

### Scenario Table

| Scenario | Total | Paid | Return Value (G_ret) | Rem. AP before | AP reduction | Final AP | SREC created | SREC row amount | Final Paid | Inventory effect | Journal (Dr ...) | Journal (Cr ...) | Another purchase payment allowed? | Supplier repayment allowed? |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **A** | 100 | 0 | 30 | 100 | 30 | 70 | 0 | 0 | 0 | −30 | Dr AP 30 | Cr Inv 30 | Yes (≤70) | No (SREC=0) |
| **B** | 100 | 40 | 30 | 60 | 30 | 30 | 0 | 0 | 40 | −30 | Dr AP 30 | Cr Inv 30 | Yes (≤30) | No (SREC=0) |
| **C** | 100 | 40 | 60 | 60 | 60 | 0 | 0 | 0 | 40 | −60 | Dr AP 60 | Cr Inv 60 | Yes (≤0, i.e. no) | No (SREC=0) |
| **D** | 100 | 60 | 50 | 40 | 40 | 0 | 10 | 10 | 60 | −50 | Dr AP 40, Dr SREC 10 | Cr Inv 50 | No (AP=0) | Yes (≤10) |
| **E** | 100 | 100 | 40 | 0 | 0 | 0 | 40 | 40 | 100 | −40 | Dr SREC 40 | Cr Inv 40 | No (AP=0) | Yes (≤40) |

**Balancing check (Assets = Liabilities + Equity):**
- **A:** Δ Assets = −30 (Inv), Δ Liab = −30 (AP). −30 = −30 + 0 ✓
- **B:** Δ Assets = −30 (Inv), Δ Liab = −30 (AP). −30 = −30 + 0 ✓
- **C:** Δ Assets = −60 (Inv), Δ Liab = −60 (AP). −60 = −60 + 0 ✓
- **D:** Δ Assets = +10 (SREC) − 50 (Inv) = −40, Δ Liab = −40 (AP). −40 = −40 + 0 ✓
- **E:** Δ Assets = +40 (SREC) − 40 (Inv) = 0, Δ Liab = 0 (AP). 0 = 0 + 0 ✓

All scenarios balance. The candidate rule is **accounting-consistent**.

---

## 4. Multiple Returns

**Current implementation (purchases.py:1718–1872):**

- Each `create_return` call creates one `purchase_returns` row + its `purchase_return_lines`.
- `fn_prl_quantity_bound` trigger (schema.sql:1908–1935) enforces `Σ return qty per purchase_line ≤ original qty` at the DB level.
- `sum_returned_qty_for_line` (purchases.py:783–793) excludes cancelled returns (`pr.lifecycle_status = 'posted'`).
- `total_value_returned` on each `purchase_returns` row is independent per return.

**Multiple-return rules:**

| Rule | Current enforcement | Gap |
|---|---|---|
| Cumulative returned qty ≤ original qty | `fn_prl_quantity_bound` trigger (DB) + service pre-check (purchases.py:1740–1750) | **None** — DB trigger is the authoritative guard |
| Can create multiple returns | Yes (no unique constraint on purchase_id) | — |
| SREC is per-return | DB-Design §8.4: SREC is derived as `Σ supplier_repayments.amount − Σ received_amount` across **all** rows for a purchase. Not per-return. | **Undefined:** current code does NOT create `supplier_repayments` rows on return at all (see §10, Gap). For multiple returns creating SREC, the formula must be **cumulative** across the purchase, not per-return. |
| Later return creates additional SREC after earlier return consumed AP | Logically: `SREC_created = max(0, return_value − remaining_AP_after_prior_returns)`. | **Undefined** — depends on Decision 1 (partial-payment split). Current code has no SREC creation on return. |
| Cancelled return reduces returned qty | `sum_returned_qty_for_line` excludes `cancelled` returns | — |

**Example — Multiple returns with SREC:**
- Purchase: total=100, paid=40, lines: 10×@10 each.
- Return #1: 5 units (value=50). Remaining AP = 60. AP_reduction=50, SREC_created=0. AP=10.
- Return #2: 3 units (value=30). Remaining AP=10. AP_reduction=10, SREC_created=20. AP=0. SREC=20 (new row, amount=20).
- Return #3: 2 units (value=20). Remaining AP=0. AP_reduction=0, SREC_created=20. AP=0. SREC=40 (another row, amount=20).
- Total returned = 10/10 units. Purchase → `returned`.
- SREC = Σ amount − Σ received = 40 − 0 = 40. ✓

---

## 5. Payment After Return

**Current implementation (`add_payment`, purchases.py:1285–1482):**

- `max_payable = max(0, total − existing_paid − srec_received)` (line 1377).
- `srec_received = Σ supplier_repayments.received_amount` (line 1374, via `sum_supplier_repayments_for_purchase`).
- This computes remaining AP as `total − payments − received_repayments`.

**Payment-after-return scenarios:**

| Case | Behavior | Current code | Gap? |
|---|---|---|---|
| Unpaid → return (AP reduced) → payment | Payment capped at remaining AP | ✓ | **No** (if return reduces AP via the DB trigger on `purchase_payments`) |
| Partial-paid → return (AP+SREC split) → payment | Payment capped at remaining AP | ✓ for AP portion (if SREC row has `received_amount=0`, it doesn't affect `max_payable`) | **The AP reduction itself is NOT implemented** — current `create_return` does not modify AP via payment rows; AP is derived as `total − payments`. If return doesn't insert a payment row or modify total, AP is unchanged. See §10. |
| SREC created by return → payment attempt | `max_payable` does not include SREC (only `received_amount`), so payment is not blocked by SREC | ✓ | **No** (correct behavior — SREC ≠ AP; payment should only reduce AP) |
| Payment exceeds remaining AP after returns | 409 `allocation_exceeds_payable` via `fn_purchase_payment_allocation_bound` trigger (schema.sql:1870–1895) | ✓ | **None** — DB trigger enforces Σ payments ≤ total. **But** this trigger does NOT account for returns reducing AP. It checks `Σ purchase_payments.amount ≤ purchase total`. Returns do NOT reduce `total` (total is derived from `purchase_lines.line_total`). |

**Critical finding:** The DB trigger `fn_purchase_payment_allocation_bound` (schema.sql:1870) enforces `Σ purchase_payments ≤ Σ purchase_lines.line_total + shipping`. **Return value is NOT subtracted from this total.** This means:

- After a return of 50 on a 100 purchase (paid=0), AP should be 50.
- But the trigger still allows payments up to 100 (the full line total).
- The **application-layer** `max_payable` check (purchases.py:1377) is the effective guard, but it does NOT subtract return value from the total — it only subtracts `existing_paid` and `received_amount` from `total`.

**The actual invariant that prevents overpayment after returns is application-layer (purchases.py:1378–1387), NOT the DB trigger.** The DB trigger will let an overpayment through if the app-layer check is bypassed.

---

## 6. Supplier Repayment / SREC

### 6.1 How return-created SREC is represented

**Currently: not represented at all.** The `create_return` method (purchases.py:1677–1872) does NOT insert any `supplier_repayments` row. It only:
1. Creates `purchase_returns` + `purchase_return_lines`.
2. Creates `stock_movements` (trigger='purchase_return', negative qty).
3. Updates `purchases.lifecycle_status`.

**No `supplier_repayments` row is created for paid or partially-paid returns.** This is a **gap** — the documented behavior (API-Architecture §15.12.10 step 5, DB-Design §5.4) says a row should be created "if paid."

### 6.2 Exact `supplier_repayments` fields (from schema.sql:1255–1280)

| Field | Type | Purpose |
|---|---|---|
| `id` | BIGSERIAL PK | Row ID |
| `purchase_id` | BIGINT FK | Links to purchase |
| `amount` | NUMERIC(15,2) | Total obligation (credit amount for return; Σ paid for direct repayments) |
| `received_amount` | NUMERIC(15,2) DEFAULT 0 | Cumulative cash received against this obligation |
| `payment_method_id` | INT FK | How cash was received |
| `repayment_date` | TIMESTAMPTZ | Date |
| `reason` | TEXT | Distinguishes 'purchase_return_credit' vs 'supplier_repayment' vs 'purchase_cancellation' |
| `refundable_amount_snapshot` | NUMERIC(15,2) | Audit snapshot of SREC at creation time |
| `created_at`, `created_by` | — | Audit |

### 6.3 How `SupplierRepaymentService` currently calculates SREC

**Current formula (supplier_repayments.py:173–177):**
```python
outstanding = max(0, total - paid)        # remaining AP
srec_received = sum_supplier_repayments_for_purchase(purchase_id)  # Σ received_amount
srec = max(0, outstanding - srec_received)
```

**This is WRONG for return-generated SREC:**

- `outstanding = total − paid` = remaining AP.
- `srec = outstanding − srec_received` = `remaining_AP − Σ received_amount`.
- This formula computes SREC as `remaining_AP − received`, but the **true SREC per DB-Design §8.4 line 1170** is `Σ supplier_repayments.amount − Σ supplier_repayments.received_amount`.

**Example of the bug:**
- Purchase: total=100, paid=100, remaining AP=0.
- Return 40: creates SREC=40. `supplier_repayments` row: `amount=40, received_amount=0`.
- User wants to record supplier actually repaid 10:
  - Current service: `outstanding = 0`, `srec_received = 0`, `srec = 0`. **Rejects** with `RepaymentExceedsSREC`.
  - Correct: SREC = 40 − 0 = 40. **Should allow** up to 40.

### 6.4 What `SupplierRepaymentService.create_repayment` currently does

1. Idempotency check (Idempotency-Key required).
2. `SELECT ... FOR UPDATE` on purchase.
3. `If-Match` ETag check.
4. `total = Σ line_total + shipping` (lines 168–171).
5. `paid = Σ purchase_payments` (line 172).
6. `outstanding = max(0, total − paid)` (line 173).
7. `srec_received = Σ supplier_repayments.received_amount` (line 174).
8. `srec = max(0, outstanding − srec_received)` (line 177).
9. If `amount > srec`: raise `RepaymentExceedsSREC` (line 179).
10. Cash guard for cash methods (lines 188–207).
11. `total_obligation = outstanding` (line 221).
12. `new_received = min(srec_received + amount, total_obligation)` (lines 222–224).
13. INSERT `supplier_repayments` with `amount = total_obligation if > 0 else amount`, `received_amount = new_received`, `refundable_amount_snapshot = srec` (lines 226–235).
14. If cash: INSERT `cash_movements` (direction='in', amount=+amount, trigger='supplier_repayment') (lines 237–254).

**Problem:** This service creates a new `supplier_repayments` row on every call (no `find_supplier_repayment_for_return` lookup — **function does not exist**). It uses `outstanding` (= remaining AP) as `amount` (the obligation), which is wrong for return-created SREC.

**No `delete_supplier_repayment` endpoint exists** (verified: no DELETE route in `openapi.yaml`, no method in `supplier_repayments.py` API or service, no repo method). There is **no way to delete or reverse a supplier repayment record**.

### 6.5 Can a supplier repayment be created against a return-created credit?

**Currently: NO.** Because `create_return` does not create `supplier_repayments` rows, there is no return-created SREC to repay against. And `SupplierRepaymentService` computes SREC as `outstanding_AP − received`, which is 0 for a fully-paid purchase with a return (return_value=40, paid=100).

**Once return-generated SREC rows exist (`amount=40, received_amount=0`):**
- `sum_supplier_repayments_for_purchase` returns `Σ received_amount = 0`.
- Current formula: `srec = max(0, 0 − 0) = 0`. Still rejects.
- **Must be fixed** to `srec = Σ amount − Σ received_amount = 40 − 0 = 40`.

### 6.6 Return cancelled after supplier repayment received

This is **undefined and dangerous.** The current `cancel_return` (purchases.py:1929–2046):
1. Inserts `purchase_return_reversal` stock movements.
2. Calls `cancel_return` repo (sets `lifecycle_status='cancelled'`, bumps version).
3. Does **NOT** touch any `supplier_repayments` row.

If a return created a `supplier_repayments` row (future), and the supplier already repaid cash (`received_amount > 0`), cancelling the return would:
- Restore inventory stock (stock movements).
- But leave `supplier_repayments` in place with `received_amount > 0`.
- No reversal of cash or SREC.

**This is a gap.** The correct behavior would be to reverse the SREC and the cash movement (if any received). But since `cancel_return` cannot delete `supplier_repayments` (no delete mechanism exists), this needs a design decision (see §13, Decision 3).

---

## 7. Database Enforcement

| Invariant | Enforcement | File:Line | Notes |
|---|---|---|---|
| Returned qty ≤ purchased qty | **DB trigger** `fn_prl_quantity_bound` | `schema.sql:1908–1935` | Triggered on INSERT/UPDATE of `purchase_return_lines`. Excludes the current row. ✓ Authoritative. |
| Purchase payment ≤ purchase total | **DB trigger** `fn_purchase_payment_allocation_bound` | `schema.sql:1870–1895` | Sum of `purchase_payments.amount ≤ Σ line_total + shipping`. **Does NOT subtract returns.** Payment-after-return is only guarded at the app layer (purchases.py:1377). |
| Payment ≤ cash balance | **DB trigger** `fn_cash_movements_balance_check` | `schema.sql:1668–1687` | `SUM(cash_movements.amount) ≥ 0` after insert. |
| `received_amount ≤ amount` (repayment) | **DB CHECK** `ck_srep_received_le_amount` | `schema.sql:1276` | ✓ |
| `amount > 0` (repayment) | **DB CHECK** `ck_srep_amount_pos` | `schema.sql:1272` | ✓ |
| `received_amount ≥ 0` | **DB CHECK** `ck_srep_received_nonneg` | `schema.sql:1274` | ✓ |
| `refundable_amount_snapshot ≥ 0` | **DB CHECK** `ck_srep_snapshot_nonneg` | `schema.sql:1278` | ✓ |
| `total_value_returned > 0` | **DB CHECK** `ck_purchase_returns_value_pos` | `schema.sql:848` | ✓ |
| Purchase lifecycle transitions | **DB trigger** `fn_purchase_lifecycle_terminal` | `schema.sql:1833–1861` | draft→posted\|cancelled; posted→completed\|partially_returned\|returned\|cancelled; partially_returned→returned\|cancelled; returned→cancelled; completed→partially_returned\|cancelled. ✓ |
| Purchase return lifecycle | **DB trigger** `fn_purchase_returns_lifecycle_terminal` | `schema.sql:2083–2105` | posted→cancelled only. ✓ |
| `supplier_repayments.received_amount ≤ (Σ payments − Σ received)` | **Documented only** | `schema.sql:1253` COMMENT | **Claimed in comment but NO trigger exists.** Actual enforcement is application-layer (`SupplierRepaymentService.create_repayment` line 179). **Gap.** |
| Return-created SREC ceiling | **None** | — | No DB trigger on `supplier_repayments` linking to `purchase_returns`. SREC from returns is unbounded at the DB level. App layer must enforce. |
| Concurrent AP/SREC corruption | **App-layer** `FOR UPDATE` + ETag | purchases.py:1707,1323,1617; supplier_repayments.py:160 | `get_for_update` locks the purchase row. ETag/If-Match on lifecycle. ✓ |
| Post-return payment ceiling | **App-layer** only | purchases.py:1377 | `max_payable = total − paid − srec_received`. DB trigger `fn_purchase_payment_allocation_bound` does NOT subtract returns. **Gap.** |

**Key DB enforcement findings:**

1. **`fn_prl_quantity_bound` (schema.sql:1908–1935):** Correctly enforces `Σ return qty ≤ original qty` per `purchase_line_id`. Excludes current row in UPDATE. Raises `check_violation` with message referencing BR-PURCHASE-006. ✓
2. **`fn_purchase_payment_allocation_bound` (schema.sql:1870–1895):** Enforces `Σ payments ≤ Σ line_total + shipping`. **Does not subtract `Σ purchase_return_lines.line_value`.** After a return reduces the payable, the DB will still allow a payment up to the original total. The app-layer check (purchases.py:1377–1387) is the real guard.
3. **No trigger on `supplier_repayments`** enforces `amount ≤ AP` or links to return-created credits. The comment at schema.sql:1253 is **stale/misleading**.
4. **`trg_purchase_returns_bump_version` (schema.sql:2188–2190)** calls `fn_bump_version_and_updated_at()` which does `NEW.updated_at := NOW()` — but `purchase_returns` has **no `updated_at` column** (schema.sql:833–851). The repo's `cancel_return` (purchases.py:701–730) works around this by setting `session_replication_role = replica`. This is a **schema defect**.

---

## 8. Concurrency & Transaction Safety

### 8.1 Audit of specific code paths

**`find_supplier_repayment_for_return` (requested for audit in task §8.2):**
- **Does not exist.** Grep of entire `backend/` returns 0 matches. This function is referenced conceptually but is **not implemented**.

**Return creation (`create_return`, purchases.py:1677–1872):**
- `SELECT ... FOR UPDATE` on `purchases` (line 1707 via `get_for_update`). **Locks the purchase row.** ✓
- Idempotency key check before the lock (lines 1690–1705). ✓
- ETag check after lock (line 1715–1716). ✓
- **`fn_prl_quantity_bound` trigger** provides the authoritative quantity check — even if two concurrent returns compute the same remaining qty, only one will succeed; the second hits the DB trigger. ✓
- **No `supplier_repayments` insert** → no concurrency issue on SREC rows yet (they don't exist).

**`Payment creation (`add_payment`, purchases.py:1285–1482):**
- `SELECT ... FOR UPDATE` on purchase (line 1323). ✓
- Idempotency (lines 1297–1321). ✓
- ETag (line 1335). ✓
- `max_payable` check (lines 1377–1387) — app-layer ceiling.
- `fn_purchase_payment_allocation_bound` trigger — DB-layer ceiling (but doesn't subtract returns). ✓ for basic payment ceiling, ✗ for post-return payments.
- Cash balance check (app-layer, lines 1391–1403; DB trigger is the guard).

**`Supplier repayment creation (`create_repayment`, supplier_repayments.py:123–281):**
- `SELECT ... FOR UPDATE` on purchase (line 160). ✓
- Idempotency (lines 144–157). ✓
- ETag check (line 164). ✓
- `srec` computed as `outstanding − received` (line 177) — **incorrect formula** (see §6.3). This means concurrent repayments could both pass the check if they both read the same SREC value, then both insert rows. **No `supplier_repayments` row is upserted — each call INSERTs a new row** (line 226). With `amount = outstanding`, concurrent calls would create duplicate/overstated SREC rows.
- **Race on `received_amount`:** No `FOR UPDATE` on existing `supplier_repayments` rows. Two concurrent repayments could read the same `received_amount`, both pass the check, and both insert rows. The new rows are independent (no UPDATE of `received_amount` on existing rows). This is a **TOCTOU gap**.

**Return cancellation (`cancel_return`, purchases.py:1929–2046):**
- `get_return_for_update` — but `purchase_returns` has no FK row lock mechanism shown; verify.
- ETag check (line 1965–1966). ✓
- `session_replication_role = replica` to bypass broken trigger (repo level, purchases.py:714).
- **Does not lock or check `supplier_repayments`** — if SREC rows existed, cancellation could leave them dangling.

### 8.2 Transaction boundaries

All service methods run inside `async with UnitOfWork() as uow:` blocks (see API layers: purchase_returns.py:113, supplier_repayments.py:119). The `uow.commit()` is called in the API handler after the service returns (e.g., purchase_returns.py:134, supplier_repayments.py:139). **All mutations within a single service call are atomic.** ✓

### 8.3 Idempotency

| Endpoint | Idempotency-Key required? | Current |
|---|---|---|
| `POST /purchases/{id}/returns` | Per OpenAPI §15.12.10 | ✓ (purchase_returns.py:125) |
| `POST /purchase-returns/{id}/cancel` | Per OpenAPI §15.12.11 | ✓ (purchase_returns.py:125) |
| `POST /purchases/{id}/payments` | Per OpenAPI §15.12.9 | ✓ (inferred from purchases.py:1299) |
| `POST /supplier-repayments` | Per OpenAPI §15.13.3 | ✓ (supplier_repayments.py:125) |

**Idempotency uses `compute_request_fingerprint`** (SHA-256 of canonical body). Replay returns the cached response body. ✓

### 8.4 ETags / If-Match

| Endpoint | If-Match required? |
|---|---|
| `POST /purchases/{id}/returns` | Per OpenAPI: required | **Current code: reads `if_match` but passes `parse_if_match_optional`** (purchase_returns.py:125) — If-Match is optional in the implementation. **Gap vs. spec.** |
| `POST /purchase-returns/{id}/cancel` | Per OpenAPI: required | Same — `parse_if_match_optional` (purchase_returns.py:198). **Gap.** |
| `POST /supplier-repayments` | Per OpenAPI §15.13.3: no If-Match listed | supplier_repayments.py:113 does not require If-Match. ✓ (consistent) |
| `POST /purchases/{id}/payments` | Per OpenAPI §15.12.9: required | purchases.py uses `check_if_match` — but `if_match` param is `str | None`. Need to verify it's enforced. |

---

## 9. Current Implementation Gaps

| Area | Current behavior | Intended behavior | Gap | Severity |
|---|---|---|---|---|
| Return creation | Inserts `purchase_returns` + lines + stock movements; **no `supplier_repayments` row** | DB-Design §5.4, API-Arch §15.12.10 step 5: "If paid: insert `supplier_repayments`" | No SREC credit row created for paid/partial-paid returns | **High** |
| SREC formula (service) | `srec = outstanding_AP − Σ received_amount` | DB-Design §8.4: `srec = Σ amount − Σ received_amount` | Returns 0 SREC for fully-paid purchase with return credit; blocks legitimate repayments | **High** |
| SREC formula (`_enrich_purchase`) | `srec = max(0, ap − srec_received)` (purchases.py:252) | Same bug — uses AP not Σ amount | SupplierReceivable display is wrong for return-credit scenarios | **Medium** |
| SREC formula (`add_payment`) | `max_payable = total − paid − srec_received` (purchases.py:1377) | Correct per DB-Design §8.4 | ✓ (no gap) | — |
| Return cancellation | Reverses stock + sets status; **no `supplier_repayments` reversal** | Should reverse SREC and cash if repayment received | No SREC/cash reversal on cancel | **High** |
| `delete_supplier_repayment` | **Does not exist** (no endpoint, no service, no repo) | Posted business data must not be hard-deleted | No way to remove or reverse a supplier repayment | **Medium** (no delete capability) |
| Purchase payment ceiling | App-layer `max_payable` (purchases.py:1377); DB trigger ignores returns | Should cap at `total − returns − payments` | DB trigger `fn_purchase_payment_allocation_bound` does not subtract returns; relies on app layer | **Medium** |
| Return If-Match | `parse_if_match_optional` (optional) | OpenAPI §15.12.10: required | If-Match not enforced on return creation | **Low** |
| Return cancel If-Match | `parse_if_match_optional` (optional) | OpenAPI §15.12.11: required | If-Match not enforced on return cancellation | **Low** |
| `purchase_returns` table | No `updated_at` column | Trigger `fn_bump_version_and_updated_at` expects one | `cancel_return` must use `session_replication_role = replica` workaround | **Medium** (schema defect) |
| Multiple `supplier_repayments` rows | Each `create_repayment` INSERTs a new row with `amount = outstanding` | One obligation row per purchase, updated `received_amount` | Duplicate rows with stale `amount` values | **High** (concurrency + data integrity) |
| `refundable_amount_snapshot` on return-created SREC | N/A (no SREC row created) | Should store the SREC credit amount at creation (M6 §7) | Not implemented | **Low** |

---

## 10. Frontend Contract

### 10.1 Current frontend state (`frontend/app/(protected)/purchases/[id]/page.tsx`)

- **Purchase detail page** displays: lines (read-only table), shipping, payments, returns (read-only table), supplier info, purchase info, payment summary (total/paid/outstanding/AP/supplier_receivable), notes.
- **No "Return" action button** exists for purchases. The Returns card is read-only ("Goods returned to supplier (read-only)").
- **No "Supplier Repayment" action** on the detail page.
- **No "Cancel" action** for purchases with payments/returns on the detail page.
- **Post button** exists (`postPurchase` at line 445) for draft→posted transition.
- API client calls `GET /purchases/{id}?include=lines,shipping,payments,returns` (line 172).
- Filters on list page include `lifecycle_status` (draft/posted/completed/partially_returned/returned/cancelled) and `payment_state` (unpaid/partial/paid).

### 10.2 V0 designs (`V0-design-zip-file/`)

| Zip | Contents | Return UI? |
|---|---|---|
| `purchasing-management.zip` | `app/page.tsx` only | **No** — static dashboard page, no purchase detail, no return action |
| `purchase-detail.zip` | `app/page.tsx`, `app/purchases/page.tsx` | **No** — mockup only, static HTML, no interactive return form |
| `returns-and-refunds-workspace.zip` | `app/page.tsx` only | **No** — single static page, no return creation dialog |

**V0 designs do NOT contain return UI.** They are static mockups with no interactive forms, dialogs, or action buttons for returns.

### 10.3 Phase E frontend requirements (from the gap analysis)

The frontend needs:

- **Return action button** on purchase detail (Phase E): enabled when `purchase.lifecycle_status ∈ ['posted','completed','partially_returned','returned']` and user has `purchase.return` capability.
- **Return dialog** with: line selection (checkbox per line), quantity input per line (max = `line.quantity − returned_qty`), reason field, computed total (Σ `quantity × unit_cost_snapshot`).
- **Return cancellation** action on each return row in the Returns table: button with confirmation dialog, `purchase.return` capability.
- **Supplier Repayment display:** show SREC/Supplier Receivable amount on the purchase detail (already present at line 862–863 as `purchase.supplier_receivable`).
- **Post-return payment awareness:** after a return, the payment button should reflect the reduced outstanding.
- **Loading/error/empty states** for the Returns table (empty state exists at line 765; error state partially at line 877+).

**V0 does NOT already contain the required return UI.** A new return dialog/page must be specified.

---

## 11. Required Test Matrix

### 11.1 Accounting

| # | Test case | Existing? | Notes |
|---|---|---|---|
| A1 | Unpaid purchase → return → verify AP reduced, no SREC, no `supplier_repayments` row | No | `test_purchases.py` has no return tests |
| A2 | Partially-paid purchase → return → verify AP+SREC split | No | Candidate rule; needs Decision 1 |
| A3 | Fully-paid purchase → return → verify SREC created, AP=0 | No | Needs Decision 1 |
| A4 | Return quantity exceeds purchased quantity → 409 | No (trigger exists but no test) | `fn_prl_quantity_bound` exists; test needed |
| A5 | Return on draft/cancelled purchase → 409 `lifecycle_state_invalid` | No | Service checks `_LIFECYCLE_RETURNABLE` |

### 11.2 Payments

| # | Test case | Existing? | Notes |
|---|---|---|---|
| P1 | Payment before return | Partial (test_purchases.py:721+) | Return tests missing |
| P2 | Payment after return → capped at remaining AP | No | `max_payable` formula; not tested post-return |
| P3 | Overpayment attempt after return → 409 `allocation_exceeds_payable` | No | DB trigger + app layer |

### 11.3 Supplier Repayments

| # | Test case | Existing? | Notes |
|---|---|---|---|
| R1 | SREC creation on paid return | No | Not implemented |
| R2 | Partial supplier repayment against return credit | Partial (`test_supplier_repayments.py:189+`) | Tests SREC from AP; not from return |
| R3 | Full supplier repayment against return credit | No | |
| R4 | Repayment after multiple returns | No | |
| R5 | Repayment amount > SREC → 409 `repayment_exceeds_srec` | ✓ | `test_supplier_repayments.py:148` |

### 11.4 Cancellation

| # | Test case | Existing? | Notes |
|---|---|---|---|
| C1 | Cancel return before supplier repayment → stock restored, AP unchanged | No | `cancel_return` exists but no SREC to reverse |
| C2 | Cancel return after supplier repayment received → SREC reversed, cash reversed | No | **Undefined** — needs Decision 3 |
| C3 | Cancel return → stock restoration (positive qty movement) | No | `cancel_return` inserts `purchase_return_reversal` |
| C4 | Cancel unpaid purchase → AP restoration | Partial (`test_purchases.py:935+`) | Purchase cancel, not return cancel |

### 11.5 Concurrency

| # | Test case | Existing? | Notes |
|---|---|---|---|
| CON1 | Concurrent returns on same purchase → one rejected by DB trigger | No | Needs concurrent test setup |
| CON2 | Return + payment race → no over-allocation | No | `FOR UPDATE` lock should serialize |
| CON3 | Return + supplier repayment race → no duplicate SREC | No | No SREC creation yet |
| CON4 | Duplicate idempotency key | Partial (`test_purchases.py`) | For returns: `test_purchases.py` has idempotency for create/payment/cancel but not returns |
| CON5 | Stale ETag → 412/409 | Partial | `check_if_match` exists but may be optional |

### 11.6 Permissions

| # | Test case | Existing? | Notes |
|---|---|---|---|
| PER1 | Owner creates return | No | No return tests at all |
| PER2 | Staff creates return → 403 (no `purchase.return`) | No | |
| PER3 | Staff views returns → 200 | Partial | `test_purchases.py` has general auth tests |
| PER4 | Staff creates supplier repayment → 403 | ✓ | `test_supplier_repayments.py:285` |

### 11.7 Frontend/API Contract

| # | Test case | Existing? | Notes |
|---|---|---|---|
| F1 | Return creation success → 201 + `PurchaseReturn` | No | |
| F2 | Return validation failure → 409 with error code | No | |
| F3 | Stale ETag → 412/409 | No | |
| F4 | Server error → 500 with error envelope | No | |

### Summary

**Existing tests covering the area:** ~5 tests in `test_supplier_repayments.py` (SREC from AP, repayment_exceeds_srec, idempotency, auth), ~12 tests in `test_purchases.py` (mostly for purchase lifecycle: create, post, payment, cancel — NOT returns).

**Tests that need to be added:** ~20 (all return-specific + SREC-from-return + cancellation-of-return + concurrency).

**Tests impossible/undefined until business rule decided:** 3 (partial-payment SREC split, SREC reversal on return cancel, supplier repayment against return credit).

---

## 12. Remaining Business Decisions

> **Owner decisions ratified (post-contract, recorded here as binding):**
> 1. **D1 — Adopt Option A.** The partial-payment split rule is now a **ratified business rule**, not a candidate inference:
>    - `AP_reduction = min(return_value, remaining_AP_before)`
>    - `SREC_created = max(0, return_value − remaining_AP_before)`
>    - When `SREC_created > 0`, create a `supplier_repayments` row with `reason = 'purchase_return_credit'`, `amount = SREC_created`, `received_amount = 0`.
>    - **Status:** ratified by Owner. The candidate rule was previously **an inference** — no authoritative document (schema.sql, V1.9-Accounting-Event-Matrix.md, Database-Design-V1.0.md, Business-Rules.md, Backend-Architecture-V1.0.md, API-Architecture-V1.0.md, openapi.yaml) explicitly states the partial-payment split. The Owner ratified it as a new business rule. M6 is **not** in the §1 authority ladder and its endorsement does not retroactively make this "documented."
> 2. **D2 — Option A.** Cancellation of a purchase return is refused when the return-created `supplier_repayments` row has `received_amount > 0`. **Cancellation before any supplier cash has been received remains allowed** (the rule is specifically *cannot cancel after supplier cash has been received*, not *cannot cancel whenever an SREC row exists*)."
>
> **Note on Database-Design-V1.0.md:** This file is listed in §1.3 as authority rank 3 but **does not exist** in the repository at the time of this writing. All §13 line-number citations to it (§2 line 1170/1171, §5.4 line 1037, §6.4 line 1170, §7 table rows) are **unverifiable against the named file** — they reference line numbers in a document that has not been created. The substance these citations point at is corroborated in V1.9-Accounting-Event-Matrix.md (INV-04 line 358–359, INV-07 line 364) and schema.sql CHECK constraints, so the underlying invariant holds; only the `Database-Design-V1.0.md` attribution is broken. This must be resolved by creating the file or removing the citation before final Go/No-Go.

### Decision 1: Partial-payment purchase return — AP reduction + SREC split?
**[RATIFIED — Owner chose Option A]**

- **Why it matters:** When a purchase is partially paid and a return occurs, the return value may exceed the remaining AP. The rule (now ratified): `AP_reduction = min(return_value, remaining_AP)`, `SREC_created = max(0, return_value − remaining_AP)`. No authoritative doc explicitly stated this; it is a new business rule ratified by the Owner.
- **Options (for record; Owner chose A):**
  - **A.** ~~Adopt the candidate rule~~ → **Adopted (ratified).** Partial-payment return creates both AP reduction AND a `supplier_repayments` row for the SREC portion. `reason = 'purchase_return_credit'`.
  - **B.** Binary only — not chosen.
  - **C.** Conservative — not chosen.
- **Documented evidence status:** DB-Design §5.4 line 1037 says "If purchase paid: Supplier Receivable increase." API-Arch §15.12.10 says "If paid: insert supplier_repayments." Both are binary paid/unpaid, silent on partial. M6 explicitly recommends Option A as the only economically-consistent choice. The Owner ratified Option A as a new business rule; it is **not** retroactively "documented" by prior docs.
- **Decision:** **Option A** — ratified by Owner. The accounting balances, the DB permits it, and it generalizes both Event 12 and Event 15.
- **Change to Final Accounting Rules §2:** The "If purchase partially paid" bullet (§2 lines 60–65) was previously labelled a candidate rule pending owner sign-off. It is now a **ratified business rule**. The accounting journal and `supplier_repayments` creation for partial-payment returns are live requirements.

### Decision 2: SREC service formula

- **Why it matters:** `SupplierRepaymentService` computes SREC as `outstanding_AP − Σ received`, but the correct formula (DB-Design §8.4) is `Σ amount − Σ received`. Until fixed, post-return SREC is invisible to the repayment service.
- **Options:**
  - **A.** Fix the service formula to `Σ amount − Σ received`.
  - **B.** Leave as-is (breaks return-generated SREC).
- **Recommended default:** **Option A** — this is a **technical fix**, not a business decision. The DB-Design formula is authoritative.

### Decision 3: Return cancellation + supplier repayment interaction
**[RATIFIED — Owner chose Option A]****

- **Why it matters:** If a return creates a `supplier_repayments` row (Decision 1 = Option A) and the supplier later repays cash (`received_amount > 0`), cancelling that return creates a conflict: the return credit obligation is being reversed, but cash was already received.
- **Options:**
  - **A.** Block cancellation if `received_amount > 0` on the return-created SREC row (return cannot be cancelled after cash received). → **Chosen & ratified.**
  - **B.** Allow cancellation but reverse the SREC and create a compensating `cash_out` (reverse the cash movement). → Not chosen.
  - **C.** Allow cancellation; zero-out the SREC row's `amount` (mark as void) — preserves cash Movements for audit. → Not chosen.
  - **D.** Don't allow return cancellation at all once any SREC interaction exists. → Not chosen.
- **Owner-specified scope:** The ratified rule is **specifically *cannot cancel after supplier cash has been received***, not *cannot cancel whenever an SREC row exists*. Cancellation of a return that created an SREC row with `received_amount = 0` remains **allowed**; only the `received_amount > 0` case is blocked.
- **Current documented evidence:** "Posted business data must not be hard-deleted" (M4 design principle). No `delete_supplier_repayment` exists. `cancel_return` currently does not touch `supplier_repayments`. V1.9 §4 INV-05 ("single cancellation") and INV-04 ("repayment ≤ SRec") are the nearest invariants and support blocking, not reversal.
- **Decision:** **Option A** — ratified by Owner. Block cancellation when the return-created SREC row has `received_amount > 0`. No cash or SREC reversal on cancellation; the blocked return keeps its SREC row intact.

### Decision 4: Return cancellation — should it update parent purchase lifecycle?

- **Why it matters:** If a purchase is in `returned` status and a return is cancelled, the purchase should transition back to `partially_returned` (or `completed` if all returns cancelled). The current `cancel_return` does NOT update the parent purchase lifecycle.
- **Current evidence:** `create_return` sets parent lifecycle (purchases.py:1823–1837); `cancel_return` does not reverse it.
- **Recommended default:** Yes — `cancel_return` should recompute and set parent lifecycle status (`returned → partially_returned` if not all lines returned, `partially_returned → completed`/`posted` if all returns cancelled). This is a **technical gap**, not a business decision.

### Decision 5: If-Match enforcement on return endpoints

- **Why it matters:** OpenAPI §15.12.10/11 say If-Match is required, but the implementation uses `parse_if_match_optional`.
- **Options:** **A.** Enforce If-Match (change to `parse_if_match`). **B.** Leave optional.
- **Recommended default:** **Option A** — match the spec. **Technical fix, not business.**

---

## 13. Remaining Business Decisions

| # | Decision | Type | Status |
|---|---|---|---|
| 1 | Partial-payment return creates SREC via `supplier_repayments` row? | Business | **RATIFIED — Option A** (split rule adopted; `reason='purchase_return_credit'` when SREC>0) |
| 2 | SREC service formula must be corrected | Technical | **GAP** → Fix to `Σ amount − Σ received` |
| 3 | Return cancel after supplier cash repayment | Business | **RATIFIED — Option A** (block when `received_amount > 0`; pre-cash cancel allowed) |
| 4 | `cancel_return` should update parent purchase lifecycle | Technical | **GAP** → Fix |
| 5 | If-Match required on return create/cancel | Spec-coupled (code/spec mismatch, no spec change) | **GAP** → Enforce on implementation to match openapi.yaml §15.12.10/11 (already required in openapi.yaml) |

---

## 14. Implementation sequence

> **Prerequisites — owner-ratified business rules (§12):** Decision 1 (Option A: partial-payment split, `reason='purchase_return_credit'`) and Decision 3 (Option A: block return cancel when SREC `received_amount > 0`).

1. **Fix `create_return`** (purchases.py:1677–1872) — insert `supplier_repayments` row for SREC portion when `SREC_created > 0`. `reason = 'purchase_return_credit'`, `amount = SREC_created`, `received_amount = 0`, `refundable_amount_snapshot = SREC_created`.
2. **Fix SREC formula** in `SupplierRepaymentService.create_repayment` (supplier_repayments.py:173–177) and `_enrich_purchase` (purchases.py:248–252) → `Σ amount − Σ received`.
3. **Fix `add_payment` max_payable** (purchases.py:1377) — confirms `max_payable = total − paid − Σ received` already correct (excludes un-received SREC credit). No change. ✓
4. **Fix `cancel_return`** (purchases.py:1929–2046) — add:
   - Block when return-created SREC row has `received_amount > 0` (Decision 3 / Owner-ratified).
   - Parent purchase lifecycle recomputation (`returned → partially_returned` / `→ completed` / `→ posted`).
   - (No SREC/cash reversal — blocked returns keep their rows.)
5. **Fix If-Match** on return endpoints (`purchase_returns.py:125,198`) → `parse_if_match` (already required in openapi.yaml; only code-spec mismatch, no spec change). Add concurrency tests.
6. **Add `find_supplier_repayment_for_return`** repo method.
7. **Fix `trg_purchase_returns_bump_version`** schema defect.
8. **Frontend return dialog** + return cancellation button.
9. **Tests** — ~20 new (see §11); 3 Decision-3-dependent tests now unlocked by ratified rule.

---

## 15. Final Go/No-Go

**GO — Business decisions ratified.**

Owner-ratified decisions:
- **Decision 1 (§12):** Option A adopted — partial-payment return splits AP reduction + SREC creation. `reason = 'purchase_return_credit'` when SREC > 0.
- **Decision 3 (§12):** Option A adopted — return cancellation blocked when return-created SREC row has `received_amount > 0`. Pre-cash cancellation allowed.

**Status:** Contract updated. Two business decisions resolved by Owner. Three technical gaps remain (SREC formula, `cancel_return` lifecycle, If-Match enforcement) but are documented and sequenced in §14.

**Remaining technical contradictions requiring resolution before implementation (not business decisions):**

1. **`Database-Design-V1.0.md` does not exist.** Listed in §1.3 authority rank 3; cited in §6.4, §7, and §12. All line-number references to it (§2 line 1170/1171, §5.4 line 1037, §6.4 line 1170) are **unverifiable**. The invariants they cite are corroborated in V1.9-Accounting-Event-Matrix.md and schema.sql — but this file must be created or the citations removed before final clearance.

2. **`trg_purchase_returns_bump_version` schema defect (schema.sql:2188–2190).** Triggers `fn_bump_version_and_updated_at()` which sets `NEW.updated_at := NOW()`, but `purchase_returns` (schema.sql:833–851) has **no `updated_at` column**. Current workaround (`session_replication_role = 'replica'` in purchases.py:714) is fragile and must be fixed — either add the column or change the trigger function.

3. **SREC formula bug in `SupplierRepaymentService` (supplier_repayments.py:173–177).** Computes `srec = max(0, outstanding_AP − Σ received)`, but correct formula (V1.9 §8.4 / DB-Design §8.4) is `Σ amount − Σ received`. Must be fixed for return-generated SREC to be visible to the repayment service.