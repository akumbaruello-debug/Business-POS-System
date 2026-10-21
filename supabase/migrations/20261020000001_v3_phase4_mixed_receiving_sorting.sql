-- V3 Phase 4: pending mixed receipts, classified sorting, reconciliation and waste.
BEGIN;

ALTER TABLE purchase_lines ALTER COLUMN product_id DROP NOT NULL;
ALTER TABLE purchase_lines ADD COLUMN receipt_type VARCHAR(20) NOT NULL DEFAULT 'classified';
ALTER TABLE purchase_lines ADD COLUMN unit_id INT NULL REFERENCES units(id) ON DELETE RESTRICT;
ALTER TABLE purchase_lines ADD CONSTRAINT ck_purchase_lines_receipt_type CHECK
  ((receipt_type='classified' AND product_id IS NOT NULL) OR
   (receipt_type='unsorted' AND product_id IS NULL AND unit_id IS NOT NULL));
ALTER TABLE purchase_return_lines ALTER COLUMN product_id DROP NOT NULL;

CREATE TABLE pending_receipt_batches (
  id BIGSERIAL PRIMARY KEY,
  purchase_line_id BIGINT NOT NULL UNIQUE REFERENCES purchase_lines(id) ON DELETE RESTRICT,
  unit_id INT NOT NULL REFERENCES units(id) ON DELETE RESTRICT,
  unit_code_snapshot VARCHAR(20) NOT NULL,
  unit_name_snapshot VARCHAR(50) NOT NULL,
  factor_to_base_snapshot NUMERIC(18,6) NOT NULL CHECK (factor_to_base_snapshot > 0),
  base_unit_code_snapshot VARCHAR(20) NOT NULL,
  original_quantity NUMERIC(15,4) NOT NULL CHECK (original_quantity > 0),
  original_value NUMERIC(15,2) NOT NULL CHECK (original_value >= 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_by INT NOT NULL REFERENCES users(id) ON DELETE RESTRICT
);
CREATE TABLE pending_inventory_movements (
  id BIGSERIAL PRIMARY KEY,
  pending_receipt_batch_id BIGINT NOT NULL REFERENCES pending_receipt_batches(id) ON DELETE RESTRICT,
  trigger VARCHAR(30) NOT NULL CHECK (trigger IN ('purchase_receipt','sorting_input','sorting_remainder','purchase_return','purchase_return_reversal','purchase_reversal','sorting_reversal')),
  quantity NUMERIC(15,4) NOT NULL CHECK (quantity <> 0),
  unit_cost_at_movement NUMERIC(15,4) NOT NULL CHECK (unit_cost_at_movement >= 0),
  total_cost NUMERIC(15,2) NOT NULL CHECK ((quantity > 0 AND total_cost >= 0) OR (quantity < 0 AND total_cost <= 0)),
  reference_type VARCHAR(40) NOT NULL,
  reference_id BIGINT NOT NULL,
  reference_line_id BIGINT NULL,
  reversal_of_movement_id BIGINT NULL REFERENCES pending_inventory_movements(id) ON DELETE RESTRICT,
  reason TEXT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_by INT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  CHECK (reversal_of_movement_id IS NULL OR trigger IN ('purchase_return_reversal','purchase_reversal','sorting_reversal'))
);
CREATE FUNCTION fn_pending_receipt_batch_immutable() RETURNS TRIGGER AS $$
BEGIN RAISE EXCEPTION 'Pending receipt conversion and cost snapshots are immutable'; END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_pending_receipt_batch_immutable BEFORE UPDATE OR DELETE ON pending_receipt_batches
FOR EACH ROW EXECUTE FUNCTION fn_pending_receipt_batch_immutable();
CREATE INDEX ix_pending_movements_batch ON pending_inventory_movements(pending_receipt_batch_id,id);
CREATE INDEX ix_pending_movements_reference ON pending_inventory_movements(reference_type,reference_id);
CREATE UNIQUE INDEX ux_pending_movement_reversal ON pending_inventory_movements(reversal_of_movement_id) WHERE reversal_of_movement_id IS NOT NULL;
CREATE FUNCTION fn_pending_inventory_movements_immutable() RETURNS TRIGGER AS $$
BEGIN RAISE EXCEPTION 'pending_inventory_movements is append-only; use a linked compensating movement'; END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_pending_inventory_movements_no_update BEFORE UPDATE OR DELETE ON pending_inventory_movements
FOR EACH ROW EXECUTE FUNCTION fn_pending_inventory_movements_immutable();
CREATE FUNCTION fn_pending_inventory_balance_guard() RETURNS TRIGGER AS $$
DECLARE v_qty NUMERIC(15,4); v_value NUMERIC(15,2);
BEGIN
  PERFORM 1 FROM pending_receipt_batches WHERE id=NEW.pending_receipt_batch_id FOR UPDATE;
  SELECT COALESCE(SUM(quantity),0),COALESCE(SUM(total_cost),0) INTO v_qty,v_value FROM pending_inventory_movements WHERE pending_receipt_batch_id=NEW.pending_receipt_batch_id;
  IF v_qty + NEW.quantity < 0 OR v_value + NEW.total_cost < 0 THEN RAISE EXCEPTION 'Insufficient pending receipt balance' USING ERRCODE='check_violation'; END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_pending_inventory_balance_guard BEFORE INSERT ON pending_inventory_movements
FOR EACH ROW EXECUTE FUNCTION fn_pending_inventory_balance_guard();

ALTER TABLE stock_movements DROP CONSTRAINT ck_sm_trigger;
ALTER TABLE stock_movements ADD CONSTRAINT ck_sm_trigger CHECK (trigger IN (
  'purchase_receipt','sale','sales_return','purchase_return','production_input','production_output','stock_adjustment','opening_balance',
  'sale_reversal','purchase_reversal','sales_return_reversal','purchase_return_reversal','production_reversal','adjustment_reversal',
  'value_adjustment','sorting_output','sorting_reversal'));
ALTER TABLE stock_movements DROP CONSTRAINT ck_sm_total_cost_nonzero;
ALTER TABLE stock_movements ADD CONSTRAINT ck_sm_total_cost_nonzero CHECK (
  (trigger IN ('value_adjustment','sorting_output','sorting_reversal')) OR
  (total_cost IS NULL AND quantity IS NULL) OR
  (total_cost IS NOT NULL AND quantity IS NOT NULL AND SIGN(total_cost)=SIGN(quantity)
   AND (total_cost<>0 OR trigger IN ('sorting_output','sorting_reversal'))));
ALTER TABLE stock_movements DROP CONSTRAINT ck_sm_reversal_trigger;
ALTER TABLE stock_movements ADD CONSTRAINT ck_sm_reversal_trigger CHECK (reversal_of_movement_id IS NULL OR
  trigger IN ('sale_reversal','purchase_reversal','sales_return_reversal','purchase_return_reversal',
              'production_reversal','adjustment_reversal','sorting_reversal'));
ALTER TABLE stock_movements DROP CONSTRAINT ck_sm_nonreversal_trigger;
ALTER TABLE stock_movements ADD CONSTRAINT ck_sm_nonreversal_trigger CHECK (reversed_by_movement_id IS NULL OR
  trigger NOT IN ('sale_reversal','purchase_reversal','sales_return_reversal','purchase_return_reversal',
                  'production_reversal','adjustment_reversal','sorting_reversal'));

CREATE TABLE sorting_events (
  id BIGSERIAL PRIMARY KEY,
  pending_receipt_batch_id BIGINT NOT NULL REFERENCES pending_receipt_batches(id) ON DELETE RESTRICT,
  input_quantity NUMERIC(15,4) NOT NULL CHECK (input_quantity>0),
  loss_quantity NUMERIC(15,4) NOT NULL DEFAULT 0 CHECK (loss_quantity>=0),
  remaining_quantity NUMERIC(15,4) NOT NULL DEFAULT 0 CHECK (remaining_quantity>=0),
  source_value_snapshot NUMERIC(15,2) NULL CHECK (source_value_snapshot>=0),
  loss_value_snapshot NUMERIC(15,2) NULL CHECK (loss_value_snapshot>=0),
  financial_category_id INT NULL REFERENCES financial_categories(id) ON DELETE RESTRICT,
  reason TEXT NOT NULL CHECK (length(btrim(reason))>0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_by INT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
  posted_at TIMESTAMPTZ NULL,
  posted_by INT NULL REFERENCES users(id) ON DELETE RESTRICT,
  version INT NOT NULL DEFAULT 1,
  CHECK ((loss_quantity=0 AND financial_category_id IS NULL) OR (loss_quantity>0 AND financial_category_id IS NOT NULL))
);
CREATE INDEX ix_sorting_events_batch ON sorting_events(pending_receipt_batch_id,created_at);
CREATE INDEX ix_sorting_events_posted ON sorting_events(posted_at) WHERE posted_at IS NOT NULL;
CREATE TABLE sorting_event_outputs (
  id BIGSERIAL PRIMARY KEY,
  sorting_event_id BIGINT NOT NULL REFERENCES sorting_events(id) ON DELETE RESTRICT,
  product_id INT NOT NULL REFERENCES products(id) ON DELETE RESTRICT,
  quantity NUMERIC(15,4) NOT NULL CHECK (quantity>0),
  unit_id INT NOT NULL REFERENCES units(id) ON DELETE RESTRICT,
  unit_code_snapshot VARCHAR(20) NOT NULL,
  factor_to_base_snapshot NUMERIC(18,6) NOT NULL CHECK (factor_to_base_snapshot>0),
  base_quantity NUMERIC(15,4) NOT NULL CHECK (base_quantity>0),
  value_snapshot NUMERIC(15,2) NOT NULL CHECK (value_snapshot>=0),
  stock_movement_id BIGINT NULL UNIQUE REFERENCES stock_movements(id) ON DELETE RESTRICT,
  line_number SMALLINT NOT NULL CHECK (line_number>0),
  UNIQUE (sorting_event_id,line_number)
);
CREATE TABLE sorting_event_reversals (
  id BIGSERIAL PRIMARY KEY,
  sorting_event_id BIGINT NOT NULL UNIQUE REFERENCES sorting_events(id) ON DELETE RESTRICT,
  reason TEXT NOT NULL CHECK (length(btrim(reason))>0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_by INT NOT NULL REFERENCES users(id) ON DELETE RESTRICT
);
CREATE FUNCTION fn_sorting_posted_immutable() RETURNS TRIGGER AS $$
DECLARE v_posted_at TIMESTAMPTZ; v_output_qty NUMERIC(15,4);
BEGIN
  IF TG_TABLE_NAME='sorting_events' THEN
    IF OLD.posted_at IS NOT NULL THEN RAISE EXCEPTION 'Posted sorting events are immutable; use a linked reversal'; END IF;
    IF OLD.posted_at IS NULL AND NEW.posted_at IS NOT NULL THEN
      SELECT COALESCE(SUM(base_quantity),0) INTO v_output_qty FROM sorting_event_outputs WHERE sorting_event_id=OLD.id;
      IF v_output_qty+NEW.loss_quantity+NEW.remaining_quantity<>NEW.input_quantity THEN
        RAISE EXCEPTION 'Sorting quantity reconciliation must be exact at persisted precision' USING ERRCODE='check_violation';
      END IF;
    END IF;
    RETURN NEW;
  END IF;
  SELECT posted_at INTO v_posted_at FROM sorting_events WHERE id=OLD.sorting_event_id;
  IF v_posted_at IS NOT NULL THEN RAISE EXCEPTION 'Posted sorting outputs are immutable; use a linked reversal'; END IF;
  RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_sorting_events_immutable BEFORE UPDATE OR DELETE ON sorting_events FOR EACH ROW EXECUTE FUNCTION fn_sorting_posted_immutable();
CREATE TRIGGER trg_sorting_outputs_immutable BEFORE INSERT OR UPDATE OR DELETE ON sorting_event_outputs FOR EACH ROW EXECUTE FUNCTION fn_sorting_posted_immutable();
CREATE FUNCTION fn_sorting_reversals_immutable() RETURNS TRIGGER AS $$
BEGIN RAISE EXCEPTION 'sorting_event_reversals is append-only'; END;
$$ LANGUAGE plpgsql;
CREATE TRIGGER trg_sorting_reversals_immutable BEFORE UPDATE OR DELETE ON sorting_event_reversals
FOR EACH ROW EXECUTE FUNCTION fn_sorting_reversals_immutable();

CREATE OR REPLACE VIEW inventory_valuation AS
SELECT COALESCE((SELECT SUM(inventory_value) FROM product_valuation),0) AS product_inventory_value,
       COALESCE((SELECT SUM(total_cost) FROM pending_inventory_movements),0) AS pending_inventory_value,
       COALESCE((SELECT SUM(inventory_value) FROM product_valuation),0)+
         COALESCE((SELECT SUM(total_cost) FROM pending_inventory_movements),0) AS total_inventory_value;

INSERT INTO capabilities(code,description) VALUES ('inventory.sort','Create, post, and reverse sorting events.') ON CONFLICT(code) DO NOTHING;
INSERT INTO role_capabilities(role_id,capability_id)
SELECT r.id,c.id FROM roles r CROSS JOIN capabilities c WHERE r.name='Owner' AND c.code='inventory.sort'
ON CONFLICT DO NOTHING;
COMMIT;
