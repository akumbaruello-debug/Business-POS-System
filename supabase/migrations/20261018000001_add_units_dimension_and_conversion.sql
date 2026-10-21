-- =============================================================================
-- 20261018000001 — Units dimension + conversion factor (V3 Phase 2)
-- =============================================================================
-- V3-PLAN §5.2 / §Phase 2 (lines 229-243): extend existing Settings → Units
-- with unit dimension and conversion factor. Mass dimension has kg as base
-- (kg=1, kuintal=100, ton=1000); count dimension has buah/pcs as base with
-- factor 1. Units from different dimensions must never be added together and
-- buah must never be implicitly treated as kg.
--
-- Additive, guarded schema change. No DML on existing rows, no table
-- recreation, no permission changes, no seed of starter data (template
-- CSV rows are owner-editable reference material only — see
-- V3-PLAN/Business-POS-V3-Plan-Package/templates/README.md requirement #6:
-- never bulk-add starter values via direct production SQL).
--
-- Scope: this table only. Does NOT touch products, price history, or any
-- existing behavior (those are later V3 phases).
-- =============================================================================

ALTER TABLE units
    ADD COLUMN dimension       VARCHAR(20) NULL,
    ADD COLUMN base_unit_id    BIGINT      NULL,
    ADD COLUMN factor_to_base  NUMERIC(18, 6) NULL;

-- Self-referential FK: base unit must exist and be a real unit row.
ALTER TABLE units
    ADD CONSTRAINT fk_units_base_unit
        FOREIGN KEY (base_unit_id) REFERENCES units (id) ON DELETE RESTRICT;

-- factor_to_base must be positive and numerically finite.
ALTER TABLE units
    ADD CONSTRAINT ck_units_factor_positive
        CHECK (factor_to_base IS NULL OR factor_to_base > 0);

-- dimension enum-ish check (guarded value). Keep it open to expansion.
ALTER TABLE units
    ADD CONSTRAINT ck_units_dimension
        CHECK (dimension IS NULL OR dimension IN ('mass', 'count'));

-- A unit must never point at itself as its base (a self-loop is meaningless).
ALTER TABLE units
    ADD CONSTRAINT ck_units_base_not_self
        CHECK (base_unit_id IS NULL OR base_unit_id <> id);

CREATE INDEX ix_units_dimension ON units (dimension);

-- Base-unit contract: base_unit_id NULL = this unit IS the canonical base of its
-- dimension; a non-base unit sets base_unit_id -> canonical + factor_to_base.
-- Cross-dimension conversion is enforced at the API/service layer
-- (_require_same_dimension), not a DB check.
