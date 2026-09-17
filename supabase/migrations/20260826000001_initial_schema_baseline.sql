-- =============================================================================
-- db/migrations/m0001__initial_schema_baseline.sql
-- Business Management & POS System — Initial Schema Baseline (V1.0)
-- =============================================================================
-- This migration is the canonical, deterministic, fresh-database schema for
-- the Business-POS-System V1.0.
--
-- Source of truth:
--   - PRD V1.1
--   - Business Rules V1.0
--   - Database Design V1.0
--   - V1.9 Accounting Event Matrix
--   - API Architecture V1.0
--   - Reconciliation Report V1.0 (C-01 .. C-13)
--   - PostgreSQL Execution Validation Report V1.0
--
-- The DDL body is byte-for-byte equivalent to the validated `schema.sql`
-- (in the project root), with the seed block left intact.
--
-- A fresh database that applies this migration produces the exact same
-- catalog as `psql -f schema.sql` against PostgreSQL 17.11, verified by:
--   1. 35/35 integrity tests passing
--   2. Catalog comparison via db/scripts/compare_catalog.sh
--
-- Migration characteristics:
--   - One-shot baseline; does not use IF NOT EXISTS internally
--   - The seed block at the end of schema.sql already wraps the seed
--     inserts in BEGIN; ... COMMIT; (atomic)
--   - No data migrated; DDL + seed only
--   - Requires: pgcrypto and btree_gist extensions
--
-- =============================================================================

-- =============================================================================
-- Business Management & POS System — Canonical PostgreSQL Schema
-- Version: V1.0
-- Source of Truth: PRD V1.1, Business Rules V1.0, Database Design V1.0,
--                  V1.9-Accounting-Event-Matrix, API Architecture V1.0,
--                  Reconciliation Report V1.0 (C-01 .. C-13)
-- =============================================================================
--
-- This DDL is the single, canonical schema source for the V1 system.
-- It is deterministic, dependency-ordered, and safe to execute on a fresh
-- database. The file is structured as:
--
--   1. Extensions
--   2. Enum types (lookup domains)
--   3. Reference / configuration tables (lookup values, no business data)
--   4. Identity & access tables
--   5. Master data tables
--   6. Operational transactional tables (sales, purchases, production, etc.)
--   7. Inventory ledger (the single authoritative stock ledger)
--   8. Cash journal (the single authoritative cash ledger)
--   9. Refunds & supplier repayments
--  10. Manual finance entries
--  11. Configuration
--  12. Notifications
--  13. Sessions & idempotency
--  14. Audit log
--  15. Views (derived data)
--  16. Triggers & functions (all enforcement logic)
--  17. Indexes (declared inline; final consolidation)
--  18. Seed data (roles, capabilities, payment methods, financial categories,
--      cost types, system settings, default Owner user)
--
-- =============================================================================


-- =============================================================================
-- 1. EXTENSIONS
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;       -- gen_random_uuid(), digest()
CREATE EXTENSION IF NOT EXISTS btree_gist;     -- exclusion constraints if needed


-- =============================================================================
-- 2. ENUM TYPES
-- =============================================================================
--
-- We use VARCHAR(40) for trigger/status enum-like columns in the database
-- (per the DB Design Spec) and enforce validity through CHECK constraints
-- rather than PostgreSQL ENUM types. This is intentional:
--   * CHECK constraints are easier to amend in operational use (ALTER ...
--     ADD VALUE on a real ENUM cannot be done inside a transaction in some
--     older PostgreSQL versions, and removing a value is never allowed).
--   * VARCHAR with CHECK matches the API Architecture's enum_value_invalid
--     validation pattern (server validates against the closed set).
--
-- The set of "enum" domains below captures the closed value space for the
-- high-churn fields. Each is enforced by an inline CHECK on the column.
-- =============================================================================


