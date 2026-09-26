-- =============================================================================
-- Fix manual_finance_entries version trigger to use fn_bump_version_only
-- =============================================================================
-- Per authoritative schema.sql (lines 2191–2196, 2210–2212):
--   - fn_bump_version_only() bumps version WITHOUT setting updated_at
--   - manual_finance_entries has NO updated_at column (only created_at, version)
--     (schema.sql §12.1: columns are id, category_id, entry_date, amount,
--      payment_method_id, notes, lifecycle_status, cancellation_date,
--      cancellation_reason, cancelled_by, created_at, created_by, version)
--   - trg_mfe_bump_version must use fn_bump_version_only
--
-- The live pos_dev database currently has trg_mfe_bump_version calling
-- fn_bump_version_and_updated_at(), which fails because manual_finance_entries
-- lacks an updated_at column. Every UPDATE on manual_finance_entries triggers:
--   ERROR: record "new" has no field "updated_at"
-- This blocks POST /manual-entries/{id}/cancel (lifecycle flip to 'cancelled').
--
-- This migration brings the DB trigger into alignment with schema.sql.
-- =============================================================================

-- fn_bump_version_only already exists (created by schema.sql baseline).
-- Ensure it is the correct version (idempotent CREATE OR REPLACE).
CREATE OR REPLACE FUNCTION fn_bump_version_only() RETURNS TRIGGER AS $$
BEGIN
    NEW.version := COALESCE(OLD.version, 0) + 1;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop the incorrect trigger (calls fn_bump_version_and_updated_at)
DROP TRIGGER IF EXISTS trg_mfe_bump_version ON manual_finance_entries;

-- Recreate with the correct function (fn_bump_version_only)
CREATE TRIGGER trg_mfe_bump_version
BEFORE UPDATE ON manual_finance_entries
FOR EACH ROW
EXECUTE FUNCTION fn_bump_version_only();
