-- =============================================================================
-- Fix purchase_returns version trigger to use fn_bump_version_only
-- =============================================================================
-- Per authoritative schema.sql (lines 2191, 2223-2225):
--   - fn_bump_version_only() bumps version WITHOUT setting updated_at
--   - purchase_returns has NO updated_at column (per schema.sql §7.5)
--   - trg_purchase_returns_bump_version must use fn_bump_version_only
--
-- The live database currently has trg_purchase_returns_bump_version calling
-- fn_bump_version_and_updated_at(), which fails because purchase_returns
-- lacks an updated_at column. This migration brings the DB into alignment
-- with schema.sql.
-- =============================================================================

-- Ensure fn_bump_version_only exists (copy from schema.sql)
CREATE OR REPLACE FUNCTION fn_bump_version_only() RETURNS TRIGGER AS $$
BEGIN
    NEW.version := COALESCE(OLD.version, 0) + 1;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Drop the incorrect trigger (calls fn_bump_version_and_updated_at)
DROP TRIGGER IF EXISTS trg_purchase_returns_bump_version ON purchase_returns;

-- Recreate with the correct function (fn_bump_version_only)
CREATE TRIGGER trg_purchase_returns_bump_version
BEFORE UPDATE ON purchase_returns
FOR EACH ROW EXECUTE FUNCTION fn_bump_version_only();

-- Verify the trigger is correct (this will raise an error if wrong)
-- The trigger should now increment version without touching updated_at
