-- =============================================================================
-- 20261019000001 — Product attributes: Jenis / Size / Kualitas (V3 Phase 2)
-- =============================================================================
-- V3-PLAN §5.1 (product identity) §Phase 2: model Jenis, Size, and Kualitas as
-- managed master values and link a product/SKU to the selected values. Each
-- combination is a distinct SKU with its own stock, pricing, and history.
--
-- Design (semantics — do NOT infer business rules or invent defaults):
--   * One table `product_attributes` discriminated by `kind`
--     (CHECK IN ('jenis','size','kualitas')). The three dimensions stay
--     semantically distinct: `kind` is closed to these three values.
--   * NO starter/default values are seeded. V3-PLAN templates are reference
--     material; per §6 + templates/README the Owner edits/reviews before use.
--     Existing rows are never invented for them.
--   * products get nullable FKs jenis_id / size_id / kualitas_id. NULL = the
--     product is not classified on that dimension. No automatic backfill:
--     existing products keep their exact prior state (Verified: their rows are
--     untouched by this migration).
--   * Duplicate-SKU prevention (accepted criteria: "duplicate combinations
--     ... rejected"): partial unique index on (jenis_id, size_id, kualitas_id)
--     WHERE all three are NOT NULL. A fully-classified product cannot be
--     duplicated; legacy/partially-classified rows (any NULL) are unaffected.
--   * Referenced values cannot be hard-deleted: FK RESTRICT surfaces a 23503
--     that the service translates to 409 referenced_by_history. Deactivation
--     is always allowed and reversible (no history rewrite).
--
-- Additive, guarded schema change. No DML on existing rows, no table
-- recreation, no permission grants, no starter-data seed. Does NOT touch
-- units or any other V1 behavior.
-- =============================================================================

CREATE TABLE product_attributes (
    id          SERIAL        PRIMARY KEY,
    kind        VARCHAR(20)   NOT NULL,
    name        VARCHAR(100)  NOT NULL,
    is_active   BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_product_attributes_kind
        CHECK (kind IN ('jenis', 'size', 'kualitas')),
    CONSTRAINT ux_product_attributes_kind_name
        UNIQUE (kind, name)
);

COMMENT ON TABLE product_attributes IS
  'Managed master values for the three product dimensions (Jenis/Size/Kualitas), discriminated by kind. No starter values are seeded — the Owner reviews/edits before use (V3-PLAN §5.1, §6).';
COMMENT ON COLUMN product_attributes.kind IS
  'One of: jenis (fish species/type), size (grading), kualitas (quality grade). Closed CHECK set — distinct semantics, never merged.';

-- Link products to their attribute values. NULL = not classified on that axis.
ALTER TABLE products
    ADD COLUMN jenis_id    INT NULL REFERENCES product_attributes (id) ON DELETE RESTRICT,
    ADD COLUMN size_id     INT NULL REFERENCES product_attributes (id) ON DELETE RESTRICT,
    ADD COLUMN kualitas_id INT NULL REFERENCES product_attributes (id) ON DELETE RESTRICT;

-- Duplicate full combination rejected; partial (any NULL) combos unaffected.
CREATE UNIQUE INDEX ux_products_dim_combination
    ON products (jenis_id, size_id, kualitas_id)
    WHERE jenis_id IS NOT NULL AND size_id IS NOT NULL AND kualitas_id IS NOT NULL;

CREATE INDEX ix_product_attributes_kind ON product_attributes (kind);
CREATE INDEX ix_product_attributes_active ON product_attributes (is_active);
CREATE INDEX ix_products_jenis ON products (jenis_id);
CREATE INDEX ix_products_size ON products (size_id);
CREATE INDEX ix_products_kualitas ON products (kualitas_id);

-- -----------------------------------------------------------------------------
-- New capabilities (V3 Phase 2): product_attribute.view / .manage
-- -----------------------------------------------------------------------------
-- Owner holds ALL capabilities (baseline seeds Owner via CROSS JOIN — capacity
-- catalog is expanded here, so Owner picks up the new codes through the same
-- CROSS JOIN when this migration runs in a fresh baseline, but for a database
-- that already has the baseline applied we explicitly grant Owner. Staff gets
-- view by default; manage is never granted to Staff implicitly.
INSERT INTO capabilities (code, description)
SELECT v.code, v.description
FROM (VALUES
    ('product_attribute.view',   'View product attributes (Jenis/Size/Kualitas).'),
    ('product_attribute.manage', 'Manage product attributes (Jenis/Size/Kualitas).')
) AS v(code, description)
WHERE NOT EXISTS (SELECT 1 FROM capabilities c WHERE c.code = v.code);

INSERT INTO role_capabilities (role_id, capability_id)
SELECT r.id, c.id
FROM roles r, capabilities c
WHERE r.name = 'Owner' AND c.code IN ('product_attribute.view', 'product_attribute.manage')
  AND NOT EXISTS (
      SELECT 1 FROM role_capabilities rc
      WHERE rc.role_id = r.id AND rc.capability_id = c.id
  );

INSERT INTO role_capabilities (role_id, capability_id)
SELECT r.id, c.id
FROM roles r, capabilities c
WHERE r.name = 'Staff' AND c.code = 'product_attribute.view'
  AND NOT EXISTS (
      SELECT 1 FROM role_capabilities rc
      WHERE rc.role_id = r.id AND rc.capability_id = c.id
  );
