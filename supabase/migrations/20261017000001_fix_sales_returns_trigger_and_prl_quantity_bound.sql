-- =============================================================================
-- 20261017000001 — Fix sales_returns version trigger + PRL quantity bound
-- =============================================================================
-- Corrective forward migration. Fixes the two schema-convergence gaps found by
-- Task D3 after replaying the migration chain onto a clean disposable database.
-- Applied migration history is NOT rewritten; schema.sql is NOT modified.
--
-- Gap A — trg_sales_returns_bump_version
--   The chain leaves this trigger calling fn_bump_version_and_updated_at(),
--   which assigns NEW.updated_at. sales_returns has NO updated_at column
--   (it tracks lifecycle via cancellation_date + version, like purchase_returns),
--   so every UPDATE on sales_returns fails with:
--     ERROR: record "new" has no field "updated_at"
--   Application code updates sales_returns
--   (backend/app/repositories/sales.py, sales-return cancel path), so this is a
--   live defect. schema.sql §18.20 wires the trigger to fn_bump_version_only() —
--   the same fix already applied to manual_finance_entries (20260926000001) and
--   purchase_returns (20260919000003) by earlier migrations.
--
-- Gap B — fn_prl_quantity_bound
--   The chain retains the baseline definition, which sums returned quantity
--   across ALL purchase_return_lines for the purchase line, including lines
--   belonging to CANCELLED purchase returns. A cancelled return therefore keeps
--   consuming the per-line return ceiling and can block a legitimate later return.
--   schema.sql §18.12 restricts the sum to posted purchase returns via a join on
--   purchase_returns. This migration restores that predicate (BR-PURCHASE-006).
--
-- Scope: these two objects only. No data backfill, no business-table DML, no
-- table recreation, no permission changes.
--
-- Precondition: fn_bump_version_only() must already exist. It is NOT defined by
-- the initial baseline; migration 20260919000003 creates it, and earlier
-- migrations (20260919000003, 20260926000001) already rely on it. Because this
-- migration is ordered after those, the function is present on any database
-- that has replayed the chain in full.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- Gap A: sales_returns version trigger must use fn_bump_version_only
-- Mirrors schema.sql §18.20 exactly: BEFORE UPDATE ON sales_returns, FOR EACH ROW.
-- DROP is guarded so a re-run is safe; the trigger is then recreated against the
-- version-only function that does not reference the absent updated_at column.
-- -----------------------------------------------------------------------------
DROP TRIGGER IF EXISTS trg_sales_returns_bump_version ON sales_returns;

CREATE TRIGGER trg_sales_returns_bump_version
BEFORE UPDATE ON sales_returns
FOR EACH ROW EXECUTE FUNCTION fn_bump_version_only();

-- -----------------------------------------------------------------------------
-- Gap B: fn_prl_quantity_bound must ignore cancelled purchase returns
-- Body reproduced from schema.sql §18.12 (posted-only filter via the join).
-- Signature, return type, language, trigger binding, error handling and all other
-- predicates are unchanged; only the returned-quantity predicate is corrected.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_prl_quantity_bound() RETURNS TRIGGER AS $$
DECLARE
    v_orig_qty   NUMERIC(15,4);
    v_total_ret  NUMERIC(15,4);
BEGIN
    SELECT quantity INTO v_orig_qty FROM purchase_lines WHERE id = NEW.purchase_line_id;
    IF v_orig_qty IS NULL THEN
        RAISE EXCEPTION 'purchase_line % not found', NEW.purchase_line_id;
    END IF;

    SELECT COALESCE(SUM(prl.quantity), 0) INTO v_total_ret
    FROM purchase_return_lines prl
    JOIN purchase_returns pr ON pr.id = prl.purchase_return_id
    WHERE prl.purchase_line_id = NEW.purchase_line_id
      AND pr.lifecycle_status = 'posted'
      AND prl.id <> COALESCE(NEW.id, -1);

    IF (v_total_ret + NEW.quantity) > v_orig_qty THEN
        RAISE EXCEPTION
            'purchase_return quantity for purchase_line % exceeds remaining (returned=%, new=%, original=%); BR-PURCHASE-006',
            NEW.purchase_line_id, v_total_ret, NEW.quantity, v_orig_qty
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMIT;
