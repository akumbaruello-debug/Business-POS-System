-- Per-SKU reference prices per kilogram. Existing unit-price columns and
-- transaction snapshots retain their current values and meaning.
ALTER TABLE products
    ADD COLUMN purchase_price_per_kg NUMERIC(15,2) NULL,
    ADD COLUMN selling_price_per_kg NUMERIC(15,2) NULL,
    ADD CONSTRAINT ck_products_purchase_price_per_kg
        CHECK (purchase_price_per_kg IS NULL OR purchase_price_per_kg >= 0),
    ADD CONSTRAINT ck_products_selling_price_per_kg
        CHECK (selling_price_per_kg IS NULL OR selling_price_per_kg >= 0);

ALTER TABLE product_price_history
    ADD COLUMN purchase_price_per_kg NUMERIC(15,2) NULL,
    ADD COLUMN selling_price_per_kg NUMERIC(15,2) NULL,
    ADD CONSTRAINT ck_pph_purchase_price_per_kg
        CHECK (purchase_price_per_kg IS NULL OR purchase_price_per_kg >= 0),
    ADD CONSTRAINT ck_pph_selling_price_per_kg
        CHECK (selling_price_per_kg IS NULL OR selling_price_per_kg >= 0);
