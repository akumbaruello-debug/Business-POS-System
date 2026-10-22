-- =============================================================================
-- 20261022000001 — F1: align migration-built schemas with schema.sql
-- =============================================================================
-- schema.sql:1277 declares ix_sorting_outputs_product on
-- sorting_event_outputs(product_id, id), but no migration created it:
-- migration-built databases (e.g. pos_dev) drift from schema-built ones.
--
-- Semantics (do NOT infer from the name):
--   * Non-unique secondary index supporting products(id) ON DELETE RESTRICT
--     reverse lookups and stable (product_id, id) ordering of a sorting
--     event's outputs. No application query filters product_id on this table
--     yet; indexed per FK-column convention (ix_sale_lines_product et al.).
--   * Idempotent (IF NOT EXISTS) so re-running is a no-op.
--   * Additive-only: no constraint, trigger, or existing index touched.
--
-- Owner decision: F1 Option A (add migration) — approved.
CREATE INDEX IF NOT EXISTS ix_sorting_outputs_product
  ON sorting_event_outputs (product_id, id);
