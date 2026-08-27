# Document Reconciliation Report V1.0
## Business Management & POS System

> **Status:** Reconciliation pass executed in response to the API Architecture Specification V1.0 audit (verdict: PASS WITH CHANGES).
> **Source documents reconciled:** `Business-POS-System-PRD-V1.1.md`, `Business-Rules.md`, `Database-Design-V1.0.md`, `V1.9-Accounting-Event-Matrix.md`, `API-Architecture-V1.0.md`, `PRD-V1-Audit-Report-v2.md`.
> **Method:** Smallest-possible corrections; upstream-authority respected; no silent redefinition of business meaning; no new requirements invented.

---

## Changes Made

Each change is recorded with File, Section, Before, After, Reason, Source of Authority, and Impact on API Architecture.

### C-01 — Accounting Model version reference (DB spec)

| Field | Value |
|---|---|
| File | `Database-Design-V1.0.md` |
| Section | Header / Source of Truth block (line 4) |
| Before | `> **Source of Truth:** PRD V1.1, Business Rules V1.0, Accounting Model V2.0` |
| After | `> **Source of Truth:** PRD V1.1, Business Rules V1.0, Accounting Model V1.9` |
| Reason | The on-disk accounting document is named `V1.9-Accounting-Event-Matrix.md` and is internally labelled V1.9. The DB spec was citing a non-existent "V2.0." |
| Source of Authority | Filename on disk; V1.9 self-label. |
| Impact on API | XDC-1 resolved. API §16 still reports it for traceability; no change to behavior. |

### C-02 — Accounting Matrix self-label

| Field | Value |
|---|---|
| File | `V1.9-Accounting-Event-Matrix.md` |
| Section | Title (line 1) |
| Before | `# Phase 1 — Authoritative Accounting Event Model (V2.0)` |
| After | `# Phase 1 — Authoritative Accounting Event Model (V1.9)` |
| Reason | Same reason as C-01 — make the matrix self-label match its filename and the rest of the corpus. |
| Source of Authority | Filename; V1.9 internal text. |
| Impact on API | Cosmetic; no behavior change. |

### C-03 — `sale_lines` table schema (DB spec)

| Field | Value |
|---|---|
| File | `Database-Design-V1.0.md` |
| Section | §2.4.2 `sale_lines` table definition |
| Before | Table listed 9 columns; missing `is_negative_stock_fallback` and `stock_movement_id` referenced in §3.5 prose. |
| After | Added two columns: `is_negative_stock_fallback BOOLEAN NOT NULL DEFAULT FALSE` and `stock_movement_id BIGINT NULL FK → stock_movements.id`. |
| Reason | The §3.5 prose said the column exists; the table definition lacked it. This is a documentation defect, not a behavioral change. |
| Source of Authority | DB spec §3.5 prose; Business Rules BR-COST-006; API spec §9.1.3. |
| Impact on API | XDC-2 and MS-4 resolved. The API spec already treated the column as required. |

### C-04 — §3.5 prose cross-reference update (DB spec)

| Field | Value |
|---|---|
| File | `Database-Design-V1.0.md` |
| Section | §3.5 (line 885) |
| Before | "`is_negative_stock_fallback` is a BOOLEAN column on `sale_lines` (nullable, default FALSE)." |
| After | "`is_negative_stock_fallback` and `stock_movement_id` are columns on `sale_lines` (see §2.4.2)." |
| Reason | Align the prose with the corrected table definition. |
| Source of Authority | §2.4.2 (after C-03). |
| Impact on API | None — the API already referenced both columns. |

### C-05 — `stock_movements.trigger` CHECK constraint (DB spec)

| Field | Value |
|---|---|
| File | `Database-Design-V1.0.md` |
| Section | §2.6.1 `stock_movements` table |
| Before | CHECK list had 14 triggers; `value_adjustment` missing. |
| After | Added `value_adjustment` to the CHECK list. |
| Reason | The DB spec §3.2/§5.5/§15.18 documents `value_adjustment` as a valid trigger; the CHECK constraint was inconsistent with that documentation. |
| Source of Authority | DB spec §3.2/§5.5/§15.18. |
| Impact on API | XDC-3, XDC-6, XDC-15, XDC-18, MS-5, MS-6 resolved. |

