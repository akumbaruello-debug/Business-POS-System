-- =============================================================================
-- 20260927000001 — Remove obsolete Purchase Return Courier (Phase-E) objects
-- =============================================================================
-- Removes the Courier finalization / supplier-arrival / overdue-override
-- feature that was introduced by 20260917000001. This migration is the
-- V1 release-gate reconciliation: the Courier capability is obsolete in V1
-- and must not ship.
--
-- Historical migration 20260917000001 (already applied to pos_dev) is left
-- untouched; this migration is the compensating forward migration.
--
-- Idempotent: every DROP is guarded with IF EXISTS so re-runs are safe.
--
-- Objects removed:
--   purchase_returns columns: finalized_at, finalized_by, finalized_reason,
--     supplier_arrival_at, supplier_arrival_by, overdue_override_at,
--     overdue_override_by, overdue_override_reason
--   foreign keys: fk_purchase_returns_finalized_by,
--     fk_purchase_returns_arrival_by, fk_purchase_returns_override_by
--   audit_log ck_audit_action values: 'finalize', 'arrival', 'override'
--   capabilities: purchase.return.finalize, purchase.return.arrival,
--     purchase.return.override
--   role_capabilities: Staff × purchase.return.finalize, Staff ×
--     purchase.return.arrival
--   fn_purchase_lifecycle_terminal: restore V1 baseline (remove the
--     'posted' recovery branches added for unfinalized-return cancellation).
--
-- V1-required objects PRESERVED (not touched):
--   supplier_repayments.purchase_return_id + fk_srep_purchase_return
--   supplier_repayments.payment_method_id (already nullable)
--   trg_purchase_returns_bump_version / fn_bump_version_only
--   trg_purchase_returns_lifecycle_terminal / fn_purchase_returns_lifecycle_terminal
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. Drop Courier foreign keys (must precede column drops that reference them)
-- -----------------------------------------------------------------------------
ALTER TABLE purchase_returns
    DROP CONSTRAINT IF EXISTS fk_purchase_returns_finalized_by,
    DROP CONSTRAINT IF EXISTS fk_purchase_returns_arrival_by,
    DROP CONSTRAINT IF EXISTS fk_purchase_returns_override_by;

-- -----------------------------------------------------------------------------
-- 2. Drop Courier columns from purchase_returns
-- -----------------------------------------------------------------------------
ALTER TABLE purchase_returns
    DROP COLUMN IF EXISTS finalized_at,
    DROP COLUMN IF EXISTS finalized_by,
    DROP COLUMN IF EXISTS finalized_reason,
    DROP COLUMN IF EXISTS supplier_arrival_at,
    DROP COLUMN IF EXISTS supplier_arrival_by,
    DROP COLUMN IF EXISTS overdue_override_at,
    DROP COLUMN IF EXISTS overdue_override_by,
    DROP COLUMN IF EXISTS overdue_override_reason;

-- -----------------------------------------------------------------------------
-- 3. Remove Courier audit actions from ck_audit_action
-- -----------------------------------------------------------------------------
ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS ck_audit_action;
ALTER TABLE audit_log
    ADD CONSTRAINT ck_audit_action
        CHECK (action IN ('create','post','cancel','complete','return','adjust',
                          'movement','price_override','payment','refund',
                          'permission_grant','permission_revoke','settings_change',
                          'deactivate','update'));

-- -----------------------------------------------------------------------------
-- 4. Remove Courier capability catalog rows
-- -----------------------------------------------------------------------------
DELETE FROM role_capabilities rc
USING capabilities c
WHERE rc.capability_id = c.id
  AND c.code IN ('purchase.return.finalize',
                 'purchase.return.arrival',
                 'purchase.return.override');

DELETE FROM capabilities
WHERE code IN ('purchase.return.finalize',
               'purchase.return.arrival',
               'purchase.return.override');

-- -----------------------------------------------------------------------------
-- 5. Restore fn_purchase_lifecycle_terminal to the V1 baseline
--    (remove the 'posted' recovery branches added by 20260917000001 for
--     unfinalized-return parent-lifecycle recovery).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_purchase_lifecycle_terminal() RETURNS TRIGGER AS $$
BEGIN
    IF OLD.lifecycle_status = 'cancelled' AND NEW.lifecycle_status <> 'cancelled' THEN
        RAISE EXCEPTION
            'purchase % is cancelled; lifecycle_status is terminal (INV-05)',
            OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.lifecycle_status IS DISTINCT FROM NEW.lifecycle_status THEN
        IF NOT (
            (OLD.lifecycle_status = 'draft' AND NEW.lifecycle_status IN ('posted','cancelled'))
         OR (OLD.lifecycle_status = 'posted' AND NEW.lifecycle_status IN ('completed','partially_returned','returned','cancelled'))
         OR (OLD.lifecycle_status = 'partially_returned' AND NEW.lifecycle_status IN ('returned','cancelled','posted'))
         OR (OLD.lifecycle_status = 'returned' AND NEW.lifecycle_status = 'cancelled')
         OR (OLD.lifecycle_status = 'completed' AND NEW.lifecycle_status IN ('partially_returned','cancelled'))
        ) THEN
            RAISE EXCEPTION
                'purchase %: invalid lifecycle transition % -> %',
                OLD.id, OLD.lifecycle_status, NEW.lifecycle_status
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMIT;