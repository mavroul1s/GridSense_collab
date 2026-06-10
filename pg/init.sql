-- pg/init.sql
-- GridSense PostgreSQL Schema
-- Week 4 rules: JSONB for flexible data, GIN index for @> containment queries
-- This file runs once at container startup via postgres entrypoint
-- (postgres:15 DOES watch /docker-entrypoint-initdb.d — unlike Cassandra)


-- ─────────────────────────────────────────────────────────────────────────────
-- TABLE 1: consumer_accounts
-- Serves: GET /billing/account/{premise_id}
--
-- premise_id   : natural primary key — matches SmartMeter.premise_id in Neo4j
-- name         : account holder full name
-- address      : postal address as plain text
-- tariff_info  : JSONB — stores tariff band, rate per kWh, standing charge,
--                time-of-use windows, regulatory surcharges.
--                Different tariff classes (residential/commercial/industrial)
--                have genuinely different field sets — JSONB is the correct
--                type per Week 4 Slide 32 (flexible attributes problem).
-- balance      : NUMERIC not FLOAT — monetary values must never use
--                floating point due to binary rounding errors.
--                NUMERIC(12,2) stores up to 9,999,999,999.99 exactly.
--
-- GIN INDEX on tariff_info:
-- Week 4 Slide 34: GIN creates an entry for every key AND value inside the
-- JSONB object. This allows PostgreSQL to instantly locate accounts matching
-- a specific tariff structure using the @> containment operator without a
-- full table scan.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS consumer_accounts (
    premise_id   VARCHAR(50)   PRIMARY KEY,
    name         VARCHAR(200)  NOT NULL,
    address      TEXT          NOT NULL,
    tariff_info  JSONB         NOT NULL DEFAULT '{}',
    balance      NUMERIC(12,2) NOT NULL DEFAULT 0.00,
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- GIN index: enables @> containment queries on tariff_info (Week 4 Slide 36)
CREATE INDEX IF NOT EXISTS idx_consumer_tariff_gin
    ON consumer_accounts USING GIN (tariff_info);

-- B-Tree index on name for account lookup by name (standard SQL index)
CREATE INDEX IF NOT EXISTS idx_consumer_name
    ON consumer_accounts (name);


-- ─────────────────────────────────────────────────────────────────────────────
-- TABLE 2: invoices
-- Serves: POST /billing/invoice
--
-- invoice_id   : surrogate primary key — SERIAL auto-increments
-- premise_id   : FK to consumer_accounts — links invoice to account
-- period_start : billing period start (date only, no time component)
-- period_end   : billing period end
-- consumption_kwh: total units consumed in the period
-- amount_due   : NUMERIC(12,2) — never FLOAT for money
-- line_items   : JSONB — stores per-tariff-band breakdown, surcharges,
--                credits. Structure varies by tariff class — JSONB is correct.
-- status       : CHECK constraint — only valid lifecycle values accepted
-- issued_at    : when the invoice was generated
--
-- FK with ON DELETE RESTRICT: prevents deleting an account that has invoices.
-- This is the ACID safety guarantee described in Part A A.5 Case 5 —
-- a billing run that fails midway cannot leave orphaned invoice records.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS invoices (
    invoice_id      SERIAL        PRIMARY KEY,
    premise_id      VARCHAR(50)   NOT NULL
                        REFERENCES consumer_accounts(premise_id)
                        ON DELETE RESTRICT,
    period_start    DATE          NOT NULL,
    period_end      DATE          NOT NULL,
    consumption_kwh NUMERIC(10,3) NOT NULL DEFAULT 0.000,
    amount_due      NUMERIC(12,2) NOT NULL DEFAULT 0.00,
    line_items      JSONB         NOT NULL DEFAULT '{}',
    status          VARCHAR(20)   NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','issued','paid','disputed','cancelled')),
    issued_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW(),

    -- Prevent duplicate invoices for the same account and period
    CONSTRAINT uq_invoice_period UNIQUE (premise_id, period_start, period_end)
);

-- Index on premise_id for fast lookup of all invoices for one account
CREATE INDEX IF NOT EXISTS idx_invoice_premise
    ON invoices (premise_id);

-- Index on status for dashboard queries (all pending invoices, all disputed)
CREATE INDEX IF NOT EXISTS idx_invoice_status
    ON invoices (status);

-- GIN index on line_items for @> containment queries on invoice breakdown
CREATE INDEX IF NOT EXISTS idx_invoice_line_items_gin
    ON invoices USING GIN (line_items);


-- ─────────────────────────────────────────────────────────────────────────────
-- TRIGGER: auto-update updated_at on consumer_accounts
-- Standard PostgreSQL pattern — TIMESTAMPTZ stays accurate after any UPDATE
-- ─────────────────────────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER trg_consumer_accounts_updated_at
    BEFORE UPDATE ON consumer_accounts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();