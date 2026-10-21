-- Explicit product unit prices take precedence over prices derived from kg.
ALTER TABLE products
    ADD COLUMN purchase_price_override NUMERIC(15,2) NULL CHECK (purchase_price_override IS NULL OR purchase_price_override >= 0),
    ADD COLUMN selling_price_override NUMERIC(15,2) NULL CHECK (selling_price_override IS NULL OR selling_price_override >= 0);

ALTER TABLE product_price_history
    ADD COLUMN purchase_price_override NUMERIC(15,2) NULL CHECK (purchase_price_override IS NULL OR purchase_price_override >= 0),
    ADD COLUMN selling_price_override NUMERIC(15,2) NULL CHECK (selling_price_override IS NULL OR selling_price_override >= 0);
