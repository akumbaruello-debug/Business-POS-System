-- =============================================================================
-- Fix Staff role defaults: remove purchase-return courier capabilities
-- =============================================================================
-- Per PRD V1.1 §6.1 (authoritative permission table): sensitive actions
-- stay OFF for Staff unless explicitly granted. The Phase-E migration
-- (20260917000001) granted `purchase.return.finalize` and
-- `purchase.return.arrival` to the Staff role; Phase 1 corrected the
-- code default (app/authz/caps.py) and the canonical seed (schema.sql
-- §19.3), but databases migrated through 20260917000001 still carry the
-- two grants. This fix-forward migration removes exactly those two
-- role-level grants.
--
-- Scope (narrow by design):
--   - Staff role rows for the two courier capabilities: DELETED.
--   - Owner role: untouched (Owner holds every capability by grant-all).
--   - user_capability_overrides: untouched (explicit per-user grants and
--     revokes survive; an explicitly granted user keeps the capability).
--   - All other Staff capabilities: untouched.
--   - audit_log and all business tables: untouched.
--
-- Idempotent: deleting absent rows is a no-op; safe to run exactly once
-- per the migrate.sh runner (lexical order, checksum-guarded).
-- =============================================================================

DELETE FROM role_capabilities rc
USING roles r, capabilities c
WHERE rc.role_id = r.id
  AND rc.capability_id = c.id
  AND r.name = 'Staff'
  AND c.code IN ('purchase.return.finalize', 'purchase.return.arrival');

-- Verify: Staff must hold neither courier capability at role level.
-- Raises if the correction did not apply (e.g. unexpected role rename).
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM role_capabilities rc
        JOIN roles r ON r.id = rc.role_id
        JOIN capabilities c ON c.id = rc.capability_id
        WHERE r.name = 'Staff'
          AND c.code IN ('purchase.return.finalize', 'purchase.return.arrival')
    ) THEN
        RAISE EXCEPTION 'staff courier capability grants still present after fix-forward migration';
    END IF;
END
$$;