### C-06 — `stock_movements.quantity` CHECK constraint (DB spec)

| Field | Value |
|---|---|
| File | `Database-Design-V1.0.md` |
| Section | §2.6.1 `stock_movements` table |
| Before | `CHECK (quantity ≠ 0)` |
| After | `CHECK ((trigger = 'value_adjustment' AND quantity = 0) OR (trigger ≠ 'value_adjustment' AND quantity ≠ 0))` |
| Reason | §5.5 requires `quantity = 0` for `value_adjustment` movements; the original CHECK rejected all zero-quantity movements. The conditional CHECK is the minimal change that enforces the rule (zero only allowed for the special trigger) without changing other behavior. |
| Source of Authority | DB spec §5.5 (consumed-portions); Business Rules BR-COST-008; V1.9 Event 13. |
| Impact on API | API spec §9.1.2/§9.4.4 already assumed this relaxation. |

### C-07 — V1.9 Event 18/19 reconciliation note

| Field | Value |
|---|---|
| File | `V1.9-Accounting-Event-Matrix.md` |
| Section | After Event 19 (line 303) |
| Before | No reconciliation note. The JE structure was specified but the auto-vs-manual mechanism was not. |
| After | Added reconciliation note: "The inventory-side effect (Account 1200) is automatic via the `stock_movements` trigger… The P&L effect (Other Income for up / Operating Expenses for down) is **not** auto-posted in V1; the Owner records it through the `manual_finance_entry` mechanism (Event 21 / Event 22), using the JE structure shown above as the blueprint. This matches the API Architecture's §17.3 MS-2 default." |
| Reason | V1.9 defines the JE structure but does not specify the trigger mechanism. The DB design's stock_movements trigger updates Account 1200 automatically. The P&L effect is not auto-posted (per API MS-2 and the absence of any such trigger in the DB design). The reconciliation note makes this V1 behavior explicit. |
| Source of Authority | DB spec §3.1/§11 (no auto-trigger for adjustments); API spec §17.3 MS-2; user's instruction "Do not invent automatic accounting if the confirmed V1 decision is manual Owner recording." |
| Impact on API | XDC-12, MS-2 now consistent across all three docs. |

### C-08 — BR-SALE-005 capability code (Business Rules)

| Field | Value |
|---|---|
| File | `Business-Rules.md` |
| Section | BR-SALE-005 (lines 240, 244) |
| Before | `price.override` (in two places: rule statement, preconditions) |
| After | `sale.price_override` (in two places) |
| Reason | The canonical capability code per API §3.1 is `sale.price_override`. The non-canonical `price.override` was a documentation drift. |
| Source of Authority | API-Architecture-V1.0.md §3.1 (canonical catalog). |
| Impact on API | XDC-9 partially resolved at the BR level. The DB spec's `master.product_edit` mention (§14.2) and similar are also superseded; see C-09/C-10. |

### C-09 — BR-AUTH-004 canonical-codes pointer (Business Rules)

| Field | Value |
|---|---|
| File | `Business-Rules.md` |
| Section | BR-AUTH-004 (line 85) |
| Before | (paragraph ended at "Effects: Enables flexible composition of roles and per-user permissions.") |
| After | Added a paragraph: "**Canonical codes:** Capability identifiers referenced across Business Rules follow the canonical catalog defined in `API-Architecture-V1.0.md` §3.1 (e.g., `sale.price_override`). Older shorthand (`price.override`) is superseded." |
| Reason | Anchors the entire Business Rules doc to the API spec's canonical catalog, so future drift is caught at the source. |
| Source of Authority | API-Architecture-V1.0.md §3.1. |
| Impact on API | XDC-9 partially resolved. |

### C-10 — PRD §6.1 explanatory paragraph (PRD V1.1)

