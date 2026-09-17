-- =============================================================================
-- 20260917000001 — Purchase Return Finalization + Supplier Arrival + Overdue Override
-- =============================================================================
-- Adds the columns / capabilities / trigger / audit-action extensions required by
-- the locked Phase-E finalization contract.
--
-- Locked business rules (NOT re-decided by this migration):
--   * 5 calendar days (overdue = supplier_arrival_at + 5 days < NOW())
--   * finalize + arrival are manual, granted to Owner + Staff
--   * overdue override is Owner-only
--   * supplier repayment is independent of all of the above
--   * cancellation is blocked once finalized_at IS NOT NULL
--
-- Migration is idempotent: every ALTER / INSERT is guarded so re-runs are safe.
-- =============================================================================

BEGIN;

-- -----------------------------------------------------------------------------
-- 1. purchase_returns column additions
-- -----------------------------------------------------------------------------

ALTER TABLE purchase_returns
    ADD COLUMN IF NOT EXISTS finalized_at            TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS finalized_by            INT         NULL,
    ADD COLUMN IF NOT EXISTS finalized_reason        TEXT        NULL,
    ADD COLUMN IF NOT EXISTS supplier_arrival_at     TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS supplier_arrival_by     INT         NULL,
    ADD COLUMN IF NOT EXISTS overdue_override_at     TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS overdue_override_by     INT         NULL,
    ADD COLUMN IF NOT EXISTS overdue_override_reason TEXT        NULL;

-- FK: RETAIN. finalized_by / supplier_arrival_by / overdue_override_by are all
-- INT NULL REFERENCES users(id) ON DELETE SET NULL — a nullable FK is valid;
-- NULL means "not yet set" while any non-NULL value must reference users(id).
ALTER TABLE purchase_returns
    DROP CONSTRAINT IF EXISTS fk_purchase_returns_finalized_by,
    ADD CONSTRAINT fk_purchase_returns_finalized_by
        FOREIGN KEY (finalized_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE purchase_returns
    DROP CONSTRAINT IF EXISTS fk_purchase_returns_arrival_by,
    ADD CONSTRAINT fk_purchase_returns_arrival_by
        FOREIGN KEY (supplier_arrival_by) REFERENCES users (id) ON DELETE SET NULL;

ALTER TABLE purchase_returns
    DROP CONSTRAINT IF EXISTS fk_purchase_returns_override_by,
    ADD CONSTRAINT fk_purchase_returns_override_by
        FOREIGN KEY (overdue_override_by) REFERENCES users (id) ON DELETE SET NULL;

-- -----------------------------------------------------------------------------
-- 2. Canonical capability additions (mirror caps.py)
--    finalize + arrival → granted to Staff (new capabilities, NOT reusing
--    purchase.return which Staff does not hold).
--    purchase.return.override → catalog only (Owner inherits via Owner grant-all;
--    NOT added to Staff defaults → Owner-only).
-- -----------------------------------------------------------------------------

INSERT INTO capabilities (code, description) VALUES
    ('purchase.return.finalize', 'Confirm courier handoff (finalize) on a posted purchase return. Irreversible.'),
    ('purchase.return.arrival',  'Record supplier arrival on a posted purchase return (starts the 5-day confirmation window).'),
    ('purchase.return.override', 'Owner-only: override an expired arrival-confirmation window on a posted purchase return.')
ON CONFLICT (code) DO NOTHING;

INSERT INTO role_capabilities (role_id, capability_id)
SELECT r.id, c.id
FROM roles r
JOIN capabilities c ON c.code IN ('purchase.return.finalize','purchase.return.arrival')
WHERE r.name = 'Staff'
  AND NOT EXISTS (
      SELECT 1 FROM role_capabilities rc2
      JOIN capabilities c2 ON c2.id = rc2.capability_id
      WHERE rc2.role_id = r.id AND c2.code IN ('purchase.return.finalize','purchase.return.arrival')
  );

-- -----------------------------------------------------------------------------
-- 3. fn_purchase_lifecycle_terminal: extend 'partially_returned' and 'returned'
--    branches to allow parent-purchase recovery. After an unfinalized return is
--    cancelled, _recover_parent_purchase_lifecycle collapses the parent to its
--    true state — including partially_returned -> posted (when active_returned
--    drops to 0 and the purchase is unpaid). Without 'posted' here the recovery
--    UPDATE raises "invalid lifecycle transition partially_returned -> posted".
-- -----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION fn_purchase_lifecycle_terminal() RETURNS TRIGGER AS $$
BEGIN
    IF OLD.lifecycle_status = 'cancelled' AND NEW.lifecycle_status <> 'cancelled' THEN
        RAISE EXCEPTION
            'purchase % is cancelled; lifecycle_status is terminal',
            OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.lifecycle_status IS DISTINCT FROM NEW.lifecycle_status THEN
        IF NOT (
            (OLD.lifecycle_status = 'draft' AND NEW.lifecycle_status IN ('posted','cancelled'))
         OR (OLD.lifecycle_status = 'posted' AND NEW.lifecycle_status IN ('completed','partially_returned','returned','cancelled'))
         OR (OLD.lifecycle_status = 'partially_returned' AND NEW.lifecycle_status IN ('returned','cancelled','posted'))
         OR (OLD.lifecycle_status = 'returned' AND NEW.lifecycle_status IN ('cancelled','partially_returned','completed','posted'))
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

-- -----------------------------------------------------------------------------
-- 4. audit_log.ck_audit_action: add 'finalize', 'arrival', 'override'
-- -----------------------------------------------------------------------------

ALTER TABLE audit_log DROP CONSTRAINT IF EXISTS ck_audit_action;
ALTER TABLE audit_log
    ADD CONSTRAINT ck_audit_action
        CHECK (action IN ('create','post','cancel','complete','return','adjust',
                          'movement','price_override','payment','refund',
                          'permission_grant','permission_revoke','settings_change',
                          'deactivate','update','finalize','arrival','override'));

COMMIT;
