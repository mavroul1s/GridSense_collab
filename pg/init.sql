-- GridSense PostgreSQL schema. Auto-run by the postgres entrypoint.
-- Money is NUMERIC (never FLOAT); tariff/line-item structures are JSONB with GIN indexes.

CREATE TABLE IF NOT EXISTS consumer_accounts (
    premise_id   VARCHAR(50)   PRIMARY KEY,
    name         VARCHAR(200)  NOT NULL,
    address      TEXT          NOT NULL,
    tariff_info  JSONB         NOT NULL DEFAULT '{}',
    balance      NUMERIC(12,2) NOT NULL DEFAULT 0.00,
    created_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- GIN index enables @> containment queries on tariff_info.
CREATE INDEX IF NOT EXISTS idx_consumer_tariff_gin
    ON consumer_accounts USING GIN (tariff_info);

CREATE INDEX IF NOT EXISTS idx_consumer_name
    ON consumer_accounts (name);


-- FK ON DELETE RESTRICT prevents deleting an account that still has invoices.
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

    -- One invoice per account per billing period.
    CONSTRAINT uq_invoice_period UNIQUE (premise_id, period_start, period_end)
);

CREATE INDEX IF NOT EXISTS idx_invoice_premise
    ON invoices (premise_id);

CREATE INDEX IF NOT EXISTS idx_invoice_status
    ON invoices (status);

CREATE INDEX IF NOT EXISTS idx_invoice_line_items_gin
    ON invoices USING GIN (line_items);


-- Keep updated_at current on every UPDATE.
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