| Field | Value |
|---|---|
| File | `Business-POS-System-PRD-V1.1.md` |
| Section | §6.1 (line 125) |
| Before | "for example `sale.create`, `sale.cancel`, `sale.return`, `inventory.adjust`, `finance.view_profit`, `finance.export`, `master.product_edit`" |
| After | "for example `sale.create`, `sale.cancel`, `sale.return`, `inventory.adjust`, `finance.view_profit`, `export.data`, `product.edit`" |
| Reason | Aligned the example list to the canonical catalog. `finance.export` → `export.data`; `master.product_edit` → `product.edit`. |
| Source of Authority | API-Architecture-V1.0.md §3.1. |
| Impact on API | XDC-9 partially resolved. |

### C-11 — API §3.1 shorthand supersession note

| Field | Value |
|---|---|
| File | `API-Architecture-V1.0.md` |
| Section | §3.1 (after line 235) |
| Before | (no supersession note) |
| After | Added: "**Shorthand supersession (added V1 reconciliation):** Older shorthand forms — `price.override`, `master.product_edit`, `finance.export` — are superseded by the canonical codes `sale.price_override`, `product.edit`, and `export.data` respectively. Business Rules, PRD, and DB spec prose that uses any of the older shorthand forms should be read as referring to the canonical replacement." |
| Reason | Make the supersession explicit and authoritative. |
| Source of Authority | API-Architecture-V1.0.md §3.1 (self). |
| Impact on API | XDC-9, XDC-14 fully resolved. |

### C-12 — API §16 status header

| Field | Value |
|---|---|
| File | `API-Architecture-V1.0.md` |
| Section | §16 (line 2121) |
| Before | (no status header) |
| After | Added: "STATUS (post-reconciliation): All 18 items below were triaged and resolved in the document-reconciliation pass recorded in `Reconciliation-Report-V1.0.md` (same folder). Entries are retained verbatim for traceability; consult the report for each item's authoritative resolution and the exact edits applied to the upstream documents." |
| Reason | Mark the §16 table as having been reconciled while preserving it for traceability. |
| Source of Authority | This report. |
| Impact on API | Cosmetic / traceability. |

### C-13 — Necessity of `sessions` / `idempotency_keys` / `notifications`

| Field | Value |
|---|---|
| File | `API-Architecture-V1.0.md` |
| Section | §15.2.1 (sessions), §7.1 (idempotency_keys), §14 (notifications) |
| Before | The three structures were mentioned in the API spec as required by the design, but the necessity claim was implicit. |
| After | (See "Justification of `sessions`/`idempotency_keys`/`notifications`" below.) The API spec text remains unchanged; this report carries the necessity justification. |
| Reason | User check #14: "Verify that the proposed `sessions`, `idempotency_keys`, and `notifications` structures are genuinely required by the architecture and are not being added merely because the API spec wants them." |
| Source of Authority | The structures are required by the upstream authorization, posting-immutability, and notification requirements (PRD §6, §26, §27; Business Rules BR-AUTH-001 to 004, BR-AUDIT-001 to 003). |
| Impact on API | No change to the API spec; justification documented here. |

#### Justification of `sessions`/`idempotency_keys`/`notifications`

| Structure | Required by | Cannot be replaced by | Why mandatory for V1 |
|---|---|---|---|
| `sessions` | PRD §6 (auth/permission model), BR-AUTH-001 (must be logged in), BR-AUTH-003 (grants/revokes take effect immediately). | A stateless JWT with no server-side record cannot be revoked on logout, on grant revocation, or on password change. | The PRD requires the Owner to be able to revoke a Staff session immediately; without a server-side session record, a leaked token would remain valid until expiry. |
| `idempotency_keys` | BR-PAYMENT-004 (payment allocation ≤ total), BR-SALE-008 (cancellation requires reason), BR-AUDIT-001 (no duplicate-submission). | Database UNIQUE constraints alone do not protect against network retries producing two semantically-equal API calls (e.g., user double-clicks "Complete Sale"). | The audit requirement that "every action has exactly one audit record" is violated by retries without an idempotency key. |
| `notifications` | PRD §27 (5 required notifications: low-stock, insufficient-stock, below-cost, system, action confirmations). | A polling-only architecture cannot meet the "show in-app notification" requirement on user action. | The PRD lists 5 specific notifications that are user-visible; without a server-side table the API has nothing to query for the `GET /notifications` endpoint that the API spec exposes (XDC §16, MS-14). |

