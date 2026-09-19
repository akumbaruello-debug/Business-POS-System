-- =============================================================================
-- supplier_repayments.payment_method_id → NULLABLE
-- Per authoritative audit (`m6_corrected_validation.md` / Phase E contract §6.2):
--   `payment_method_id` semantically describes "How cash was received" and
--   belongs to an actual supplier-repayment transaction. A return-created
--   SREC obligation row carries `received_amount = 0` (no cash has yet been
--   received); forcing a dummy method ID corrupts the payment-channel story
--   and the GET /supplier-repayments?filter[payment_method_id] filter.
--
-- This migration widens the column to NULLABLE. Existing real-repayment rows
-- retain their concrete `payment_method_id`; only future return-created
-- obligations (and any obligation pre-existed from cancellation flows if the
-- same gap appears there) will be allowed to use NULL.
--
-- The foreign-key constraint `fk_srep_method` remains intact: PostgreSQL
-- allows NULL on a nullable FK column (it is satisfied by NULL).
-- =============================================================================

ALTER TABLE supplier_repayments
    ALTER COLUMN payment_method_id DROP NOT NULL;
