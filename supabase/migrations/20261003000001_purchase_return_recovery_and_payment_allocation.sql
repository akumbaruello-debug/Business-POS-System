-- =============================================================================
-- 20261003000001 — Purchase return lifecycle recovery and payment ceiling
-- =============================================================================
-- P1-1: Permit purchase lifecycle recomputation after cancelling a posted
-- return. The service derives the target from active returned quantity and
-- the payment state computed in currency units.
-- P1-2: Keep purchase payment allocations within the remaining payable after
-- posted purchase returns.
--
-- Forward-only migration. Historical migrations are not edited.

BEGIN;

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
         OR (OLD.lifecycle_status = 'partially_returned' AND NEW.lifecycle_status IN ('returned','cancelled','posted','completed'))
         OR (OLD.lifecycle_status = 'returned' AND NEW.lifecycle_status IN ('cancelled','partially_returned','completed','posted'))
         OR (OLD.lifecycle_status = 'completed' AND NEW.lifecycle_status IN ('partially_returned','cancelled','posted'))
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

CREATE OR REPLACE FUNCTION fn_purchase_payment_allocation_bound() RETURNS TRIGGER AS $$
DECLARE
    v_total       NUMERIC(15,2);
    v_returned    NUMERIC(15,2);
    v_allocated   NUMERIC(15,2);
BEGIN
    -- line_total already includes allocated shipping; mirror the service's
    -- derived purchase total without adding shipping a second time.
    SELECT COALESCE(SUM(line_total), 0) INTO v_total
    FROM purchase_lines WHERE purchase_id = NEW.purchase_id;

    SELECT COALESCE(SUM(total_value_returned), 0) INTO v_returned
    FROM purchase_returns
    WHERE purchase_id = NEW.purchase_id
      AND lifecycle_status = 'posted';

    SELECT COALESCE(SUM(amount), 0) INTO v_allocated
    FROM purchase_payments
    WHERE purchase_id = NEW.purchase_id
      AND id <> COALESCE(NEW.id, -1);

    IF (v_allocated + NEW.amount) > (v_total - v_returned) THEN
        RAISE EXCEPTION
            'purchase_payment allocation exceeds purchase payable (allocated=%, new=%, total=%, returned=%); BR-PAYMENT-004',
            v_allocated, NEW.amount, v_total, v_returned
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

COMMIT;