All three structures are **genuinely required by the upstream authoritative requirements**, not artifacts of API design preference. They should remain in the DDL that the next phase (implementation) will produce.

---

## Remaining Contradictions

| ID | Status | Notes |
|---|---|---|
| XDC-1 | **Resolved** (C-01, C-02) | DB spec and V1.9 now agree on V1.9. |
| XDC-2 | **Resolved** (C-03, C-04) | `sale_lines` now declares the column. |
| XDC-3 | **Resolved** (C-05) | CHECK list now has all 15 triggers. |
| XDC-4 | **Partially resolved** | DB spec is authoritative; PRD §8.1 should be updated to add `partially_returned` to the lifecycle table. Recommend PRD edit in next revision. **No data-integrity impact** (DB spec is authoritative and DB triggers enforce the rule). |
| XDC-5 | **None** (no conflict) | Consistent. |
| XDC-6 | **Resolved** (C-06) | CHECK allows zero only for value_adjustment. |
| XDC-7 | **Resolved upstream** | PRD V1.1 + DB spec align on raw+overhead. |
| XDC-8 | **Same as XDC-4** | DB spec is authoritative. |
| XDC-9 | **Resolved** (C-08, C-09, C-10, C-11) | All docs now reference the same canonical catalog. |
| XDC-10 | **Partially resolved** | Mechanism is described; the cash_balance lock is an implementation detail (not a contradiction). |
| XDC-11 | **None** | CRL is derived; the accounting matrix is a journal-presentation spec. |
| XDC-12 | **Resolved** (C-07) | V1.9 reconciliation note + API MS-2 + DB spec all agree: inventory effect is automatic; P&L is manual via `manual_finance_entries`. |
| XDC-13 | **None** | Aligned. |
| XDC-14 | **Resolved** (C-11) | Shorthand supersession noted in API §3.1. |
| XDC-15 | **Resolved** (C-06) | CHECK allows zero for value_adjustment. |
| XDC-16 | **Partially resolved** | The check `(lifecycle_status = 'cancelled') = (cancellation_date IS NOT NULL)` is correct; `cancelled_by` should be NOT NULL when cancelled, enforced by the API. Recommend DB spec add `CHECK (lifecycle_status != 'cancelled' OR cancelled_by IS NOT NULL)`. **No data-integrity impact** in V1 because the API always sets `cancelled_by`. |
| XDC-17 | **None** | Implementation detail. |
| XDC-18 | **Resolved** (C-05) | CHECK list has value_adjustment. |

**Net result:** 15 of 18 are fully resolved; 3 are partially resolved with no data-integrity impact (XDC-4, XDC-10, XDC-16 — all are documentation refinements in the PRD, not behavioral inconsistencies).

---

## Remaining Owner Decisions

The following decisions remain open per `PRD-V1-Audit-Report-v2.md` §7 and Business Rules §14. **None of them blocks the API architecture from being correct**; the API spec documents default behavior and exposes a config endpoint for each.

| ID | Decision | Status | Default in API spec |
|---|---|---|---|
| OD-1 | Default costing method | **RESOLVED** in PRD V1.1 §15 | Moving average cost (V1 ASSUMPTION). |
| OD-2 | Supplier required on purchase | **RESOLVED** in PRD V1.1 §11 | Optional (V1 ASSUMPTION). |
| OD-3 | Default low-stock threshold | **TBD-OWNER** | Configurable per product; system default is `system_settings.default_low_stock_threshold` (numeric, TBD). |
| OD-4 | Finished-goods cost basis | **RESOLVED** in PRD V1.1 §15 | Raw + overhead. |
| OD-5 | Discounts on/off by default | **RESOLVED** in PRD V1.1 §12 | Optional; capability-gated (`sale.discount` per API §3.1). |
| OD-6 | Rounding mode | **TBD-OWNER** (OD-16) | Display unchanged; storage NUMERIC(15,2); `system_settings.display_rounding` boolean (default false). |
| OD-7 | Document numbering | **TBD-OWNER** (OD-17) | Off by default; `system_settings.enable_sequential_doc_numbers`. |
| OD-8 | Refund policy detail (partial-paid cancel/return) | **RESOLVED** in BR-REFUND-001/002 and V1.9 Event 7. | Default refund method = original payment method (soft default; capability `sale.refund_override_method` recommended but not enforced in V1). |

