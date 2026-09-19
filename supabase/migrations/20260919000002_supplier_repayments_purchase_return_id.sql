-- =============================================================================
-- supplier_repayments.purchase_return_id → NULLABLE FK to purchase_returns
-- Per authoritative audit:
--   Associates a return-created SREC obligation with the purchase return
--   that created it. This guarantees that multiple returns on the same
--   purchase each produce their own distinct SREC obligation, and repayments
--   against Return A never mutate Return B's SREC obligation.
-- =============================================================================

ALTER TABLE supplier_repayments
    ADD COLUMN purchase_return_id BIGINT NULL,
    ADD CONSTRAINT fk_srep_purchase_return
        FOREIGN KEY (purchase_return_id) REFERENCES purchase_returns (id) ON DELETE RESTRICT;

CREATE INDEX ix_srep_purchase_return ON supplier_repayments (purchase_return_id);
