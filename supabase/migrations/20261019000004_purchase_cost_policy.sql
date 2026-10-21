-- V3 Phase 3: separately configured purchase costs and immutable posting snapshots.
ALTER TABLE purchase_lines
    ADD COLUMN allocated_capitalizable_cost NUMERIC(15,2) NOT NULL DEFAULT 0
        CHECK (allocated_capitalizable_cost >= 0),
    ADD COLUMN allocated_operating_cost NUMERIC(15,2) NOT NULL DEFAULT 0
        CHECK (allocated_operating_cost >= 0);

CREATE TABLE purchase_cost_categories (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    capitalizable BOOLEAN NOT NULL,
    financial_category_id INT NULL REFERENCES financial_categories(id) ON DELETE RESTRICT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by INT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    updated_by INT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    CONSTRAINT ck_purchase_cost_category_expense_mapping CHECK
      ((capitalizable AND financial_category_id IS NULL) OR
       (NOT capitalizable AND financial_category_id IS NOT NULL))
);

CREATE TABLE purchase_costs (
    id BIGSERIAL PRIMARY KEY,
    purchase_id BIGINT NOT NULL REFERENCES purchases(id) ON DELETE CASCADE,
    category_id INT NOT NULL REFERENCES purchase_cost_categories(id) ON DELETE RESTRICT,
    amount NUMERIC(15,2) NOT NULL CHECK (amount >= 0),
    capitalizable_snapshot BOOLEAN NOT NULL,
    financial_category_id_snapshot INT NULL REFERENCES financial_categories(id) ON DELETE RESTRICT,
    allocation_method VARCHAR(20) NOT NULL DEFAULT 'line_value'
      CHECK (allocation_method IN ('line_value','weight','manual')),
    description TEXT NULL,
    posted_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by INT NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    CONSTRAINT ck_purchase_cost_mapping_snapshot CHECK
      ((capitalizable_snapshot AND financial_category_id_snapshot IS NULL) OR
       (NOT capitalizable_snapshot AND financial_category_id_snapshot IS NOT NULL))
);
CREATE INDEX ix_purchase_costs_purchase ON purchase_costs(purchase_id);
CREATE INDEX ix_purchase_costs_posted ON purchase_costs(posted_at) WHERE posted_at IS NOT NULL;

CREATE TABLE purchase_cost_allocations (
    id BIGSERIAL PRIMARY KEY,
    purchase_cost_id BIGINT NOT NULL REFERENCES purchase_costs(id) ON DELETE CASCADE,
    purchase_line_id BIGINT NOT NULL REFERENCES purchase_lines(id) ON DELETE RESTRICT,
    amount NUMERIC(15,2) NOT NULL CHECK (amount >= 0),
    UNIQUE (purchase_cost_id, purchase_line_id)
);

CREATE OR REPLACE FUNCTION fn_purchase_cost_snapshot_immutable() RETURNS TRIGGER AS $$
BEGIN
    IF OLD.posted_at IS NOT NULL THEN
        RAISE EXCEPTION 'Posted purchase costs are immutable.' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_purchase_cost_snapshot_immutable
BEFORE UPDATE OR DELETE ON purchase_costs
FOR EACH ROW EXECUTE FUNCTION fn_purchase_cost_snapshot_immutable();

CREATE OR REPLACE FUNCTION fn_purchase_cost_allocation_immutable() RETURNS TRIGGER AS $$
DECLARE
    v_cost_id BIGINT;
    v_posted_at TIMESTAMPTZ;
BEGIN
    v_cost_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.purchase_cost_id ELSE NEW.purchase_cost_id END;
    SELECT posted_at INTO v_posted_at FROM purchase_costs WHERE id = v_cost_id;
    IF v_posted_at IS NOT NULL THEN
        RAISE EXCEPTION 'Posted purchase cost allocations are immutable.' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_purchase_cost_allocation_immutable
BEFORE INSERT OR UPDATE OR DELETE ON purchase_cost_allocations
FOR EACH ROW EXECUTE FUNCTION fn_purchase_cost_allocation_immutable();

INSERT INTO system_settings (key, value, value_type, description)
VALUES ('default_purchase_cost_allocation', 'line_value', 'string',
        'Default allocation for additional purchase costs: line_value, weight, or manual.')
ON CONFLICT (key) DO NOTHING;

CREATE TRIGGER trg_purchase_cost_categories_touch_updated
BEFORE UPDATE ON purchase_cost_categories
FOR EACH ROW EXECUTE FUNCTION fn_touch_updated_at();