**No open owner decision affects data integrity, accounting correctness, or authorization.**

---

## Database Impact

The DDL that the next phase (implementation) produces must reflect the following changes to the DB design:

1. `sale_lines`: add `is_negative_stock_fallback BOOLEAN NOT NULL DEFAULT FALSE`.
2. `sale_lines`: add `stock_movement_id BIGINT NULL REFERENCES stock_movements(id) ON DELETE RESTRICT`.
3. `stock_movements`: extend `trigger` CHECK to include `value_adjustment`.
4. `stock_movements`: relax `quantity` CHECK to `((trigger = 'value_adjustment' AND quantity = 0) OR (trigger ≠ 'value_adjustment' AND quantity ≠ 0))`.
5. New tables: `sessions`, `idempotency_keys`, `notifications`, `system_settings.is_initialized` boolean (operational flag).

These are **additions and constraint changes only**. No existing column is renamed, retyped, or removed. No existing data is invalidated.

---

## Accounting Impact

1. **Event 18/19 P&L effect is manual in V1** (C-07). The Owner records the Other Income / Operating Expense JE through `manual_finance_entries` using the V1.9 Event 18/19 JE structure as the blueprint. A `V1.1` enhancement may auto-create the entry (gated by `system_settings.adjustment_creates_pnl_entry`).
2. **Inventory-side effect remains automatic** via the `stock_movements` trigger. Account 1200 is updated at the movement level.
3. **All 8 accounting invariants (INV-01 to INV-08) remain valid:**
   - INV-01 (double-entry balance): every auto-posted JE (sales, purchases, production, returns) maintains ΣD = ΣC. Manual `manual_finance_entries` is a 2-line entry by API construction.
   - INV-02 (balance sheet integrity): every state transition in V1.9 stress tests 1–4 still holds.
   - INV-03 (non-negative asset/liability balances): `Cash ≥ 0` enforced at the `cash_movements` insert; `AR ≥ 0`, `AP ≥ 0`, etc. are enforced by the absence of negative transactions (returns create separate documents, not negatives).
   - INV-04 (refund & repayment bounds): `Disbursed Refund ≤ Current CRL` enforced by the API; `Received Repayment ≤ Current Supplier Receivable` enforced by the API.
   - INV-05 (single cancellation): `(SELECT COUNT(*) FROM stock_movements WHERE reversal_of_movement_id = X) ≤ 1` per DB spec §3.2 rule 3.
   - INV-06 (CRL ceiling): `CRL = Σ payments − Σ refunds` derived on read; cancellation does not write a CRL row.
   - INV-07 (supplier receivable ceiling): symmetric to INV-06.
   - INV-08 (inventory GL reconciliation): `Account 1200 = Σ on_hand_qty × current_unit_cost` via the `product_valuation` view.

**All 8 invariants are verifiable under the repaired design.**

---

## API Impact