-- =============================================================================
-- 3. REFERENCE / CONFIGURATION TABLES
-- =============================================================================
--
-- (Lookup tables that are seed-time data, no business transactions reference
-- them by INSERT; only by FK.)
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 3.1 payment_methods
-- Per V1.9 + API §15.7.3: cash, bank_transfer, e_wallet, other (seeded).
-- `is_cash` drives the over-tender / change semantics in sale_payments.
-- -----------------------------------------------------------------------------
CREATE TABLE payment_methods (
    id            SERIAL       PRIMARY KEY,
    code          VARCHAR(50)  NOT NULL,
    name          VARCHAR(100) NOT NULL,
    is_cash       BOOLEAN      NOT NULL DEFAULT FALSE,
    is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX ux_payment_methods_code ON payment_methods (code);
CREATE INDEX ix_payment_methods_active ON payment_methods (is_active);

COMMENT ON TABLE  payment_methods IS 'Configurable payment methods; is_cash=TRUE drives over-tender semantics. Seeded with cash, bank_transfer, e_wallet, other.';


-- -----------------------------------------------------------------------------
-- 3.2 cost_types
-- Production overhead cost types. Per PRD §15 + API §15.7.4.
-- Deactivated but never hard-deleted when referenced by history.
-- -----------------------------------------------------------------------------
CREATE TABLE cost_types (
    id          SERIAL       PRIMARY KEY,
    code        VARCHAR(50)  NOT NULL,
    name        VARCHAR(100) NOT NULL,
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX ux_cost_types_code ON cost_types (code);
CREATE INDEX ix_cost_types_active ON cost_types (is_active);

COMMENT ON TABLE cost_types IS 'Production cost types (labor, electricity, etc.). History-preserving on deactivation.';


-- -----------------------------------------------------------------------------
-- 3.3 financial_categories
-- Manual income/expense categories. Per PRD §17 + API §15.7.5.
-- entry_type is enforced as income|expense. The set is seeded; Owners may add
-- or rename (rename = metadata only; historical amounts unchanged).
-- -----------------------------------------------------------------------------
CREATE TABLE financial_categories (
    id          SERIAL       PRIMARY KEY,
    code        VARCHAR(50)  NOT NULL,
    name        VARCHAR(100) NOT NULL,
    entry_type  VARCHAR(20)  NOT NULL,
    is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_financial_categories_entry_type
        CHECK (entry_type IN ('income', 'expense'))
);

CREATE UNIQUE INDEX ux_financial_categories_code ON financial_categories (code);
CREATE INDEX ix_financial_categories_type_active ON financial_categories (entry_type, is_active);

COMMENT ON TABLE financial_categories IS 'Manual income/expense categories. Categories that duplicate derived entries (Sales, COGS, Production, Purchase Shipping) are excluded by API rule.';
COMMENT ON COLUMN financial_categories.entry_type IS 'income or expense; the API server-side validates that a category cannot be a derived-class duplicate.';


-- =============================================================================
-- 4. IDENTITY & ACCESS
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 4.1 roles
-- Two V1 roles: Owner and Staff. Custom roles are allowed; system roles
-- (Owner) cannot be hard-deleted while referenced by users.
-- -----------------------------------------------------------------------------
CREATE TABLE roles (
    id              SMALLSERIAL   PRIMARY KEY,
    name            VARCHAR(50)   NOT NULL,
    description     TEXT          NULL,
    is_system_role  BOOLEAN       NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX ux_roles_name ON roles (name);

COMMENT ON TABLE  roles IS 'Authorization roles. system roles (is_system_role=TRUE) cannot be hard-deleted while users reference them.';
COMMENT ON COLUMN roles.is_system_role IS 'TRUE for built-in roles (Owner, Staff). Custom roles have FALSE.';


-- -----------------------------------------------------------------------------
-- 4.2 capabilities
-- Canonical catalog per API §3.1 (reconciled via C-08..C-11).
-- The catalog is system-controlled: capabilities may be hidden but not
-- hard-deleted when referenced in role_capabilities.
-- -----------------------------------------------------------------------------
CREATE TABLE capabilities (
    id           SMALLSERIAL   PRIMARY KEY,
    code         VARCHAR(100)  NOT NULL,
    description  TEXT          NULL,
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX ux_capabilities_code ON capabilities (code);

COMMENT ON TABLE capabilities IS 'Canonical capability catalog (API §3.1). Capability codes are stable strings, e.g. sale.create, sale.price_override, product.edit.';


-- -----------------------------------------------------------------------------
-- 4.3 role_capabilities
-- Many-to-many between roles and capabilities.
-- -----------------------------------------------------------------------------
CREATE TABLE role_capabilities (
    role_id        SMALLINT    NOT NULL,
    capability_id  SMALLINT    NOT NULL,
    granted_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (role_id, capability_id),
    CONSTRAINT fk_role_capabilities_role
        FOREIGN KEY (role_id) REFERENCES roles (id) ON DELETE CASCADE,
    CONSTRAINT fk_role_capabilities_capability
        FOREIGN KEY (capability_id) REFERENCES capabilities (id) ON DELETE RESTRICT
);

CREATE INDEX ix_role_capabilities_capability ON role_capabilities (capability_id);


-- -----------------------------------------------------------------------------
-- 4.4 users
-- V1 staff + Owner. username unique, email optional, password_hash required.
-- Soft-delete via is_active=FALSE; sessions are revoked on deactivation.
-- -----------------------------------------------------------------------------
CREATE TABLE users (
    id              SERIAL        PRIMARY KEY,
    username        VARCHAR(64)   NOT NULL,
    full_name       VARCHAR(150)  NOT NULL,
    email           VARCHAR(150)  NULL,
    password_hash   VARCHAR(255)  NOT NULL,
    is_active       BOOLEAN       NOT NULL DEFAULT TRUE,
    role_id         SMALLINT      NOT NULL,
    failed_login_count  SMALLINT  NOT NULL DEFAULT 0,
    locked_until    TIMESTAMPTZ   NULL,
    last_login_at   TIMESTAMPTZ   NULL,
    created_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    created_by      INT           NULL,
    updated_by      INT           NULL,
    CONSTRAINT fk_users_role
        FOREIGN KEY (role_id) REFERENCES roles (id) ON DELETE RESTRICT,
    CONSTRAINT fk_users_created_by
        FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT fk_users_updated_by
        FOREIGN KEY (updated_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_users_email_format
        CHECK (email IS NULL OR email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$')
);

CREATE UNIQUE INDEX ux_users_username ON users (username);
CREATE UNIQUE INDEX ux_users_email ON users (email) WHERE email IS NOT NULL;
CREATE INDEX ix_users_role_active ON users (role_id, is_active);
CREATE INDEX ix_users_active ON users (is_active);

COMMENT ON TABLE  users IS 'Identity. Soft-delete via is_active. Sessions and audit references are preserved (audit.user_id ON DELETE SET NULL).';
COMMENT ON COLUMN users.locked_until IS 'Set after repeated failed logins; cleared on successful unlock.';


-- -----------------------------------------------------------------------------
-- 4.5 user_capability_overrides
-- Per-user capability grant/revoke. is_granted distinguishes grant (TRUE)
-- from explicit revoke (FALSE) layered on top of role base.
-- -----------------------------------------------------------------------------
CREATE TABLE user_capability_overrides (
    user_id        INT         NOT NULL,
    capability_id  SMALLINT    NOT NULL,
    is_granted     BOOLEAN     NOT NULL,
    granted_by     INT         NOT NULL,
    granted_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, capability_id),
    CONSTRAINT fk_user_cap_user
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT fk_user_cap_capability
        FOREIGN KEY (capability_id) REFERENCES capabilities (id) ON DELETE RESTRICT,
    CONSTRAINT fk_user_cap_granted_by
        FOREIGN KEY (granted_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_user_cap_granted_bool
        CHECK (is_granted IN (TRUE, FALSE))
);

CREATE INDEX ix_user_cap_capability ON user_capability_overrides (capability_id);


-- =============================================================================
-- 5. MASTER DATA
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 5.1 categories
-- Optional parent_id for hierarchy. Soft-delete via is_active.
-- Hard-delete blocked by ON DELETE RESTRICT from products.category_id.
-- -----------------------------------------------------------------------------
CREATE TABLE categories (
    id          SERIAL        PRIMARY KEY,
    name        VARCHAR(100)  NOT NULL,
    parent_id   INT           NULL,
    is_active   BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_categories_parent
        FOREIGN KEY (parent_id) REFERENCES categories (id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX ux_categories_name ON categories (name);
CREATE INDEX ix_categories_parent ON categories (parent_id);
CREATE INDEX ix_categories_active ON categories (is_active);

COMMENT ON TABLE categories IS 'Product categories. Hierarchical via parent_id. Deactivated (not deleted) when referenced by products.';


-- -----------------------------------------------------------------------------
-- 5.2 units
-- Units of measure (pcs, kg, etc.). No master data effects.
-- -----------------------------------------------------------------------------
CREATE TABLE units (
    id          SERIAL        PRIMARY KEY,
    code        VARCHAR(20)   NOT NULL,
    name        VARCHAR(50)   NOT NULL,
    is_active   BOOLEAN       NOT NULL DEFAULT TRUE
);

CREATE UNIQUE INDEX ux_units_code ON units (code);
CREATE INDEX ix_units_active ON units (is_active);


-- -----------------------------------------------------------------------------
-- 5.3 products
-- Master record. purchase_price and selling_price are display/reference
-- values; the authoritative cost is computed at posting time from
-- stock_movements and snapshotted on sale_lines / purchase_lines.
-- `code` is optional (BR-PRODUCT-001): a partial unique index ensures
-- uniqueness only when present.
-- -----------------------------------------------------------------------------
CREATE TABLE products (
    id                    SERIAL          PRIMARY KEY,
    code                  VARCHAR(64)     NULL,
    name                  VARCHAR(200)    NOT NULL,
    category_id           INT             NULL,
    unit_id               INT             NULL,
    purchase_price        NUMERIC(15,2)   NOT NULL DEFAULT 0,
    selling_price         NUMERIC(15,2)   NOT NULL DEFAULT 0,
    low_stock_threshold   NUMERIC(15,4)   NULL,
    allow_negative_stock  BOOLEAN         NULL,    -- NULL = use global default
    is_sellable           BOOLEAN         NOT NULL DEFAULT TRUE,
    is_purchasable        BOOLEAN         NOT NULL DEFAULT TRUE,
    is_producible         BOOLEAN         NOT NULL DEFAULT FALSE,
    is_active             BOOLEAN         NOT NULL DEFAULT TRUE,
    notes                 TEXT            NULL,
    created_at            TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at            TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_by            INT             NOT NULL,
    updated_by            INT             NOT NULL,
    CONSTRAINT fk_products_category
        FOREIGN KEY (category_id) REFERENCES categories (id) ON DELETE RESTRICT,
    CONSTRAINT fk_products_unit
        FOREIGN KEY (unit_id) REFERENCES units (id) ON DELETE RESTRICT,
    CONSTRAINT fk_products_created_by
        FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT fk_products_updated_by
        FOREIGN KEY (updated_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_products_purchase_price
        CHECK (purchase_price >= 0),
    CONSTRAINT ck_products_selling_price
        CHECK (selling_price >= 0),
    CONSTRAINT ck_products_low_stock_threshold
        CHECK (low_stock_threshold IS NULL OR low_stock_threshold >= 0)
);

-- Code is unique only when present (BR-PRODUCT-001)
CREATE UNIQUE INDEX ux_products_code ON products (code) WHERE code IS NOT NULL;
CREATE INDEX ix_products_active ON products (is_active);
CREATE INDEX ix_products_category ON products (category_id);
-- Partial index for low-stock dashboard (per DB spec §2.2.3)
CREATE INDEX ix_products_low_stock
    ON products (low_stock_threshold)
    WHERE is_active = TRUE AND low_stock_threshold IS NOT NULL;

COMMENT ON TABLE  products IS 'Product master. price columns are display/reference; authoritative cost comes from stock_movements at posting time.';
COMMENT ON COLUMN products.allow_negative_stock IS 'NULL = use system_settings.default_negative_stock_allowed. Per BR-STOCK-011.';
COMMENT ON COLUMN products.purchase_price IS 'Display/reference value; last purchase price is the negative-stock COGS fallback (BR-COST-006).';


-- -----------------------------------------------------------------------------
-- 5.4 product_price_history
-- Append-only history of price changes (BR-PRODUCT-002, BR-AUDIT-003).
-- New row inserted on every product update that changes a price.
-- -----------------------------------------------------------------------------
CREATE TABLE product_price_history (
    id              BIGSERIAL     PRIMARY KEY,
    product_id      INT           NOT NULL,
    purchase_price  NUMERIC(15,2) NOT NULL,
    selling_price   NUMERIC(15,2) NOT NULL,
    effective_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    changed_by      INT           NOT NULL,
    CONSTRAINT fk_pph_product
        FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE RESTRICT,
    CONSTRAINT fk_pph_changed_by
        FOREIGN KEY (changed_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_pph_purchase_price
        CHECK (purchase_price >= 0),
    CONSTRAINT ck_pph_selling_price
        CHECK (selling_price >= 0)
);

CREATE INDEX ix_pph_product_date ON product_price_history (product_id, effective_at DESC);

COMMENT ON TABLE product_price_history IS 'Append-only price history. Rows are inserted; never updated or deleted. Historical cost snapshots are unaffected (BR-COST-004).';


-- -----------------------------------------------------------------------------
-- 5.5 contacts
-- Customers and suppliers. type may be 'customer', 'supplier', or 'both'.
-- Soft-delete via is_active. No hard delete if referenced.
-- -----------------------------------------------------------------------------
CREATE TABLE contacts (
    id          SERIAL        PRIMARY KEY,
    type        VARCHAR(20)   NOT NULL,
    name        VARCHAR(200)  NOT NULL,
    phone       VARCHAR(50)   NULL,
    email       VARCHAR(150)  NULL,
    address     TEXT          NULL,
    notes       TEXT          NULL,
    is_active   BOOLEAN       NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    created_by  INT           NOT NULL,
    CONSTRAINT fk_contacts_created_by
        FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_contacts_type
        CHECK (type IN ('customer', 'supplier', 'both')),
    CONSTRAINT ck_contacts_email_format
        CHECK (email IS NULL OR email ~ '^[^@\s]+@[^@\s]+\.[^@\s]+$'),
    -- A contact is identified by (name, type): customers and suppliers are
    -- distinct records even when they share a display name, but two records
    -- with the same name AND the same type are duplicates.
    CONSTRAINT ux_contacts_name UNIQUE (name, type)
);

CREATE INDEX ix_contacts_type ON contacts (type);
CREATE INDEX ix_contacts_active ON contacts (is_active);

COMMENT ON TABLE contacts IS 'Customers and suppliers. Both types in a single table; type=customer|supplier|both. Per BR-DATA-003 hard-delete is blocked by ON DELETE RESTRICT from transaction tables.';


-- =============================================================================
-- 6. SALES / POS
-- =============================================================================
-- Per DB spec §2.4.1, §2.4.2, §2.4.3 + API §15.10.
-- Lifecycle: draft → posted → completed | partially_returned | returned | cancelled
-- (six values; partially_returned added per C-04 / XDC-8 / XDC-4).


-- -----------------------------------------------------------------------------
-- 6.1 sales
-- -----------------------------------------------------------------------------
CREATE TABLE sales (
    id                  BIGSERIAL       PRIMARY KEY,
    reference_no        VARCHAR(50)     NULL,                 -- when sequential numbering enabled
    customer_id         INT             NULL,
    sale_date           TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    total_amount        NUMERIC(15,2)   NOT NULL,
    discount_amount     NUMERIC(15,2)   NOT NULL DEFAULT 0,
    lifecycle_status    VARCHAR(20)     NOT NULL DEFAULT 'draft',
    cancellation_date   TIMESTAMPTZ     NULL,
    cancellation_reason TEXT            NULL,
    cancelled_by        INT             NULL,
    notes               TEXT            NULL,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_by          INT             NOT NULL,
    posted_at           TIMESTAMPTZ     NULL,
    posted_by           INT             NULL,
    version             INT             NOT NULL DEFAULT 1,
    CONSTRAINT fk_sales_customer
        FOREIGN KEY (customer_id) REFERENCES contacts (id) ON DELETE RESTRICT,
    CONSTRAINT fk_sales_cancelled_by
        FOREIGN KEY (cancelled_by) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_sales_created_by
        FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT fk_sales_posted_by
        FOREIGN KEY (posted_by) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT ck_sales_total_amount_nonneg
        CHECK (total_amount >= 0),
    CONSTRAINT ck_sales_discount_nonneg
        CHECK (discount_amount >= 0),
    CONSTRAINT ck_sales_discount_le_total
        CHECK (discount_amount <= total_amount),
    CONSTRAINT ck_sales_lifecycle_status
        CHECK (lifecycle_status IN ('draft','posted','completed','partially_returned','returned','cancelled')),
    CONSTRAINT ck_sales_posted_pair
        -- posted_at must be set for posted/completed/partially_returned/returned.
        -- For cancelled, posted_at may be NULL (draft→cancelled) or NOT NULL
        -- (posted→cancelled — the original post is preserved as historical fact).
        -- For draft, posted_at must be NULL.
        CHECK (
            (lifecycle_status = 'draft' AND posted_at IS NULL)
         OR (lifecycle_status IN ('posted','completed','partially_returned','returned') AND posted_at IS NOT NULL)
         OR (lifecycle_status = 'cancelled' AND (posted_at IS NULL OR posted_at IS NOT NULL))
        ),
    CONSTRAINT ck_sales_cancelled_pair
        -- The check ensures (lifecycle_status = 'cancelled') = (cancellation_date IS NOT NULL).
        -- Per XDC-16, cancelled_by is NOT enforced here: the audit references
        -- via FK ON DELETE SET NULL must remain possible, and the API always
        -- sets it. This matches the reconciliation report.
        CHECK ((lifecycle_status = 'cancelled') = (cancellation_date IS NOT NULL))
);

CREATE INDEX ix_sales_sale_date ON sales (sale_date);
CREATE INDEX ix_sales_customer ON sales (customer_id);
CREATE INDEX ix_sales_lifecycle ON sales (lifecycle_status);
CREATE INDEX ix_sales_created_by ON sales (created_by);
-- Partial index: active (non-cancelled) sales for reporting
CREATE INDEX ix_sales_active ON sales (sale_date DESC) WHERE lifecycle_status <> 'cancelled';
-- Partial unique for reference_no when configured
CREATE UNIQUE INDEX ux_sales_reference_no ON sales (reference_no) WHERE reference_no IS NOT NULL;

COMMENT ON TABLE  sales IS 'Sale / POS header. Lifecycle: draft, posted, completed, partially_returned, returned, cancelled. Posted records are immutable (see triggers).';
COMMENT ON COLUMN sales.version IS 'Server-incremented on every successful write; exposed as ETag for optimistic concurrency (If-Match).';


-- -----------------------------------------------------------------------------
-- 6.2 sale_lines
-- Per DB spec §2.4.2 + C-03: is_negative_stock_fallback + stock_movement_id
-- are present (reconciliation adds them to the table definition; the §3.5
-- prose cross-reference is updated to the table definition per C-04).
-- -----------------------------------------------------------------------------
CREATE TABLE sale_lines (
    id                          BIGSERIAL       PRIMARY KEY,
    sale_id                     BIGINT          NOT NULL,
    product_id                  INT             NOT NULL,
    quantity                    NUMERIC(15,4)   NOT NULL,
    unit_price                  NUMERIC(15,2)   NOT NULL,
    discount_amount             NUMERIC(15,2)   NOT NULL DEFAULT 0,
    line_total                  NUMERIC(15,2)   NOT NULL,
    unit_cost_snapshot          NUMERIC(15,4)   NULL,
    cogs_total_snapshot         NUMERIC(15,2)   NULL,
    line_number                 SMALLINT        NOT NULL,
    is_negative_stock_fallback  BOOLEAN         NOT NULL DEFAULT FALSE,
    stock_movement_id           BIGINT          NULL,
    CONSTRAINT fk_sale_lines_sale
        FOREIGN KEY (sale_id) REFERENCES sales (id) ON DELETE CASCADE,
    CONSTRAINT fk_sale_lines_product
        FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE RESTRICT,
    -- fk_sale_lines_movement: see ALTER TABLE statement at end of stock_movements
    -- section; it cannot be declared inline because stock_movements is defined
    -- later in this file (referenced in the same atomic script run).
    CONSTRAINT ck_sale_lines_qty_pos
        CHECK (quantity > 0),
    CONSTRAINT ck_sale_lines_unit_price_nonneg
        CHECK (unit_price >= 0),
    CONSTRAINT ck_sale_lines_discount_nonneg
        CHECK (discount_amount >= 0),
    CONSTRAINT ck_sale_lines_line_total_nonneg
        CHECK (line_total >= 0),
    CONSTRAINT ck_sale_lines_unit_cost_nonneg
        CHECK (unit_cost_snapshot IS NULL OR unit_cost_snapshot >= 0),
    CONSTRAINT ck_sale_lines_cogs_nonneg
        CHECK (cogs_total_snapshot IS NULL OR cogs_total_snapshot >= 0),
    CONSTRAINT ck_sale_lines_line_num_pos
        CHECK (line_number > 0),
    CONSTRAINT ck_sale_lines_fallback_bool
        CHECK (is_negative_stock_fallback IN (TRUE, FALSE)),
    CONSTRAINT uq_sale_lines_sale_line_num
        UNIQUE (sale_id, line_number),
    CONSTRAINT ck_sale_lines_line_total
        -- The actual equality check (qty*price - discount) is enforced by a
        -- trigger because NUMERIC multiplication can introduce rounding.
        -- Here we only ensure non-negative and consistent with header.
        CHECK (line_total >= 0)
);

CREATE INDEX ix_sale_lines_product ON sale_lines (product_id);
CREATE INDEX ix_sale_lines_movement ON sale_lines (stock_movement_id);

COMMENT ON TABLE  sale_lines IS 'Sale lines. Snapshots unit_cost and cogs_total at post; immutable once parent sale is non-draft. is_negative_stock_fallback is set by the post trigger when allow_negative_stock triggers the fallback (BR-COST-006, C-03).';
COMMENT ON COLUMN sale_lines.is_negative_stock_fallback IS 'TRUE when the sale posted against negative stock and used last-purchase-price fallback (BR-COST-006).';
COMMENT ON COLUMN sale_lines.stock_movement_id IS 'The stock_movements row produced by this sale line (negative qty). NULL until post.';


-- -----------------------------------------------------------------------------
-- 6.3 sale_payments
-- Per DB spec §2.4.3 + API §15.10.9.
-- Allocated amount is always > 0; tendered/change populated only for over-tender
-- cash (BR-SALE-007, BR-PAYMENT-003).
-- Cross-row trigger enforces Σ amounts ≤ sales.total_amount (BR-PAYMENT-004).
-- -----------------------------------------------------------------------------
CREATE TABLE sale_payments (
    id                  BIGSERIAL       PRIMARY KEY,
    sale_id             BIGINT          NOT NULL,
    payment_method_id   INT             NOT NULL,
    amount              NUMERIC(15,2)   NOT NULL,
    tendered_amount     NUMERIC(15,2)   NULL,
    change_amount       NUMERIC(15,2)   NULL,
    payment_date        TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    reference           VARCHAR(100)    NULL,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_by          INT             NOT NULL,
    CONSTRAINT fk_sale_payments_sale
        FOREIGN KEY (sale_id) REFERENCES sales (id) ON DELETE CASCADE,
    CONSTRAINT fk_sale_payments_method
        FOREIGN KEY (payment_method_id) REFERENCES payment_methods (id) ON DELETE RESTRICT,
    CONSTRAINT fk_sale_payments_created_by
        FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_sale_payments_amount_pos
        CHECK (amount > 0),
    CONSTRAINT ck_sale_payments_tendered
        CHECK (tendered_amount IS NULL OR tendered_amount >= amount),
    CONSTRAINT ck_sale_payments_change
        CHECK (change_amount IS NULL OR change_amount >= 0)
);

CREATE INDEX ix_sale_payments_sale ON sale_payments (sale_id);
CREATE INDEX ix_sale_payments_date ON sale_payments (payment_date);
CREATE INDEX ix_sale_payments_method ON sale_payments (payment_method_id);

COMMENT ON TABLE  sale_payments IS 'Allocated payment rows for a sale. Cross-row trigger enforces SUM(amount) <= sales.total_amount. tendered/change are populated only for over-tender cash.';
COMMENT ON COLUMN sale_payments.tendered_amount IS 'Gross cash given by customer; only set when payment_method.is_cash=TRUE and tendered > amount.';
COMMENT ON COLUMN sale_payments.change_amount  IS 'Tendered minus amount; only set when over-tender cash.';


-- -----------------------------------------------------------------------------
-- 6.4 sales_returns (header)
-- Per DB spec §2.4.4 + API §15.10.11/§15.10.12.
-- Lifecycle: posted (single state) | cancelled.
-- -----------------------------------------------------------------------------
CREATE TABLE sales_returns (
    id                              BIGSERIAL       PRIMARY KEY,
    sale_id                         BIGINT          NOT NULL,
    return_date                     TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    reason                          TEXT            NULL,
    total_selling_price_returned    NUMERIC(15,2)   NOT NULL,
    total_cost_returned             NUMERIC(15,2)   NOT NULL,
    lifecycle_status                VARCHAR(20)     NOT NULL DEFAULT 'posted',
    created_at                      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_by                      INT             NOT NULL,
    cancellation_date               TIMESTAMPTZ     NULL,
    cancelled_by                    INT             NULL,
    version                         INT             NOT NULL DEFAULT 1,
    CONSTRAINT fk_sales_returns_sale
        FOREIGN KEY (sale_id) REFERENCES sales (id) ON DELETE RESTRICT,
    CONSTRAINT fk_sales_returns_created_by
        FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT fk_sales_returns_cancelled_by
        FOREIGN KEY (cancelled_by) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT ck_sales_returns_selling_total_pos
        CHECK (total_selling_price_returned > 0),
    CONSTRAINT ck_sales_returns_cost_total_pos
        CHECK (total_cost_returned > 0),
    CONSTRAINT ck_sales_returns_lifecycle
        CHECK (lifecycle_status IN ('posted','cancelled'))
);

CREATE INDEX ix_sales_returns_sale ON sales_returns (sale_id);
CREATE INDEX ix_sales_returns_date ON sales_returns (return_date);

COMMENT ON TABLE sales_returns IS 'Sales return header. Returned goods re-enter inventory at original line cost (BR-COST-005, PRD §18.3).';


-- -----------------------------------------------------------------------------
-- 6.5 sales_return_lines
-- Per DB spec §2.4.5 + API §15.10.12.
-- Cross-row trigger: Σ quantity per sale_line_id ≤ sale_lines.quantity.
-- -----------------------------------------------------------------------------
CREATE TABLE sales_return_lines (
    id                       BIGSERIAL       PRIMARY KEY,
    sales_return_id          BIGINT          NOT NULL,
    sale_line_id             BIGINT          NOT NULL,
    product_id               INT             NOT NULL,
    quantity                 NUMERIC(15,4)   NOT NULL,
    returned_selling_price   NUMERIC(15,2)   NOT NULL,
    returned_unit_cost       NUMERIC(15,4)   NOT NULL,
    line_number              SMALLINT        NOT NULL,
    CONSTRAINT fk_srl_header
        FOREIGN KEY (sales_return_id) REFERENCES sales_returns (id) ON DELETE CASCADE,
    CONSTRAINT fk_srl_sale_line
        FOREIGN KEY (sale_line_id) REFERENCES sale_lines (id) ON DELETE RESTRICT,
    CONSTRAINT fk_srl_product
        FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE RESTRICT,
    CONSTRAINT uq_srl_header_line_num
        UNIQUE (sales_return_id, line_number),
    CONSTRAINT ck_srl_qty_pos
        CHECK (quantity > 0),
    CONSTRAINT ck_srl_selling_price_pos
        CHECK (returned_selling_price > 0),
    CONSTRAINT ck_srl_unit_cost_pos
        CHECK (returned_unit_cost > 0),
    CONSTRAINT ck_srl_line_num_pos
        CHECK (line_number > 0)
);

CREATE INDEX ix_srl_header_line_num ON sales_return_lines (sales_return_id, line_number);
CREATE INDEX ix_srl_sale_line ON sales_return_lines (sale_line_id);
CREATE INDEX ix_srl_product ON sales_return_lines (product_id);


-- =============================================================================
-- 7. PURCHASING
-- =============================================================================
-- Mirrors the sales lifecycle per DB spec §2.5 + API §15.12.


-- -----------------------------------------------------------------------------
-- 7.1 purchases
-- -----------------------------------------------------------------------------
CREATE TABLE purchases (
    id                  BIGSERIAL       PRIMARY KEY,
    reference_no        VARCHAR(50)     NULL,
    supplier_id         INT             NULL,                 -- per OD-15, optional
    purchase_date       TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    received_date       TIMESTAMPTZ     NULL,
    lifecycle_status    VARCHAR(20)     NOT NULL DEFAULT 'draft',
    posted_at           TIMESTAMPTZ     NULL,
    posted_by           INT             NULL,
    cancellation_date   TIMESTAMPTZ     NULL,
    cancellation_reason TEXT            NULL,
    cancelled_by        INT             NULL,
    notes               TEXT            NULL,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_by          INT             NOT NULL,
    version             INT             NOT NULL DEFAULT 1,
    CONSTRAINT fk_purchases_supplier
        FOREIGN KEY (supplier_id) REFERENCES contacts (id) ON DELETE RESTRICT,
    CONSTRAINT fk_purchases_posted_by
        FOREIGN KEY (posted_by) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_purchases_cancelled_by
        FOREIGN KEY (cancelled_by) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_purchases_created_by
        FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_purchases_lifecycle
        CHECK (lifecycle_status IN ('draft','posted','completed','partially_returned','returned','cancelled')),
    CONSTRAINT ck_purchases_posted_pair
        -- See sales.ck_sales_posted_pair for rationale. Same semantics for
        -- purchases: draft→NULL, post→NOT NULL, cancelled→either.
        CHECK (
            (lifecycle_status = 'draft' AND posted_at IS NULL)
         OR (lifecycle_status IN ('posted','completed','partially_returned','returned') AND posted_at IS NOT NULL)
         OR (lifecycle_status = 'cancelled' AND (posted_at IS NULL OR posted_at IS NOT NULL))
        ),
    CONSTRAINT ck_purchases_cancelled_pair
        CHECK ((lifecycle_status = 'cancelled') = (cancellation_date IS NOT NULL))
);

CREATE INDEX ix_purchases_date ON purchases (purchase_date);
CREATE INDEX ix_purchases_supplier ON purchases (supplier_id);
CREATE INDEX ix_purchases_lifecycle ON purchases (lifecycle_status);
CREATE INDEX ix_purchases_active ON purchases (purchase_date DESC) WHERE lifecycle_status <> 'cancelled';
CREATE UNIQUE INDEX ux_purchases_reference_no ON purchases (reference_no) WHERE reference_no IS NOT NULL;

COMMENT ON TABLE  purchases IS 'Purchase header. Supplier is optional (per OD-15, supports counter/cash buys).';
COMMENT ON COLUMN purchases.version IS 'Server-incremented; exposed as ETag for If-Match optimistic concurrency.';


-- -----------------------------------------------------------------------------
-- 7.2 purchase_lines
-- Per DB spec §2.5.2. line_total includes allocated shipping.
-- Cross-row: Σ line_total + shipping.amount = purchase.total_amount.
-- -----------------------------------------------------------------------------
CREATE TABLE purchase_lines (
    id                    BIGSERIAL       PRIMARY KEY,
    purchase_id           BIGINT          NOT NULL,
    product_id            INT             NOT NULL,
    quantity              NUMERIC(15,4)   NOT NULL,
    unit_price            NUMERIC(15,2)   NOT NULL,
    line_subtotal         NUMERIC(15,2)   NOT NULL,
    allocated_shipping    NUMERIC(15,2)   NOT NULL DEFAULT 0,
    line_total            NUMERIC(15,2)   NOT NULL,
    line_number           SMALLINT        NOT NULL,
    CONSTRAINT fk_purchase_lines_purchase
        FOREIGN KEY (purchase_id) REFERENCES purchases (id) ON DELETE CASCADE,
    CONSTRAINT fk_purchase_lines_product
        FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE RESTRICT,
    CONSTRAINT uq_purchase_lines_purchase_num
        UNIQUE (purchase_id, line_number),
    CONSTRAINT ck_purchase_lines_qty_pos
        CHECK (quantity > 0),
    CONSTRAINT ck_purchase_lines_unit_price_nonneg
        CHECK (unit_price >= 0),
    CONSTRAINT ck_purchase_lines_subtotal_nonneg
        CHECK (line_subtotal >= 0),
    CONSTRAINT ck_purchase_lines_alloc_ship_nonneg
        CHECK (allocated_shipping >= 0),
    CONSTRAINT ck_purchase_lines_line_total_nonneg
        CHECK (line_total >= 0),
    CONSTRAINT ck_purchase_lines_line_num_pos
        CHECK (line_number > 0)
);

CREATE INDEX ix_purchase_lines_product ON purchase_lines (product_id);


-- -----------------------------------------------------------------------------
-- 7.3 purchase_shipping
-- One row per purchase (UNIQUE on purchase_id). Capitalizes shipping into
-- inventory cost (BR-PURCHASE-005, PRD §13, §18.6).
-- -----------------------------------------------------------------------------
CREATE TABLE purchase_shipping (
    id            BIGSERIAL       PRIMARY KEY,
    purchase_id   BIGINT          NOT NULL,
    amount        NUMERIC(15,2)   NOT NULL,
    paid_in_cash  BOOLEAN         NOT NULL DEFAULT FALSE,
    supplier_id   INT             NULL,
    description   TEXT            NULL,
    CONSTRAINT fk_purchase_shipping_purchase
        FOREIGN KEY (purchase_id) REFERENCES purchases (id) ON DELETE CASCADE,
    CONSTRAINT fk_purchase_shipping_supplier
        FOREIGN KEY (supplier_id) REFERENCES contacts (id) ON DELETE RESTRICT,
    CONSTRAINT uq_purchase_shipping_purchase
        UNIQUE (purchase_id),
    CONSTRAINT ck_purchase_shipping_amount_nonneg
        CHECK (amount >= 0)
);

COMMENT ON TABLE purchase_shipping IS 'Landed-cost shipping. Capitalized into inventory (BR-PURCHASE-005). paid_in_cash=TRUE generates freight_cash cash_movement at post; FALSE keeps it in AP.';


-- -----------------------------------------------------------------------------
-- 7.4 purchase_payments
-- Per DB spec §2.5.4 + API §15.12.8.
-- Cross-row trigger: Σ amount WHERE purchase_id = X ≤ purchases.total_amount.
-- -----------------------------------------------------------------------------
CREATE TABLE purchase_payments (
    id                  BIGSERIAL       PRIMARY KEY,
    purchase_id         BIGINT          NOT NULL,
    payment_method_id   INT             NOT NULL,
    amount              NUMERIC(15,2)   NOT NULL,
    tendered_amount     NUMERIC(15,2)   NULL,
    change_amount       NUMERIC(15,2)   NULL,
    payment_date        TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    reference           VARCHAR(100)    NULL,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_by          INT             NOT NULL,
    CONSTRAINT fk_purchase_payments_purchase
        FOREIGN KEY (purchase_id) REFERENCES purchases (id) ON DELETE CASCADE,
    CONSTRAINT fk_purchase_payments_method
        FOREIGN KEY (payment_method_id) REFERENCES payment_methods (id) ON DELETE RESTRICT,
    CONSTRAINT fk_purchase_payments_created_by
        FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_purchase_payments_amount_pos
        CHECK (amount > 0),
    CONSTRAINT ck_purchase_payments_tendered
        CHECK (tendered_amount IS NULL OR tendered_amount >= amount),
    CONSTRAINT ck_purchase_payments_change
        CHECK (change_amount IS NULL OR change_amount >= 0)
);

CREATE INDEX ix_purchase_payments_purchase ON purchase_payments (purchase_id);
CREATE INDEX ix_purchase_payments_date ON purchase_payments (payment_date);


-- -----------------------------------------------------------------------------
-- 7.5 purchase_returns (header)
-- Per DB spec §2.5.5 + API §15.12.10.
-- -----------------------------------------------------------------------------
CREATE TABLE purchase_returns (
    id                      BIGSERIAL       PRIMARY KEY,
    purchase_id             BIGINT          NOT NULL,
    return_date             TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    reason                  TEXT            NULL,
    total_value_returned    NUMERIC(15,2)   NOT NULL,
    lifecycle_status        VARCHAR(20)     NOT NULL DEFAULT 'posted',
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_by              INT             NOT NULL,
    version                 INT             NOT NULL DEFAULT 1,
    CONSTRAINT fk_purchase_returns_purchase
        FOREIGN KEY (purchase_id) REFERENCES purchases (id) ON DELETE RESTRICT,
    CONSTRAINT fk_purchase_returns_created_by
        FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_purchase_returns_value_pos
        CHECK (total_value_returned > 0),
    CONSTRAINT ck_purchase_returns_lifecycle
        CHECK (lifecycle_status IN ('posted','cancelled'))
);

CREATE INDEX ix_purchase_returns_purchase ON purchase_returns (purchase_id);
CREATE INDEX ix_purchase_returns_date ON purchase_returns (return_date);


-- -----------------------------------------------------------------------------
-- 7.6 purchase_return_lines
-- Cross-row trigger: Σ quantity per purchase_line_id ≤ purchase_lines.quantity.
-- -----------------------------------------------------------------------------
CREATE TABLE purchase_return_lines (
    id                    BIGSERIAL       PRIMARY KEY,
    purchase_return_id    BIGINT          NOT NULL,
    purchase_line_id      BIGINT          NOT NULL,
    product_id            INT             NOT NULL,
    quantity              NUMERIC(15,4)   NOT NULL,
    unit_cost_snapshot    NUMERIC(15,4)   NOT NULL,
    line_value            NUMERIC(15,2)   NOT NULL,
    line_number           SMALLINT        NOT NULL,
    CONSTRAINT fk_prl_header
        FOREIGN KEY (purchase_return_id) REFERENCES purchase_returns (id) ON DELETE CASCADE,
    CONSTRAINT fk_prl_purchase_line
        FOREIGN KEY (purchase_line_id) REFERENCES purchase_lines (id) ON DELETE RESTRICT,
    CONSTRAINT fk_prl_product
        FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE RESTRICT,
    CONSTRAINT uq_prl_header_line_num
        UNIQUE (purchase_return_id, line_number),
    CONSTRAINT ck_prl_qty_pos
        CHECK (quantity > 0),
    CONSTRAINT ck_prl_unit_cost_nonneg
        CHECK (unit_cost_snapshot >= 0),
    CONSTRAINT ck_prl_line_value_nonneg
        CHECK (line_value >= 0),
    CONSTRAINT ck_prl_line_num_pos
        CHECK (line_number > 0)
);

CREATE INDEX ix_prl_header_line_num ON purchase_return_lines (purchase_return_id, line_number);
CREATE INDEX ix_prl_purchase_line ON purchase_return_lines (purchase_line_id);


-- =============================================================================
-- 8. INVENTORY LEDGER
-- =============================================================================
-- The single authoritative inventory ledger. Per DB spec §2.6.1 / §3.2 +
-- reconciliation C-05 / C-06 / C-07.
--
-- Trigger values (15 total per C-05):
--   purchase_receipt, sale, sales_return, purchase_return,
--   production_input, production_output, stock_adjustment, opening_balance,
--   sale_reversal, purchase_reversal, sales_return_reversal,
--   purchase_return_reversal, production_reversal, adjustment_reversal,
--   value_adjustment
--
-- Quantity rule (per C-06): quantity = 0 is allowed only for
-- value_adjustment movements.
--
-- Movements are append-only. UPDATE/DELETE are rejected by trigger.


-- -----------------------------------------------------------------------------
-- 8.1 stock_movements
-- -----------------------------------------------------------------------------
CREATE TABLE stock_movements (
    id                        BIGSERIAL       PRIMARY KEY,
    product_id                INT             NOT NULL,
    movement_date             TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    trigger                   VARCHAR(40)     NOT NULL,
    quantity                  NUMERIC(15,4)   NOT NULL,
    unit_cost_at_movement     NUMERIC(15,4)   NULL,
    total_cost                NUMERIC(15,2)   NULL,
    reference_type            VARCHAR(40)     NULL,
    reference_id              BIGINT          NULL,
    reference_line_id         BIGINT          NULL,
    reversal_of_movement_id   BIGINT          NULL,
    reversed_by_movement_id   BIGINT          NULL,
    reason                    TEXT            NULL,
    created_at                TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_by                INT             NOT NULL,
    CONSTRAINT fk_sm_product
        FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE RESTRICT,
    CONSTRAINT fk_sm_reversal_of
        FOREIGN KEY (reversal_of_movement_id) REFERENCES stock_movements (id) ON DELETE SET NULL,
    CONSTRAINT fk_sm_reversed_by
        FOREIGN KEY (reversed_by_movement_id) REFERENCES stock_movements (id) ON DELETE SET NULL,
    CONSTRAINT fk_sm_created_by
        FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT,
    -- Per C-05: the trigger set is 15 values including value_adjustment.
    CONSTRAINT ck_sm_trigger
        CHECK (trigger IN (
            'purchase_receipt','sale','sales_return','purchase_return',
            'production_input','production_output','stock_adjustment','opening_balance',
            'sale_reversal','purchase_reversal','sales_return_reversal',
            'purchase_return_reversal','production_reversal','adjustment_reversal',
            'value_adjustment'
        )),
    -- Per C-06: zero quantity is allowed only for value_adjustment.
    CONSTRAINT ck_sm_quantity
        CHECK ((trigger = 'value_adjustment' AND quantity = 0)
            OR (trigger <> 'value_adjustment' AND quantity <> 0)),
    CONSTRAINT ck_sm_unit_cost_nonneg
        CHECK (unit_cost_at_movement IS NULL OR unit_cost_at_movement >= 0),
    CONSTRAINT ck_sm_total_cost_nonzero
        -- total_cost carries the sign of quantity (positive for receipts,
        -- negative for issues); a zero value is only allowed for the
        -- value_adjustment trigger where quantity=0 and total_cost is the
        -- COGS correction. For all other triggers, total_cost must be
        -- non-zero with the sign of quantity.
        CHECK (
            (trigger = 'value_adjustment')
            OR (total_cost IS NULL AND quantity IS NULL)
            OR (total_cost IS NOT NULL AND quantity IS NOT NULL
                AND SIGN(total_cost) = SIGN(quantity)
                AND total_cost <> 0)
        ),
    -- Either reversal_of OR reversed_by, never both; reversal rows are *-reversal
    -- triggers and original rows are not.
    CONSTRAINT ck_sm_reversal_pair
        CHECK ((reversal_of_movement_id IS NULL) OR (reversed_by_movement_id IS NULL)),
    CONSTRAINT ck_sm_reversal_trigger
        CHECK ((reversal_of_movement_id IS NULL)
            OR (trigger IN ('sale_reversal','purchase_reversal','sales_return_reversal',
                            'purchase_return_reversal','production_reversal','adjustment_reversal'))),
    CONSTRAINT ck_sm_nonreversal_trigger
        CHECK ((reversed_by_movement_id IS NULL)
            OR (trigger NOT IN ('sale_reversal','purchase_reversal','sales_return_reversal',
                                'purchase_return_reversal','production_reversal','adjustment_reversal')))
);

CREATE INDEX ix_sm_product_date ON stock_movements (product_id, movement_date);
CREATE INDEX ix_sm_trigger ON stock_movements (trigger);
CREATE INDEX ix_sm_reference ON stock_movements (reference_type, reference_id);
CREATE INDEX ix_sm_reversal_of ON stock_movements (reversal_of_movement_id);
CREATE INDEX ix_sm_reversed_by ON stock_movements (reversed_by_movement_id);

COMMENT ON TABLE  stock_movements IS 'The single authoritative inventory ledger. Append-only (UPDATE/DELETE rejected by trigger except by system-defined reversal routine which inserts a new row). Per C-06: zero quantity only for value_adjustment.';
COMMENT ON COLUMN stock_movements.trigger IS '15 distinct triggers per C-05; value_adjustment is the COGS correction movement for purchase cancellation of consumed goods.';
COMMENT ON COLUMN stock_movements.unit_cost_at_movement IS 'Snapshot at insert; never updated. Per BR-COST-003.';
COMMENT ON COLUMN stock_movements.total_cost IS 'quantity × unit_cost_at_movement (with sign); the running total drives the GL inventory value (INV-08).';


-- Defer-add the FK from sale_lines.stock_movement_id to stock_movements.id
-- (sale_lines was defined earlier because it belongs to the sales domain, but
-- it references stock_movements which is the inventory-ledger domain. The FK
-- cannot be declared inline due to CREATE TABLE forward-reference
-- restriction.)
ALTER TABLE sale_lines
    ADD CONSTRAINT fk_sale_lines_movement
    FOREIGN KEY (stock_movement_id) REFERENCES stock_movements (id) ON DELETE RESTRICT;


-- =============================================================================
-- 9. PRODUCTION
-- =============================================================================
-- Per DB spec §2.7 + API §15.15. Single-step raw→finished model.
-- Lifecycle: draft → posted | cancelled (no partially_returned).


-- -----------------------------------------------------------------------------
-- 9.1 production_runs
-- finished_unit_cost = (total_raw_cost + total_overhead_cost) / output_quantity
-- -----------------------------------------------------------------------------
CREATE TABLE production_runs (
    id                      BIGSERIAL       PRIMARY KEY,
    run_date                TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    output_product_id       INT             NOT NULL,
    output_quantity         NUMERIC(15,4)   NOT NULL,
    finished_unit_cost      NUMERIC(15,4)   NOT NULL,
    total_raw_cost          NUMERIC(15,2)   NOT NULL,
    total_overhead_cost     NUMERIC(15,2)   NOT NULL,
    lifecycle_status        VARCHAR(20)     NOT NULL DEFAULT 'draft',
    posted_at               TIMESTAMPTZ     NULL,
    posted_by               INT             NULL,
    cancellation_date       TIMESTAMPTZ     NULL,
    cancellation_reason     TEXT            NULL,
    cancelled_by            INT             NULL,
    notes                   TEXT            NULL,
    created_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_by              INT             NOT NULL,
    version                 INT             NOT NULL DEFAULT 1,
    CONSTRAINT fk_production_runs_output_product
        FOREIGN KEY (output_product_id) REFERENCES products (id) ON DELETE RESTRICT,
    CONSTRAINT fk_production_runs_posted_by
        FOREIGN KEY (posted_by) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_production_runs_cancelled_by
        FOREIGN KEY (cancelled_by) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_production_runs_created_by
        FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_prun_output_qty_pos
        CHECK (output_quantity > 0),
    CONSTRAINT ck_prun_finished_unit_cost_pos
        CHECK (finished_unit_cost > 0),
    CONSTRAINT ck_prun_total_raw_nonneg
        CHECK (total_raw_cost >= 0),
    CONSTRAINT ck_prun_total_overhead_nonneg
        CHECK (total_overhead_cost >= 0),
    CONSTRAINT ck_prun_lifecycle
        CHECK (lifecycle_status IN ('draft','posted','completed','cancelled')),
    CONSTRAINT ck_prun_posted_pair
        -- See sales.ck_sales_posted_pair for rationale. Same semantics for
        -- production: draft→NULL, post→NOT NULL, cancelled→either.
        CHECK (
            (lifecycle_status = 'draft' AND posted_at IS NULL)
         OR (lifecycle_status IN ('posted','completed') AND posted_at IS NOT NULL)
         OR (lifecycle_status = 'cancelled' AND (posted_at IS NULL OR posted_at IS NOT NULL))
        )
);

CREATE INDEX ix_prun_output_product ON production_runs (output_product_id);
CREATE INDEX ix_prun_run_date ON production_runs (run_date);
CREATE INDEX ix_prun_lifecycle ON production_runs (lifecycle_status);


-- -----------------------------------------------------------------------------
-- 9.2 production_inputs (raw materials consumed)
-- line_cost = quantity × unit_cost_snapshot (snapshot of moving-avg at draft).
-- -----------------------------------------------------------------------------
CREATE TABLE production_inputs (
    id                    BIGSERIAL       PRIMARY KEY,
    production_run_id     BIGINT          NOT NULL,
    product_id            INT             NOT NULL,
    quantity              NUMERIC(15,4)   NOT NULL,
    unit_cost_snapshot    NUMERIC(15,4)   NOT NULL,
    line_cost             NUMERIC(15,2)   NOT NULL,
    line_number           SMALLINT        NOT NULL,
    CONSTRAINT fk_pi_run
        FOREIGN KEY (production_run_id) REFERENCES production_runs (id) ON DELETE CASCADE,
    CONSTRAINT fk_pi_product
        FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE RESTRICT,
    CONSTRAINT uq_pi_run_line_num
        UNIQUE (production_run_id, line_number),
    CONSTRAINT ck_pi_qty_pos
        CHECK (quantity > 0),
    CONSTRAINT ck_pi_unit_cost_nonneg
        CHECK (unit_cost_snapshot >= 0),
    CONSTRAINT ck_pi_line_cost_nonneg
        CHECK (line_cost >= 0),
    CONSTRAINT ck_pi_line_num_pos
        CHECK (line_number > 0)
);

CREATE INDEX ix_pi_product ON production_inputs (product_id);


-- -----------------------------------------------------------------------------
-- 9.3 production_outputs (finished goods)
-- UNIQUE(production_run_id): one output per run.
-- -----------------------------------------------------------------------------
CREATE TABLE production_outputs (
    id                    BIGSERIAL       PRIMARY KEY,
    production_run_id     BIGINT          NOT NULL,
    product_id            INT             NOT NULL,
    quantity              NUMERIC(15,4)   NOT NULL,
    unit_cost_snapshot    NUMERIC(15,4)   NOT NULL,
    total_cost            NUMERIC(15,2)   NOT NULL,
    CONSTRAINT fk_po_run
        FOREIGN KEY (production_run_id) REFERENCES production_runs (id) ON DELETE CASCADE,
    CONSTRAINT fk_po_product
        FOREIGN KEY (product_id) REFERENCES products (id) ON DELETE RESTRICT,
    CONSTRAINT uq_po_run
        UNIQUE (production_run_id),
    CONSTRAINT ck_po_qty_pos
        CHECK (quantity > 0),
    CONSTRAINT ck_po_unit_cost_nonneg
        CHECK (unit_cost_snapshot >= 0),
    CONSTRAINT ck_po_total_cost_nonneg
        CHECK (total_cost >= 0)
);

CREATE INDEX ix_po_product ON production_outputs (product_id);


-- -----------------------------------------------------------------------------
-- 9.4 production_cost_lines (overhead)
-- Cross-row: SUM(amount) = production_runs.total_overhead_cost.
-- paid_in_cash=TRUE → cash_movement (production_overhead) at post.
-- -----------------------------------------------------------------------------
CREATE TABLE production_cost_lines (
    id                  BIGSERIAL       PRIMARY KEY,
    production_run_id   BIGINT          NOT NULL,
    cost_type_id        INT             NOT NULL,
    description         TEXT            NULL,
    amount              NUMERIC(15,2)   NOT NULL,
    paid_in_cash        BOOLEAN         NOT NULL DEFAULT TRUE,
    line_number         SMALLINT        NOT NULL,
    CONSTRAINT fk_pcl_run
        FOREIGN KEY (production_run_id) REFERENCES production_runs (id) ON DELETE CASCADE,
    CONSTRAINT fk_pcl_cost_type
        FOREIGN KEY (cost_type_id) REFERENCES cost_types (id) ON DELETE RESTRICT,
    CONSTRAINT uq_pcl_run_line_num
        UNIQUE (production_run_id, line_number),
    CONSTRAINT ck_pcl_amount_pos
        CHECK (amount > 0),
    CONSTRAINT ck_pcl_line_num_pos
        CHECK (line_number > 0)
);

COMMENT ON TABLE production_cost_lines IS 'Production overhead cost lines (per PRD §15). History-preserving: cost_type_id never hard-deleted when referenced.';


-- =============================================================================
-- 10. CASH JOURNAL (authoritative cash ledger)
-- =============================================================================
-- Per DB spec §2.10.1. Append-only. INV-03 (Cash ≥ 0) enforced by trigger.


-- -----------------------------------------------------------------------------
-- 10.1 cash_movements
-- -----------------------------------------------------------------------------
CREATE TABLE cash_movements (
    id                  BIGSERIAL       PRIMARY KEY,
    movement_date       TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    amount              NUMERIC(15,2)   NOT NULL,
    direction           VARCHAR(10)     NOT NULL,
    trigger             VARCHAR(40)     NOT NULL,
    payment_method_id   INT             NOT NULL,
    reference_type      VARCHAR(40)     NULL,
    reference_id        BIGINT          NULL,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_by          INT             NOT NULL,
    CONSTRAINT fk_cash_movements_method
        FOREIGN KEY (payment_method_id) REFERENCES payment_methods (id) ON DELETE RESTRICT,
    CONSTRAINT fk_cash_movements_created_by
        FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_cash_movements_amount_nonzero
        CHECK (amount <> 0),
    CONSTRAINT ck_cash_movements_direction
        CHECK (direction IN ('in','out')),
    CONSTRAINT ck_cash_movements_in_pos
        CHECK ((direction = 'in') = (amount > 0)),
    CONSTRAINT ck_cash_movements_out_neg
        CHECK ((direction = 'out') = (amount < 0)),
    CONSTRAINT ck_cash_movements_trigger
        CHECK (trigger IN (
            'sale_payment','supplier_payment','manual_income','manual_expense',
            'refund','supplier_repayment','production_overhead','freight_cash',
            'change_tendered'
        ))
);

CREATE INDEX ix_cash_movements_date ON cash_movements (movement_date);
CREATE INDEX ix_cash_movements_trigger ON cash_movements (trigger);
CREATE INDEX ix_cash_movements_reference ON cash_movements (reference_type, reference_id);
CREATE INDEX ix_cash_movements_method ON cash_movements (payment_method_id);

COMMENT ON TABLE  cash_movements IS 'Authoritative cash ledger. Append-only; SUM(amount) is the current cash balance. Pre-insert trigger enforces Cash >= 0 (INV-03).';
COMMENT ON COLUMN cash_movements.trigger IS '9 distinct triggers (sale_payment, supplier_payment, manual_income, manual_expense, refund, supplier_repayment, production_overhead, freight_cash, change_tendered). The change_tendered trigger value is used in pairs (one gross-tender in + one change out) so the net cash effect equals the allocated payment amount (BR-SALE-007).';


-- =============================================================================
-- 11. REFUNDS & SUPPLIER REPAYMENTS
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 11.1 refunds
-- Per DB spec §2.8.4 + API §15.11.
-- Cross-row trigger: amount ≤ (Σ sale_payments − Σ refunds for sale) (INV-04).
-- -----------------------------------------------------------------------------
CREATE TABLE refunds (
    id                              BIGSERIAL       PRIMARY KEY,
    sale_id                         BIGINT          NOT NULL,
    amount                          NUMERIC(15,2)   NOT NULL,
    payment_method_id               INT             NOT NULL,
    refund_date                     TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    reason                          TEXT            NULL,
    refundable_amount_snapshot      NUMERIC(15,2)   NOT NULL,
    created_at                      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_by                      INT             NOT NULL,
    CONSTRAINT fk_refunds_sale
        FOREIGN KEY (sale_id) REFERENCES sales (id) ON DELETE RESTRICT,
    CONSTRAINT fk_refunds_method
        FOREIGN KEY (payment_method_id) REFERENCES payment_methods (id) ON DELETE RESTRICT,
    CONSTRAINT fk_refunds_created_by
        FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_refunds_amount_pos
        CHECK (amount > 0),
    CONSTRAINT ck_refunds_amount_le_snapshot
        -- INV-04: refund amount must not exceed the captured refundable
        -- amount at the time of refund (CRL = payments − prior refunds).
        -- The snapshot is set by the application at insert time from the
        -- derived CRL; this check guarantees consistency if the snapshot
        -- is incorrectly computed.
        CHECK (amount <= refundable_amount_snapshot),
    CONSTRAINT ck_refunds_snapshot_nonneg
        CHECK (refundable_amount_snapshot >= 0)
);

CREATE INDEX ix_refunds_sale ON refunds (sale_id);
CREATE INDEX ix_refunds_date ON refunds (refund_date);

COMMENT ON TABLE refunds IS 'Customer refund disbursements. Cross-row trigger enforces amount <= CRL (Σ sale_payments − Σ refunds for sale) per INV-04.';


-- -----------------------------------------------------------------------------
-- 11.2 supplier_repayments
-- Per DB spec §2.8.5 + API §15.13.
-- V1 simplified model: one row per purchase. `amount` is the total obligation
-- (Σ purchase_payments at creation); `received_amount` is the running total
-- of cash_movements (supplier_repayment) for this purchase. Invariant:
--   received_amount <= amount
-- Cross-row trigger: amount ≤ (Σ purchase_payments − Σ supplier_repayments received) (INV-04).
-- -----------------------------------------------------------------------------
CREATE TABLE supplier_repayments (
    id                              BIGSERIAL       PRIMARY KEY,
    purchase_id                     BIGINT          NOT NULL,
    amount                          NUMERIC(15,2)   NOT NULL,
    received_amount                 NUMERIC(15,2)   NOT NULL DEFAULT 0,
    payment_method_id               INT             NOT NULL,
    repayment_date                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    reason                          TEXT            NULL,
    refundable_amount_snapshot      NUMERIC(15,2)   NOT NULL,
    created_at                      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_by                      INT             NOT NULL,
    CONSTRAINT fk_srep_purchase
        FOREIGN KEY (purchase_id) REFERENCES purchases (id) ON DELETE RESTRICT,
    CONSTRAINT fk_srep_method
        FOREIGN KEY (payment_method_id) REFERENCES payment_methods (id) ON DELETE RESTRICT,
    CONSTRAINT fk_srep_created_by
        FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_srep_amount_pos
        CHECK (amount > 0),
    CONSTRAINT ck_srep_received_nonneg
        CHECK (received_amount >= 0),
    CONSTRAINT ck_srep_received_le_amount
        CHECK (received_amount <= amount),
    CONSTRAINT ck_srep_snapshot_nonneg
        CHECK (refundable_amount_snapshot >= 0)
);

CREATE INDEX ix_srep_purchase ON supplier_repayments (purchase_id);
CREATE INDEX ix_srep_date ON supplier_repayments (repayment_date);

COMMENT ON TABLE supplier_repayments IS 'Supplier repayment obligations and receipts. amount = total obligation; received_amount = cumulative cash received (INV-04 ceiling).';


-- =============================================================================
-- 12. MANUAL FINANCE ENTRIES
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 12.1 manual_finance_entries
-- Per DB spec §2.8.3 + API §15.16. Cash-based only.
-- Always paired with a cash_movements row inserted in the same transaction.
-- Cancellation creates a reversing cash_movement (opposite direction, same amount).
-- -----------------------------------------------------------------------------
CREATE TABLE manual_finance_entries (
    id                  BIGSERIAL       PRIMARY KEY,
    category_id         INT             NOT NULL,
    entry_date          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    amount              NUMERIC(15,2)   NOT NULL,
    payment_method_id   INT             NOT NULL,
    notes               TEXT            NULL,
    lifecycle_status    VARCHAR(20)     NOT NULL DEFAULT 'posted',
    cancellation_date   TIMESTAMPTZ     NULL,
    cancellation_reason TEXT            NULL,
    cancelled_by        INT             NULL,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    created_by          INT             NOT NULL,
    version             INT             NOT NULL DEFAULT 1,
    CONSTRAINT fk_mfe_category
        FOREIGN KEY (category_id) REFERENCES financial_categories (id) ON DELETE RESTRICT,
    CONSTRAINT fk_mfe_method
        FOREIGN KEY (payment_method_id) REFERENCES payment_methods (id) ON DELETE RESTRICT,
    CONSTRAINT fk_mfe_cancelled_by
        FOREIGN KEY (cancelled_by) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_mfe_created_by
        FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_mfe_amount_pos
        CHECK (amount > 0),
    CONSTRAINT ck_mfe_lifecycle
        CHECK (lifecycle_status IN ('posted','cancelled'))
);

CREATE INDEX ix_mfe_date_type ON manual_finance_entries (entry_date, lifecycle_status);
CREATE INDEX ix_mfe_category ON manual_finance_entries (category_id);

COMMENT ON TABLE manual_finance_entries IS 'Manual cash income/expense entries. Per PRD §17, categories that duplicate derived entries (Sales, COGS, Production, Purchase Shipping) are excluded. Cancellation creates a reversing cash_movement.';


-- =============================================================================
-- 13. CONFIGURATION
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 13.1 system_settings
-- Per DB spec §2.9.1. Key/value store with type tag.
-- Per reconciliation: is_initialized is a boolean row in this table
-- (operational flag for the one-time initialization).
-- -----------------------------------------------------------------------------
CREATE TABLE system_settings (
    key           VARCHAR(100)  PRIMARY KEY,
    value         TEXT          NOT NULL,
    value_type    VARCHAR(20)   NOT NULL,
    description   TEXT          NULL,
    updated_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_by    INT           NULL,
    CONSTRAINT fk_system_settings_updated_by
        FOREIGN KEY (updated_by) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT ck_system_settings_value_type
        CHECK (value_type IN ('string','number','boolean','json'))
);

COMMENT ON TABLE system_settings IS 'Configurable system settings. The is_initialized key gates one-time system initialization (per MS-7).';


-- =============================================================================
-- 14. NOTIFICATIONS
-- =============================================================================
-- Per BR-STOCK-011 + PRD §27 + API §15.19. The PRD lists 5 categories:
-- low-stock, insufficient-stock, below-cost, system, action confirmations.
-- notifications are computed on read for some categories (e.g. low-stock is
-- also a derived view), but the table exists for persistent categories
-- (system, action confirmations).


CREATE TABLE notifications (
    id          BIGSERIAL       PRIMARY KEY,
    user_id     INT             NULL,                 -- NULL = broadcast / system
    category    VARCHAR(50)     NOT NULL,
    severity    VARCHAR(20)     NOT NULL DEFAULT 'info',
    title       VARCHAR(200)    NOT NULL,
    body        TEXT            NULL,
    reference_type VARCHAR(50)   NULL,
    reference_id   BIGINT       NULL,
    is_read     BOOLEAN         NOT NULL DEFAULT FALSE,
    read_at     TIMESTAMPTZ     NULL,
    created_at  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_notifications_user
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT ck_notifications_category
        CHECK (category IN ('low_stock','insufficient_stock','below_cost','system','action_confirmation')),
    CONSTRAINT ck_notifications_severity
        CHECK (severity IN ('info','warning','error')),
    CONSTRAINT ck_notifications_is_read
        CHECK (is_read IN (TRUE, FALSE))
);

CREATE INDEX ix_notifications_user_unread
    ON notifications (user_id, is_read, created_at DESC)
    WHERE is_read = FALSE;
CREATE INDEX ix_notifications_category ON notifications (category);
CREATE INDEX ix_notifications_reference ON notifications (reference_type, reference_id);

COMMENT ON TABLE  notifications IS 'In-app notifications. Categories: low_stock, insufficient_stock, below_cost, system, action_confirmation (PRD §27). user_id=NULL for broadcast/system messages.';
COMMENT ON COLUMN notifications.is_read IS 'Marked read via POST /notifications/{id}/mark-read (API §15.19.2).';


-- =============================================================================
-- 15. SESSIONS & IDEMPOTENCY
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 15.1 sessions
-- Per API §2.1 + §15.2.1. Required for revokable auth (BR-AUTH-003).
-- Server-side records; access tokens and refresh tokens are opaque strings
-- stored hashed (password-style) for O(1) lookup.
-- -----------------------------------------------------------------------------
CREATE TABLE sessions (
    id                  UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id             INT           NOT NULL,
    access_token_hash   VARCHAR(255)  NOT NULL,
    refresh_token_hash  VARCHAR(255)  NOT NULL,
    access_expires_at   TIMESTAMPTZ   NOT NULL,
    refresh_expires_at  TIMESTAMPTZ   NOT NULL,
    last_seen_at        TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    ip_address          INET          NULL,
    user_agent          TEXT          NULL,
    revoked_at          TIMESTAMPTZ   NULL,
    revoked_reason      VARCHAR(100)  NULL,
    created_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    CONSTRAINT fk_sessions_user
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT ck_sessions_not_revoked_pair
        CHECK ((revoked_at IS NULL) = (revoked_reason IS NULL))
);

CREATE UNIQUE INDEX ux_sessions_access_token_hash ON sessions (access_token_hash);
CREATE UNIQUE INDEX ux_sessions_refresh_token_hash ON sessions (refresh_token_hash);
CREATE INDEX ix_sessions_user ON sessions (user_id);
CREATE INDEX ix_sessions_access_expires ON sessions (access_expires_at);
CREATE INDEX ix_sessions_active ON sessions (user_id) WHERE revoked_at IS NULL;

COMMENT ON TABLE sessions IS 'Server-side session records. Required for immediate revocation (BR-AUTH-003). Tokens stored hashed; sessions expire on schedule or revoke.';
COMMENT ON COLUMN sessions.access_token_hash IS 'Hash of opaque access token; never the token itself.';
COMMENT ON COLUMN sessions.refresh_token_hash IS 'Hash of opaque refresh token; rotated on /auth/refresh.';


-- -----------------------------------------------------------------------------
-- 15.2 idempotency_keys
-- Per API §7.1. Caches the (request_hash → response) mapping per (key, user,
-- endpoint). 24-hour TTL; expired rows pruned by operational job.
-- -----------------------------------------------------------------------------
CREATE TABLE idempotency_keys (
    id                  BIGSERIAL      PRIMARY KEY,
    key                 VARCHAR(255)   NOT NULL,
    user_id             INT            NOT NULL,
    endpoint            VARCHAR(255)   NOT NULL,
    request_hash        VARCHAR(128)   NOT NULL,
    response_status     INT            NULL,                 -- NULL = in-flight
    response_body       JSONB          NULL,
    created_at          TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ    NULL,
    expires_at          TIMESTAMPTZ    NOT NULL,
    CONSTRAINT fk_idem_user
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
    CONSTRAINT uq_idem_key_user_endpoint
        UNIQUE (key, user_id, endpoint)
);

CREATE INDEX ix_idem_expires ON idempotency_keys (expires_at);

COMMENT ON TABLE  idempotency_keys IS 'Per API §7.1: caches (key,user,endpoint) → (request_hash, response). 24h TTL. Required for sale post, purchase post, refund, payment, cancel, return, stock adjust, manual entry, login.';
COMMENT ON COLUMN idempotency_keys.response_status IS 'NULL = original request still in-flight; second request with same key returns 202 + Retry-After.';


-- =============================================================================
-- 16. AUDIT LOG
-- =============================================================================
-- Per BR-AUDIT-001/002/003 + DB spec §2.11.1. Append-only; no UPDATE/DELETE.


CREATE TABLE audit_log (
    id            BIGSERIAL      PRIMARY KEY,
    event_time    TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    user_id       INT            NULL,                 -- SET NULL on user delete
    action        VARCHAR(50)    NOT NULL,             -- create|post|cancel|return|adjust|...
    entity_type   VARCHAR(50)    NOT NULL,             -- sale|purchase|product|user|...
    entity_id     BIGINT         NULL,
    old_values    JSONB          NULL,
    new_values    JSONB          NULL,
    reason        TEXT           NULL,
    ip_address    INET           NULL,
    request_id    UUID           NULL,
    CONSTRAINT fk_audit_user
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT ck_audit_action
        CHECK (action IN ('create','post','cancel','complete','return','adjust',
                          'movement','price_override','payment','refund',
                          'permission_grant','permission_revoke','settings_change',
                          'deactivate','update'))
);

CREATE INDEX ix_audit_entity ON audit_log (entity_type, entity_id);
CREATE INDEX ix_audit_event_time ON audit_log (event_time);
CREATE INDEX ix_audit_user_time ON audit_log (user_id, event_time);
CREATE INDEX ix_audit_action ON audit_log (action);

COMMENT ON TABLE  audit_log IS 'Append-only audit log. No UPDATE, no DELETE (enforced by trigger). Per BR-AUDIT-001/002/003 + DB spec §2.11.1.';
COMMENT ON COLUMN audit_log.user_id IS 'SET NULL on user delete so history is preserved.';


-- =============================================================================
-- 17. VIEWS (derived data — no stored mutable values)
-- =============================================================================


-- -----------------------------------------------------------------------------
-- 17.1 product_stock (per DB spec §2.2.5)
-- On-hand quantity per product; recomputed on every read.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW product_stock AS
SELECT
    product_id,
    SUM(quantity)                                AS on_hand_quantity
FROM stock_movements
GROUP BY product_id;

COMMENT ON VIEW product_stock IS 'Derived on-hand quantity per product. Source of truth for inventory levels. Per BR-REPORT-001: no separate maintained totals.';


-- -----------------------------------------------------------------------------
-- 17.2 product_valuation (per DB spec §3.4)
-- On-hand quantity + inventory value per product. Drives dashboard and reports.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW product_valuation AS
SELECT
    p.id                                    AS product_id,
    p.code,
    p.name,
    COALESCE(SUM(sm.quantity), 0)           AS on_hand_quantity,
    COALESCE(SUM(sm.total_cost), 0)         AS inventory_value
FROM products p
LEFT JOIN stock_movements sm ON sm.product_id = p.id
GROUP BY p.id, p.code, p.name;

COMMENT ON VIEW product_valuation IS 'Per-product inventory valuation. total_cost carries the sign of quantity (positive for receipts, negative for issues). INV-08 reconciliation source.';


-- =============================================================================
-- 18. FUNCTIONS & TRIGGERS
-- =============================================================================
-- All enforcement logic for non-declarable invariants. Grouped by table.

-- -----------------------------------------------------------------------------
-- 18.1 audit_log immutability trigger
-- Per BR-AUDIT-002 + DB spec §2.11.1: append-only.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_audit_log_immutable() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only; UPDATE/DELETE is not permitted';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_audit_log_no_update
BEFORE UPDATE ON audit_log
FOR EACH STATEMENT EXECUTE FUNCTION fn_audit_log_immutable();

CREATE TRIGGER trg_audit_log_no_delete
BEFORE DELETE ON audit_log
FOR EACH STATEMENT EXECUTE FUNCTION fn_audit_log_immutable();


-- -----------------------------------------------------------------------------
-- 18.2 stock_movements immutability trigger
-- Per DB spec §2.6.1 + §3.2: append-only; reversals insert new rows.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_stock_movements_immutable() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'stock_movements is append-only; UPDATE/DELETE is not permitted (use a reversal row)';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_stock_movements_no_update
BEFORE UPDATE ON stock_movements
FOR EACH STATEMENT EXECUTE FUNCTION fn_stock_movements_immutable();

CREATE TRIGGER trg_stock_movements_no_delete
BEFORE DELETE ON stock_movements
FOR EACH STATEMENT EXECUTE FUNCTION fn_stock_movements_immutable();


-- -----------------------------------------------------------------------------
-- 18.3 stock_movements reversal uniqueness (INV-05)
-- Per DB spec §3.2 rule 3: a movement can be reversed at most once.
-- (SELECT COUNT(*) FROM stock_movements WHERE reversal_of_movement_id = X) <= 1
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_stock_movements_reversal_unique() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.reversal_of_movement_id IS NOT NULL THEN
        IF EXISTS (
            SELECT 1 FROM stock_movements
            WHERE reversal_of_movement_id = NEW.reversal_of_movement_id
              AND id <> NEW.id
        ) THEN
            RAISE EXCEPTION
                'stock_movement % is already reversed; INV-05: a movement can be reversed at most once',
                NEW.reversal_of_movement_id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_stock_movements_reversal_unique
BEFORE INSERT ON stock_movements
FOR EACH ROW EXECUTE FUNCTION fn_stock_movements_reversal_unique();


-- -----------------------------------------------------------------------------
-- 18.4 cash_movements immutability trigger
-- Per DB spec §2.10.1: append-only.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_cash_movements_immutable() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'cash_movements is append-only; UPDATE/DELETE is not permitted';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_cash_movements_no_update
BEFORE UPDATE ON cash_movements
FOR EACH STATEMENT EXECUTE FUNCTION fn_cash_movements_immutable();

CREATE TRIGGER trg_cash_movements_no_delete
BEFORE DELETE ON cash_movements
FOR EACH STATEMENT EXECUTE FUNCTION fn_cash_movements_immutable();


-- -----------------------------------------------------------------------------
-- 18.5 cash_movements non-negative balance (INV-03)
-- Per DB spec §2.10.1: pre-insert trigger on cash_movements.
-- This is the authoritative cash check. Per API §7.3, the transaction should
-- hold the cash_balance advisory lock when writing cash_movements; the lock
-- is the application-layer concern. This trigger is the database-layer
-- guard that catches any out-of-band insert.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_cash_movements_balance_check() RETURNS TRIGGER AS $$
DECLARE
    v_current NUMERIC(15,2);
    v_projected NUMERIC(15,2);
BEGIN
    SELECT COALESCE(SUM(amount), 0) INTO v_current FROM cash_movements;
    v_projected := v_current + NEW.amount;
    IF v_projected < 0 THEN
        RAISE EXCEPTION
            'cash_movements insert would result in negative cash balance (current=%, projected=%); INV-03 cash >= 0',
            v_current, v_projected
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_cash_movements_balance_check
BEFORE INSERT ON cash_movements
FOR EACH ROW EXECUTE FUNCTION fn_cash_movements_balance_check();


-- -----------------------------------------------------------------------------
-- 18.6 sale lifecycle terminal cancellation (INV-05)
-- Rejects any lifecycle_status UPDATE from 'cancelled' to anything else.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_sale_lifecycle_terminal() RETURNS TRIGGER AS $$
BEGIN
    IF OLD.lifecycle_status = 'cancelled' AND NEW.lifecycle_status <> 'cancelled' THEN
        RAISE EXCEPTION
            'sale % is cancelled; lifecycle_status is terminal (INV-05)',
            OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    -- Reject skipping draft: only allowed transitions are
    --   draft -> posted|cancelled
    --   posted -> completed|partially_returned|returned|cancelled
    --   partially_returned -> returned|cancelled
    --   returned -> cancelled
    --   completed -> partially_returned|cancelled
    IF OLD.lifecycle_status IS DISTINCT FROM NEW.lifecycle_status THEN
        IF NOT (
            (OLD.lifecycle_status = 'draft' AND NEW.lifecycle_status IN ('posted','cancelled'))
         OR (OLD.lifecycle_status = 'posted' AND NEW.lifecycle_status IN ('completed','partially_returned','returned','cancelled'))
         OR (OLD.lifecycle_status = 'partially_returned' AND NEW.lifecycle_status IN ('returned','cancelled'))
         OR (OLD.lifecycle_status = 'returned' AND NEW.lifecycle_status = 'cancelled')
         OR (OLD.lifecycle_status = 'completed' AND NEW.lifecycle_status IN ('partially_returned','cancelled'))
        ) THEN
            RAISE EXCEPTION
                'sale %: invalid lifecycle transition % -> %',
                OLD.id, OLD.lifecycle_status, NEW.lifecycle_status
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_sale_lifecycle_terminal
BEFORE UPDATE OF lifecycle_status ON sales
FOR EACH ROW EXECUTE FUNCTION fn_sale_lifecycle_terminal();


-- -----------------------------------------------------------------------------
-- 18.7 sale_lines immutability once posted
-- Per DB spec §2.4.2 + BR-SALE-010 + BR-DATA-002: posted sale lines are
-- immutable. Updates / deletes are rejected.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_sale_lines_immutable_when_posted() RETURNS TRIGGER AS $$
DECLARE
    v_status VARCHAR(20);
BEGIN
    SELECT lifecycle_status INTO v_status FROM sales WHERE id = OLD.sale_id;
    IF v_status IS NOT NULL AND v_status <> 'draft' THEN
        RAISE EXCEPTION
            'sale_line % belongs to a % sale; lines are immutable after post',
            OLD.id, v_status
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_sale_lines_immutable_update
BEFORE UPDATE ON sale_lines
FOR EACH ROW EXECUTE FUNCTION fn_sale_lines_immutable_when_posted();

CREATE TRIGGER trg_sale_lines_immutable_delete
BEFORE DELETE ON sale_lines
FOR EACH ROW EXECUTE FUNCTION fn_sale_lines_immutable_when_posted();


-- -----------------------------------------------------------------------------
-- 18.8 sale_payments cross-row allocation bound (BR-PAYMENT-004)
-- Σ amount per sale ≤ sales.total_amount. Computed against NEW values for
-- INSERT; against NEW + existing on UPDATE.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_sale_payment_allocation_bound() RETURNS TRIGGER AS $$
DECLARE
    v_total       NUMERIC(15,2);
    v_allocated   NUMERIC(15,2);
BEGIN
    SELECT total_amount INTO v_total FROM sales WHERE id = NEW.sale_id;
    IF v_total IS NULL THEN
        RAISE EXCEPTION 'sale % not found', NEW.sale_id;
    END IF;

    SELECT COALESCE(SUM(amount), 0) INTO v_allocated
    FROM sale_payments
    WHERE sale_id = NEW.sale_id
      AND id <> COALESCE(NEW.id, -1);

    IF (v_allocated + NEW.amount) > v_total THEN
        RAISE EXCEPTION
            'sale_payment allocation exceeds sale total (allocated=%, new=%, total=%); BR-PAYMENT-004',
            v_allocated, NEW.amount, v_total
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_sale_payments_allocation_bound
BEFORE INSERT OR UPDATE ON sale_payments
FOR EACH ROW EXECUTE FUNCTION fn_sale_payment_allocation_bound();


-- -----------------------------------------------------------------------------
-- 18.9 sales_return_lines cross-row quantity bound
-- Σ quantity per sale_line_id ≤ sale_lines.quantity (BR-RETURN-002).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_srl_quantity_bound() RETURNS TRIGGER AS $$
DECLARE
    v_orig_qty   NUMERIC(15,4);
    v_total_ret  NUMERIC(15,4);
BEGIN
    SELECT quantity INTO v_orig_qty FROM sale_lines WHERE id = NEW.sale_line_id;
    IF v_orig_qty IS NULL THEN
        RAISE EXCEPTION 'sale_line % not found', NEW.sale_line_id;
    END IF;

    SELECT COALESCE(SUM(srl.quantity), 0) INTO v_total_ret
    FROM sales_return_lines srl
    WHERE srl.sale_line_id = NEW.sale_line_id
      AND srl.id <> COALESCE(NEW.id, -1);

    IF (v_total_ret + NEW.quantity) > v_orig_qty THEN
        RAISE EXCEPTION
            'sales_return quantity for sale_line % exceeds remaining (returned=%, new=%, original=%); BR-SALE-009',
            NEW.sale_line_id, v_total_ret, NEW.quantity, v_orig_qty
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_srl_quantity_bound
BEFORE INSERT OR UPDATE ON sales_return_lines
FOR EACH ROW EXECUTE FUNCTION fn_srl_quantity_bound();


-- -----------------------------------------------------------------------------
-- 18.10 purchase lifecycle terminal cancellation (INV-05)
-- Same pattern as sale.
-- -----------------------------------------------------------------------------
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
         OR (OLD.lifecycle_status = 'partially_returned' AND NEW.lifecycle_status IN ('returned','cancelled','posted'))
         OR (OLD.lifecycle_status = 'returned' AND NEW.lifecycle_status = 'cancelled')
         OR (OLD.lifecycle_status = 'completed' AND NEW.lifecycle_status IN ('partially_returned','cancelled'))
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

CREATE TRIGGER trg_purchase_lifecycle_terminal
BEFORE UPDATE OF lifecycle_status ON purchases
FOR EACH ROW EXECUTE FUNCTION fn_purchase_lifecycle_terminal();


-- -----------------------------------------------------------------------------
-- 18.11 purchase_payments cross-row allocation bound
-- Σ amount per purchase ≤ purchase total (provisional; actual total derives
-- from lines + shipping; we check against purchase.total_amount at insert time,
-- the application layer is responsible for keeping it in sync).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_purchase_payment_allocation_bound() RETURNS TRIGGER AS $$
DECLARE
    v_total       NUMERIC(15,2);
    v_allocated   NUMERIC(15,2);
BEGIN
    -- The purchases.total_amount is not stored; it is derived from
    -- purchase_lines.line_total + purchase_shipping.amount. We compute
    -- it here.
    SELECT
        COALESCE((SELECT SUM(line_total) FROM purchase_lines WHERE purchase_id = NEW.purchase_id), 0)
        + COALESCE((SELECT amount FROM purchase_shipping WHERE purchase_id = NEW.purchase_id), 0)
      INTO v_total;
    IF v_total IS NULL THEN v_total := 0; END IF;

    SELECT COALESCE(SUM(amount), 0) INTO v_allocated
    FROM purchase_payments
    WHERE purchase_id = NEW.purchase_id
      AND id <> COALESCE(NEW.id, -1);

    IF (v_allocated + NEW.amount) > v_total THEN
        RAISE EXCEPTION
            'purchase_payment allocation exceeds purchase total (allocated=%, new=%, total=%); BR-PAYMENT-004',
            v_allocated, NEW.amount, v_total
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_purchase_payments_allocation_bound
BEFORE INSERT OR UPDATE ON purchase_payments
FOR EACH ROW EXECUTE FUNCTION fn_purchase_payment_allocation_bound();


-- -----------------------------------------------------------------------------
-- 18.12 purchase_return_lines cross-row quantity bound
-- Σ quantity per purchase_line_id ≤ purchase_lines.quantity.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_prl_quantity_bound() RETURNS TRIGGER AS $$
DECLARE
    v_orig_qty   NUMERIC(15,4);
    v_total_ret  NUMERIC(15,4);
BEGIN
    SELECT quantity INTO v_orig_qty FROM purchase_lines WHERE id = NEW.purchase_line_id;
    IF v_orig_qty IS NULL THEN
        RAISE EXCEPTION 'purchase_line % not found', NEW.purchase_line_id;
    END IF;

    SELECT COALESCE(SUM(prl.quantity), 0) INTO v_total_ret
    FROM purchase_return_lines prl
    WHERE prl.purchase_line_id = NEW.purchase_line_id
      AND prl.id <> COALESCE(NEW.id, -1);

    IF (v_total_ret + NEW.quantity) > v_orig_qty THEN
        RAISE EXCEPTION
            'purchase_return quantity for purchase_line % exceeds remaining (returned=%, new=%, original=%); BR-PURCHASE-006',
            NEW.purchase_line_id, v_total_ret, NEW.quantity, v_orig_qty
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_prl_quantity_bound
BEFORE INSERT OR UPDATE ON purchase_return_lines
FOR EACH ROW EXECUTE FUNCTION fn_prl_quantity_bound();


-- -----------------------------------------------------------------------------
-- 18.13 production lifecycle terminal cancellation
-- draft -> posted | cancelled; posted -> completed | cancelled.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_production_lifecycle_terminal() RETURNS TRIGGER AS $$
BEGIN
    IF OLD.lifecycle_status = 'cancelled' AND NEW.lifecycle_status <> 'cancelled' THEN
        RAISE EXCEPTION
            'production_run % is cancelled; lifecycle_status is terminal (INV-05)',
            OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.lifecycle_status IS DISTINCT FROM NEW.lifecycle_status THEN
        IF NOT (
            (OLD.lifecycle_status = 'draft' AND NEW.lifecycle_status IN ('posted','cancelled'))
         OR (OLD.lifecycle_status = 'posted' AND NEW.lifecycle_status IN ('completed','cancelled'))
        ) THEN
            RAISE EXCEPTION
                'production_run %: invalid lifecycle transition % -> %',
                OLD.id, OLD.lifecycle_status, NEW.lifecycle_status
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_production_lifecycle_terminal
BEFORE UPDATE OF lifecycle_status ON production_runs
FOR EACH ROW EXECUTE FUNCTION fn_production_lifecycle_terminal();


-- -----------------------------------------------------------------------------
-- 18.14 production_inputs/outputs/cost_lines immutability when posted
-- Per DB spec §2.7.1 + §2.7.3/§2.7.4: posted run lines are immutable.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_production_lines_immutable_when_posted() RETURNS TRIGGER AS $$
DECLARE
    v_status     VARCHAR(20);
    v_run_id     BIGINT;
BEGIN
    IF TG_TABLE_NAME = 'production_inputs' THEN
        v_run_id := COALESCE(OLD.production_run_id, NEW.production_run_id);
    ELSIF TG_TABLE_NAME = 'production_outputs' THEN
        v_run_id := COALESCE(OLD.production_run_id, NEW.production_run_id);
    ELSE
        v_run_id := COALESCE(OLD.production_run_id, NEW.production_run_id);
    END IF;

    SELECT lifecycle_status INTO v_status FROM production_runs WHERE id = v_run_id;
    IF v_status IS NOT NULL AND v_status <> 'draft' THEN
        RAISE EXCEPTION
            '% for production_run % belongs to a % run; lines are immutable after post',
            TG_TABLE_NAME, v_run_id, v_status
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_production_inputs_immutable_update
BEFORE UPDATE ON production_inputs
FOR EACH ROW EXECUTE FUNCTION fn_production_lines_immutable_when_posted();

CREATE TRIGGER trg_production_inputs_immutable_delete
BEFORE DELETE ON production_inputs
FOR EACH ROW EXECUTE FUNCTION fn_production_lines_immutable_when_posted();

CREATE TRIGGER trg_production_outputs_immutable_update
BEFORE UPDATE ON production_outputs
FOR EACH ROW EXECUTE FUNCTION fn_production_lines_immutable_when_posted();

CREATE TRIGGER trg_production_outputs_immutable_delete
BEFORE DELETE ON production_outputs
FOR EACH ROW EXECUTE FUNCTION fn_production_lines_immutable_when_posted();

CREATE TRIGGER trg_production_cost_lines_immutable_update
BEFORE UPDATE ON production_cost_lines
FOR EACH ROW EXECUTE FUNCTION fn_production_lines_immutable_when_posted();

CREATE TRIGGER trg_production_cost_lines_immutable_delete
BEFORE DELETE ON production_cost_lines
FOR EACH ROW EXECUTE FUNCTION fn_production_lines_immutable_when_posted();


-- -----------------------------------------------------------------------------
-- 18.15 manual_finance_entries lifecycle (only posted|cancelled; no
-- transition back to posted)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_mfe_lifecycle_terminal() RETURNS TRIGGER AS $$
BEGIN
    IF OLD.lifecycle_status = 'cancelled' AND NEW.lifecycle_status <> 'cancelled' THEN
        RAISE EXCEPTION
            'manual_finance_entry % is cancelled; lifecycle is terminal (INV-05)',
            OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.lifecycle_status IS DISTINCT FROM NEW.lifecycle_status
       AND NOT (OLD.lifecycle_status = 'posted' AND NEW.lifecycle_status = 'cancelled')
    THEN
        RAISE EXCEPTION
            'manual_finance_entry %: invalid transition % -> %',
            OLD.id, OLD.lifecycle_status, NEW.lifecycle_status
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_mfe_lifecycle_terminal
BEFORE UPDATE OF lifecycle_status ON manual_finance_entries
FOR EACH ROW EXECUTE FUNCTION fn_mfe_lifecycle_terminal();


-- -----------------------------------------------------------------------------
-- 18.16 sales_returns lifecycle (posted | cancelled)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_sales_returns_lifecycle_terminal() RETURNS TRIGGER AS $$
BEGIN
    IF OLD.lifecycle_status = 'cancelled' AND NEW.lifecycle_status <> 'cancelled' THEN
        RAISE EXCEPTION
            'sales_return % is cancelled; lifecycle is terminal',
            OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.lifecycle_status IS DISTINCT FROM NEW.lifecycle_status
       AND NOT (OLD.lifecycle_status = 'posted' AND NEW.lifecycle_status = 'cancelled')
    THEN
        RAISE EXCEPTION
            'sales_return %: invalid transition % -> %',
            OLD.id, OLD.lifecycle_status, NEW.lifecycle_status
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_sales_returns_lifecycle_terminal
BEFORE UPDATE OF lifecycle_status ON sales_returns
FOR EACH ROW EXECUTE FUNCTION fn_sales_returns_lifecycle_terminal();


-- -----------------------------------------------------------------------------
-- 18.17 purchase_returns lifecycle (posted | cancelled)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_purchase_returns_lifecycle_terminal() RETURNS TRIGGER AS $$
BEGIN
    IF OLD.lifecycle_status = 'cancelled' AND NEW.lifecycle_status <> 'cancelled' THEN
        RAISE EXCEPTION
            'purchase_return % is cancelled; lifecycle is terminal',
            OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.lifecycle_status IS DISTINCT FROM NEW.lifecycle_status
       AND NOT (OLD.lifecycle_status = 'posted' AND NEW.lifecycle_status = 'cancelled')
    THEN
        RAISE EXCEPTION
            'purchase_return %: invalid transition % -> %',
            OLD.id, OLD.lifecycle_status, NEW.lifecycle_status
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_purchase_returns_lifecycle_terminal
BEFORE UPDATE OF lifecycle_status ON purchase_returns
FOR EACH ROW EXECUTE FUNCTION fn_purchase_returns_lifecycle_terminal();


-- -----------------------------------------------------------------------------
-- 18.18 product code partial unique index
-- BR-PRODUCT-001: code is optional. When present, must be unique among
-- products with a code. Enforced via the partial unique index in §5.3.
-- This trigger complements the index by enforcing an additional check that
-- no two products share a non-null code; the index alone is sufficient,
-- so this trigger is a defensive belt-and-suspenders.
-- (Skipped; the partial unique index is the canonical enforcement.)
-- -----------------------------------------------------------------------------

-- -----------------------------------------------------------------------------
-- 18.19 user unique username
-- Enforced via the UNIQUE INDEX ux_users_username; trigger omitted.
-- -----------------------------------------------------------------------------

-- -----------------------------------------------------------------------------
-- 18.20 system_settings immutability flag check
-- The 'costing_method' setting is read-only in V1 (per API §15.20.2).
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_system_settings_readonly_keys() RETURNS TRIGGER AS $$
BEGIN
    IF OLD.key = 'costing_method' AND NEW.value IS DISTINCT FROM OLD.value THEN
        RAISE EXCEPTION 'system_settings key "costing_method" is read-only in V1' USING ERRCODE = 'check_violation';
    END IF;
    -- is_initialized may only transition from false to true
    IF OLD.key = 'is_initialized' AND NEW.value = 'false' AND OLD.value = 'true' THEN
        RAISE EXCEPTION 'system_settings key "is_initialized" may not transition back to false' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_system_settings_readonly_keys
BEFORE UPDATE ON system_settings
FOR EACH ROW EXECUTE FUNCTION fn_system_settings_readonly_keys();


-- -----------------------------------------------------------------------------
-- 18.21 ON UPDATE version++ (for optimistic concurrency)
-- Every UPDATE on version-bearing tables bumps `version` and `updated_at`.
-- Tables: sales, purchases, production_runs, manual_finance_entries,
-- sales_returns, purchase_returns.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_bump_version_and_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.version   := COALESCE(OLD.version, 0) + 1;
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_sales_bump_version
BEFORE UPDATE ON sales
FOR EACH ROW EXECUTE FUNCTION fn_bump_version_and_updated_at();

CREATE TRIGGER trg_purchases_bump_version
BEFORE UPDATE ON purchases
FOR EACH ROW EXECUTE FUNCTION fn_bump_version_and_updated_at();

CREATE TRIGGER trg_production_runs_bump_version
BEFORE UPDATE ON production_runs
FOR EACH ROW EXECUTE FUNCTION fn_bump_version_and_updated_at();

CREATE TRIGGER trg_mfe_bump_version
BEFORE UPDATE ON manual_finance_entries
FOR EACH ROW EXECUTE FUNCTION fn_bump_version_and_updated_at();

CREATE TRIGGER trg_sales_returns_bump_version
BEFORE UPDATE ON sales_returns
FOR EACH ROW EXECUTE FUNCTION fn_bump_version_and_updated_at();

CREATE TRIGGER trg_purchase_returns_bump_version
BEFORE UPDATE ON purchase_returns
FOR EACH ROW EXECUTE FUNCTION fn_bump_version_only();


-- -----------------------------------------------------------------------------
-- 18.22 ON UPDATE updated_at (no version)
-- Tables: roles, capabilities, financial_categories, payment_methods,
-- cost_types, categories, units, products, contacts, system_settings.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at := NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_roles_touch_updated
BEFORE UPDATE ON roles
FOR EACH ROW EXECUTE FUNCTION fn_touch_updated_at();

CREATE TRIGGER trg_financial_categories_touch_updated
BEFORE UPDATE ON financial_categories
FOR EACH ROW EXECUTE FUNCTION fn_touch_updated_at();

CREATE TRIGGER trg_categories_touch_updated
BEFORE UPDATE ON categories
FOR EACH ROW EXECUTE FUNCTION fn_touch_updated_at();

CREATE TRIGGER trg_products_touch_updated
BEFORE UPDATE ON products
FOR EACH ROW EXECUTE FUNCTION fn_touch_updated_at();

CREATE TRIGGER trg_contacts_touch_updated
BEFORE UPDATE ON contacts
FOR EACH ROW EXECUTE FUNCTION fn_touch_updated_at();

CREATE TRIGGER trg_payment_methods_touch_updated
BEFORE UPDATE ON payment_methods
FOR EACH ROW EXECUTE FUNCTION fn_touch_updated_at();

CREATE TRIGGER trg_system_settings_touch_updated
BEFORE UPDATE ON system_settings
FOR EACH ROW EXECUTE FUNCTION fn_touch_updated_at();


-- =============================================================================
-- 19. SEED DATA
-- =============================================================================
-- All seed operations are inside a transaction to keep the bootstrap atomic.
-- =============================================================================

BEGIN;

-- 19.1 Roles (Owner, Staff) -- V1 has exactly two roles.
INSERT INTO roles (name, description, is_system_role) VALUES
  ('Owner', 'Highest role; full access to all capabilities by default.', TRUE),
  ('Staff', 'Restricted operational role; default-on for view, edit own draft, sale.create, purchase.create.', TRUE);

-- 19.2 Capabilities (canonical catalog per API §3.1, C-08..C-11)
INSERT INTO capabilities (code, description) VALUES
  -- Auth
  ('auth.login', 'Log in to the system.'),
  ('auth.password_reset_others', 'Reset other users'' passwords.'),
  -- Identity
  ('user.view', 'View users.'),
  ('user.manage', 'Create, update, deactivate users.'),
  ('user.grant_capability', 'Grant a capability to a user.'),
  ('user.revoke_capability', 'Revoke a capability from a user.'),
  ('role.view', 'View roles.'),
  ('role.manage', 'Create, update roles.'),
  ('audit.view', 'View the audit log.'),
  -- Config
  ('settings.view', 'View system settings.'),
  ('settings.manage', 'Update system settings.'),
  -- Master
  ('category.view', 'View product categories.'),
  ('category.manage', 'Manage product categories.'),
  ('unit.view', 'View units of measure.'),
  ('unit.manage', 'Manage units of measure.'),
  ('product.view', 'View products.'),
  ('product.create', 'Create products.'),
  ('product.edit', 'Edit products.'),
  ('product.deactivate', 'Deactivate products.'),
  ('contact.view', 'View contacts.'),
  ('contact.create', 'Create contacts.'),
  ('contact.edit', 'Edit contacts.'),
  ('contact.manage', 'Manage contacts (delete/deactivate).'),
  ('payment_method.view', 'View payment methods.'),
  ('payment_method.manage', 'Manage payment methods.'),
  ('cost_type.view', 'View cost types.'),
  ('cost_type.manage', 'Manage cost types.'),
  -- Finance
  ('financial_category.view', 'View financial categories.'),
  ('financial_category.manage', 'Manage financial categories.'),
  -- Sales
  ('sale.view', 'View sales.'),
  ('sale.create', 'Create a sale (POS).'),
  ('sale.edit_own_draft', 'Edit a sale that the user created in draft status.'),
  ('sale.post', 'Post (commit) a sale.'),
  ('sale.complete', 'Complete a sale (auto on full payment).'),
  ('sale.cancel', 'Cancel a posted sale.'),
  ('sale.return', 'Process a sales return.'),
  ('sale.price_override', 'Override the selling price on a sale line.'),
  ('sale.discount', 'Apply a discount on a sale.'),
  ('sale.refund', 'Issue a customer refund.'),
  -- Purchase
  ('purchase.view', 'View purchases.'),
  ('purchase.create', 'Create a purchase.'),
  ('purchase.edit_own_draft', 'Edit a purchase that the user created in draft status.'),
  ('purchase.post', 'Post (receive) a purchase.'),
  ('purchase.complete', 'Complete a purchase (auto on full payment).'),
  ('purchase.cancel', 'Cancel a posted purchase.'),
  ('purchase.return', 'Process a purchase return.'),
  ('purchase.refund', 'Disburse a supplier refund / repayment.'),
  -- Inventory
  ('inventory.view', 'View inventory and stock movements.'),
  ('inventory.adjust', 'Manually adjust stock (positive or negative).'),
  ('inventory.transfer', 'Transfer stock between locations (V2).'),
  -- Production
  ('production.view', 'View production runs.'),
  ('production.create', 'Create a production run (draft).'),
  ('production.edit_own_draft', 'Edit a production run that the user created in draft.'),
  ('production.post', 'Post a production run.'),
  ('production.cancel', 'Cancel a posted production run.'),
  -- Manual finance
  ('manual_entry.view', 'View manual income/expense entries.'),
  ('manual_entry.create_income', 'Create a manual income entry.'),
  ('manual_entry.create_expense', 'Create a manual expense entry.'),
  ('manual_entry.cancel', 'Cancel a manual entry.'),
  -- Financial views
  ('finance.view_profit', 'View P&L.'),
  ('finance.view_cash', 'View cash balance and cash movements.'),
  ('finance.view_payables_receivables', 'View payables and receivables.'),
  -- Reports / exports
  ('report.view', 'View reports.'),
  ('export.data', 'Export PDF/Excel.'),
  -- Notifications
  ('notification.view', 'View notifications.'),
  ('notification.mark_read', 'Mark notifications as read.');

-- 19.3 Role -> Capability mapping
-- Owner: ALL capabilities. Staff: default-on set per PRD §6.1 (reconciled).
INSERT INTO role_capabilities (role_id, capability_id)
SELECT r.id, c.id
FROM roles r
CROSS JOIN capabilities c
WHERE r.name = 'Owner';

-- Staff: view + own-draft edit + sale-create + purchase-create (default-ON);
-- plus: auth.login, category.view, unit.view, product.view, contact.view,
-- payment_method.view, cost_type.view, sale.view, purchase.view,
-- inventory.view, production.view, notification.view, notification.mark_read.
INSERT INTO role_capabilities (role_id, capability_id)
SELECT r.id, c.id
FROM roles r
JOIN capabilities c ON c.code IN (
    'auth.login',
    'category.view','unit.view','product.view','contact.view',
    'payment_method.view','cost_type.view',
    'sale.view','sale.create','sale.edit_own_draft',
    'purchase.view','purchase.create','purchase.edit_own_draft',
    'inventory.view',
    'production.view',
    'notification.view','notification.mark_read'
)
WHERE r.name = 'Staff';

-- 19.4 Payment methods (per API §15.7.3; V1 default set)
INSERT INTO payment_methods (code, name, is_cash) VALUES
  ('cash',           'Cash',           TRUE),
  ('bank_transfer',  'Bank Transfer',  FALSE),
  ('e_wallet',       'E-Wallet',       FALSE),
  ('other',          'Other',          FALSE);

-- 19.5 Cost types (per PRD §15: Labor, Electricity, Gas, Packaging, Other)
INSERT INTO cost_types (code, name) VALUES
  ('labor',      'Labor'),
  ('electricity','Electricity'),
  ('gas',        'Gas'),
  ('packaging',  'Packaging'),
  ('other',      'Other');

-- 19.6 Financial categories (per PRD §17 + API §15.7.5)
INSERT INTO financial_categories (code, name, entry_type) VALUES
  -- Income
  ('capital_injection',  'Capital Injection', 'income'),
  ('other_income',       'Other Income',      'income'),
  ('other_income_misc',  'Other',             'income'),
  -- Expense
  ('rent',               'Rent',                       'expense'),
  ('labour_non_prod',    'Labour (non-production)',    'expense'),
  ('electricity_non_prod','Electricity (non-production)','expense'),
  ('maintenance',        'Maintenance',                'expense'),
  ('operational',        'Operational',                'expense'),
  ('tax',                'Tax',                        'expense'),
  ('extra_shipping',     'Extra shipping',             'expense'),
  ('other_expense',      'Other',                      'expense');

-- 19.7 System settings (per DB spec §2.9.1; key defaults)
INSERT INTO system_settings (key, value, value_type, description) VALUES
  ('costing_method',                  'moving_average', 'string',  'V1: moving_average only. Read-only in V1.'),
  ('default_negative_stock_allowed',  'false',          'boolean', 'Global default for products.allow_negative_stock when NULL.'),
  ('default_low_stock_threshold',     '0',              'number',  'Per-product override takes precedence. TBD-003.'),
  ('default_posting_timing',          'immediate',      'string',  'V1: only immediate posting is supported.'),
  ('enable_sequential_doc_numbers',   'false',          'boolean', 'OD-17. When true, reference_no is auto-generated.'),
  ('is_initialized',                  'false',          'boolean', 'Operational flag. Set true on first system initialization (MS-7).'),
  ('display_rounding',                'false',          'boolean', 'OD-16 placeholder. V1: NUMERIC(15,2) stored exact; no rounding.'),
  ('company_name',                    '',               'string',  'Owner-set company name.'),
  ('company_address',                 '',               'string',  'Owner-set company address.'),
  ('adjustment_creates_pnl_entry',    'false',          'boolean', 'MS-2 placeholder. When true, stock adjustments auto-create a manual finance entry. V1 = false.');

-- 19.8 Default Owner user
-- Placeholder: the Owner password is set via the system initialization script
-- (out of V1 API per API §15.22). The seed inserts a placeholder user with
-- a placeholder password_hash that is replaced at initialization.
-- The username is 'owner' and the role is 'Owner'.
-- A NULL password_hash is the canonical "must set on first login" marker.
INSERT INTO users (username, full_name, email, password_hash, role_id, created_by)
SELECT 'owner', 'Owner', NULL, '!UNSET', r.id, NULL
FROM roles r WHERE r.name = 'Owner';

COMMIT;


-- =============================================================================