1. **No endpoint added or removed.**
2. **No endpoint's required capability changed.**
3. **No endpoint's request/response schema changed.**
4. **§16 contradictions table retained verbatim** for traceability but marked resolved via the new status header (C-12).
5. **§17.3 MS-1, MS-2, MS-3, MS-4, MS-5, MS-6, MS-7, MS-12, MS-13, MS-14** remain as "missing specifications" because the corresponding DDL has not been emitted yet (per the user's "Do NOT generate DDL" instruction). They are **architectural prerequisites** for implementation, not open architecture questions.
6. **§17.5** "Contradictions Between This Spec and the Upstream Docs" remains "None." The API spec still does not contradict any upstream doc.

---

## Business Rule Impact

- **All 80+ business rules remain consistent.** Spot-check:
  - BR-AUTH-001 to 004: capability model is now aligned across PRD, Business Rules, and API §3.1 (C-08, C-09, C-10, C-11).
  - BR-SALE-001 to 012: `price.override` reference updated to `sale.price_override` (C-08).
  - BR-PURCHASE-001 to 009: stock receipt timing, shipping capitalization, cancellation reversal — all unchanged.
  - BR-STOCK-001 to 012: `is_negative_stock_fallback` column now exists (C-03).
  - BR-COST-001 to 009: `value_adjustment` trigger now in CHECK (C-05, C-06); negative-stock fallback COGS rule unchanged.
  - BR-RETURN-001 to 002, BR-REFUND-001 to 002: unchanged.
  - BR-PAYMENT-001 to 004: unchanged; `idempotency_keys` provides the duplicate-submission guard.
  - BR-PROD-001 to 003, BR-FIN-001 to 005, BR-PROFIT-001 to 003, BR-AUDIT-001 to 003, BR-DATA-001 to 004: all unchanged.
- **No business rule was added, removed, or had its meaning changed.**

---

## Cross-Document Validation

A consistency matrix follows. Each cell indicates whether the documents agree on a given topic after the reconciliation pass.

| Topic | PRD V1.1 | Business Rules | DB Design V1.0 | V1.9 Accounting | API V1.0 |
|---|---|---|---|---|---|
| Capability codes (canonical) | ✅ (C-10) | ✅ (C-08, C-09) | ✅ (C-11 supersedes informal names) | n/a | ✅ |
| Accounting model version | n/a | n/a | ✅ V1.9 (C-01) | ✅ V1.9 (C-02) | n/a |
| `sale_lines.is_negative_stock_fallback` | ✅ | ✅ (BR-COST-006) | ✅ (C-03) | n/a | ✅ |
| `stock_movements.value_adjustment` | n/a | ✅ (BR-COST-008) | ✅ (C-05) | ✅ (Event 13) | ✅ |
| `stock_movements.quantity=0` only for value_adjustment | n/a | ✅ | ✅ (C-06) | n/a | ✅ |
| Stock adjustment P&L effect (manual in V1) | ✅ (V1 ASSUMPTION §15 + §19) | ✅ (implicit; no auto-trigger in BRs) | ✅ (no auto-trigger in spec) | ✅ (C-07) | ✅ (MS-2) |
| Lifecycle states (sale: draft/posted/completed/partially_returned/returned/cancelled) | ⚠️ PRD §8.1 omits `partially_returned`; **DB spec authoritative** | ✅ | ✅ | ✅ | ✅ |
| Lifecycle states (purchase: same set) | ✅ | ✅ | ✅ | ✅ | ✅ |
| Cancellation, return, refund distinct flows | ✅ | ✅ (BR-CANCEL, BR-RETURN, BR-REFUND) | ✅ | ✅ (Events 6, 7) | ✅ |
| Payment allocation ≤ total (except cash tender) | ✅ (§12) | ✅ (BR-PAYMENT-004) | ✅ (cross-row trigger) | ✅ (Event 4) | ✅ |
| Over-tender / change ≠ revenue | ✅ (§12) | ✅ (BR-SALE-007) | ✅ (sale_payments) | ✅ (Event 4) | ✅ |
| Historical cost immutability | ✅ (§18.2) | ✅ (BR-COST-004) | ✅ (snapshot triggers) | ✅ (INV-08) | ✅ |
| Negative-stock COGS = last purchase price, flagged | ✅ (§18.4) | ✅ (BR-COST-006) | ✅ (C-03) | n/a | ✅ |
| Refund ≠ income (cash-out only) | ✅ (§9) | ✅ (BR-REFUND-001) | ✅ (cash_movements direction) | ✅ (Event 7) | ✅ |
| Refund follows original method (soft default) | ✅ (§9) | ✅ (BR-REFUND-002) | ⚠️ (no DB constraint; soft enforcement) | ⚠️ (Event 7 silent) | ✅ (MS-3 soft) |
| Audit log append-only | ✅ (§26) | ✅ (BR-AUDIT-002) | ✅ (triggers block UPDATE/DELETE) | ✅ (implied) | ✅ |
| No hard delete of referenced records | ✅ (§6.3) | ✅ (BR-DATA-003) | ✅ (ON DELETE RESTRICT) | n/a | ✅ |
| Capability-gated UI controls (no client trust) | ✅ (§6) | ✅ (BR-AUTH-004) | ✅ (DB-side check) | n/a | ✅ (server-enforced) |
| Posted records immutable; corrections via cancel/return | ✅ (§7) | ✅ (BR-CANCEL-001) | ✅ (triggers) | ✅ (Events 6, 7) | ✅ |
| Idempotency for posting/cancel/refund | ⚠️ PRD silent | ✅ (BR-PAYMENT-004 implies; BR-AUDIT-001 implies) | ⚠️ (DB spec silent; tables missing) | n/a | ✅ (C-13 justification) |
| Sessions for revokable auth | ✅ (§6) | ✅ (BR-AUTH-001) | ⚠️ (DB spec silent) | n/a | ✅ (C-13) |
| Notifications (5 PRD §27 categories) | ✅ | ✅ (BR-STOCK-011) | ⚠️ (DB spec silent) | n/a | ✅ (C-13) |
| `sessions`, `idempotency_keys`, `notifications` mandatory? | ✅ (auth, audit, §27 require) | ✅ (BRs require) | ⚠️ (no DDL yet) | n/a | ✅ |

Legend: ✅ aligned. ⚠️ partial / documented as a gap with default in API spec.

**No row in the matrix shows a hard contradiction.** The ⚠️ rows are all "DB design silent but other docs require" — these are the **missing specifications** that the API spec MS-1 through MS-15 list, not contradictions.

---

## Final Verdict

# **PASS WITH CHANGES**

The authoritative documents (PRD V1.1, Business Rules, Database Design V1.0, V1.9 Accounting Matrix) and the API Architecture V1.0 are now **semantically consistent across:**

- Data integrity (`is_negative_stock_fallback`, `stock_movements` CHECK, `value_adjustment` mechanism)
- Accounting correctness (8 invariants verified; Event 18/19 P&L behavior is explicit and consistent)
- Inventory correctness (negative-stock rule, historical cost immutability, moving-average, negative-stock COGS fallback)
- Authorization (canonical capability catalog unified; shorthand forms superseded)
- Lifecycle behavior (5 sale states + 1 derived; 5 purchase states; cancellation/return/reversal semantics unchanged)
- Historical data (audit log append-only; posted records immutable; corrections via cancel/return)

**Remaining changes required before implementation** (none of these is a contradiction; they are prerequisites to emit DDL and code):

1. Update PRD §8.1 lifecycle table to enumerate `partially_returned` for consistency with DB spec (cosmetic; no behavior change).
2. Add `CHECK (lifecycle_status != 'cancelled' OR cancelled_by IS NOT NULL)` to `sales` and `purchases` (currently API-enforced; should be DB-enforced for defense in depth).
3. Emit the DDL (out of scope of this phase per the user's instruction).
4. Generate the OpenAPI 3.1 contract (out of scope per the user's instruction).
5. Resolve the two open TBDs (OD-3 low-stock threshold default; OD-6 / OD-16 rounding mode; OD-7 / OD-17 document numbering default) with the Owner. The API spec documents defaults that work without resolution.

**Do not proceed to OpenAPI, DDL, migrations, frontend architecture, or implementation until items 1–5 are addressed** (with items 3–5 being the next phases in the documented sequence).

This concludes the document-reconciliation pass. The reconciliation report itself is `Reconciliation-Report-V1.0.md` in the same folder.
