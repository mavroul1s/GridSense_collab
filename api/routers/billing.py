# api/routers/billing.py
# PostgreSQL-backed billing endpoints
#
# Endpoints:
#   GET  /billing/account/{premise_id} → fetch account + tariff JSONB
#   POST /billing/invoice               → create invoice + update balance atomically
#
# Key patterns from slides:
#   Week 4 Slide 36: @> JSONB containment operator uses GIN index
#   Week 4 Slide 35: asyncpg $1/$2 placeholders — NOT %s (Cassandra style)
#   Part A A.5 Case 5: billing must be ACID atomic — execute_transaction()
#   ensures invoice INSERT + balance UPDATE are all-or-nothing.
#
# asyncpg JSONB note:
#   asyncpg does not auto-serialise Python dicts to JSONB.
#   Dicts must be passed as json.dumps(dict) strings — asyncpg then
#   coerces the text into the JSONB column type automatically.

import json
from decimal import Decimal
from fastapi import APIRouter, HTTPException

from db.postgres import fetch_one, fetch_all, execute, execute_transaction, get_pool
from models.postgres import ConsumerAccountIn, ConsumerAccountOut, InvoiceIn, InvoiceOut

router = APIRouter(prefix="/billing", tags=["Billing"])


# ── DECIMAL SERIALISER ────────────────────────────────────────────
# asyncpg returns NUMERIC(12,2) columns as Python Decimal objects.
# FastAPI's JSON encoder cannot serialise Decimal — convert to float.
# Money precision is safe here: we only lose precision below 1 cent,
# which NUMERIC(12,2) does not store anyway.

def _decimal_to_float(row: dict | None) -> dict | None:
    """Convert Decimal values to float and parse JSONB string fields to dict."""
    if row is None:
        return None

    result = {}
    for k, v in row.items():
        if isinstance(v, Decimal):
            result[k] = float(v)
        elif k in ("tariff_info", "line_items") and isinstance(v, str):
            result[k] = json.loads(v)
        else:
            result[k] = v
    return result


# ── GET /billing/account/{premise_id} ────────────────────────────
@router.get("/account/{premise_id}", response_model=ConsumerAccountOut)
async def get_account(premise_id: str):
    """
    Retrieve a consumer account and its JSONB tariff structure.

    Week 4 Slide 36:
        SELECT name FROM products WHERE attributes @> '{"ram": "16GB"}';

    The GIN index on consumer_accounts.tariff_info (created in
    pg/init.sql) means @> containment queries scan the index,
    not the full table. For a standard primary-key lookup (this
    endpoint) the B-Tree index on premise_id is used instead —
    the @> example is demonstrated in get_accounts_by_tariff() below.

    asyncpg uses $1, $2 positional placeholders.
    cassandra-driver uses %s — do not mix them up.
    """
    row = await fetch_one(
        """
        SELECT
            premise_id,
            name,
            address,
            tariff_info,
            balance,
            created_at,
            updated_at
        FROM consumer_accounts
        WHERE premise_id = $1
        """,
        premise_id
    )

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Account '{premise_id}' not found"
        )

    return _decimal_to_float(row)


# ── GET /billing/accounts/tariff ─────────────────────────────────
# Bonus endpoint: demonstrates @> GIN index usage (Week 4 Slide 36).
# Not in the 14 mandatory endpoints but demonstrates JSONB containment
# which is cited in Part A A.5 Case 5.

@router.get("/accounts/tariff")
async def get_accounts_by_tariff(tariff_class: str):
    """
    Return all accounts matching a tariff class using @> containment.

    Week 4 Slide 36:
        WHERE attributes @> '{"ram": "16GB"}'

    The @> operator checks whether tariff_info JSONB contains the
    given subset. The GIN index on tariff_info (pg/init.sql) makes
    this a fast index scan rather than a sequential table scan across
    1.2 million accounts.

    Example:
        GET /billing/accounts/tariff?tariff_class=residential
        → WHERE tariff_info @> '{"tariff_class": "residential"}'
    """
    # Build the containment filter value as a JSON string.
    # asyncpg coerces this text into JSONB automatically when the
    # column type is JSONB — we pass it as a plain string parameter.
    subset = json.dumps({"tariff_class": tariff_class})

    rows = await fetch_all(
        """
        SELECT premise_id, name, tariff_info, balance
        FROM consumer_accounts
        WHERE tariff_info @> $1::jsonb
        ORDER BY premise_id
        LIMIT 100
        """,
        subset
    )

    return [_decimal_to_float(row) for row in rows]


# ── POST /billing/invoice ─────────────────────────────────────────
@router.post("/invoice", response_model=InvoiceOut, status_code=201)
async def create_invoice(invoice: InvoiceIn):
    """
    Create an invoice and update the account balance atomically.

    Part A A.5 Case 5 — ACID atomicity:
        A billing run that fails midway must not leave the database
        in a partial state (invoice inserted but balance not updated,
        or vice versa). execute_transaction() wraps both operations
        in a single PostgreSQL transaction — if either fails, both
        roll back automatically.

    amount_due is computed SERVER-SIDE from line_items.
    It is not taken from the client request — this prevents billing
    manipulation where a client could submit an invoice with a
    fraudulently low amount_due while line_items sum to a higher value.

    Duplicate invoice guard: the UNIQUE constraint on
    (premise_id, period_start, period_end) in pg/init.sql catches
    duplicate billing runs at the DB level. We also check explicitly
    to return a clear 409 rather than letting asyncpg raise an
    UniqueViolationError that would produce a 500.

    asyncpg JSONB: line_items dict must be serialised with
    json.dumps() before passing as a parameter — asyncpg does not
    auto-serialise Python dicts to JSONB.
    """
    # ── 1. Verify account exists ──────────────────────────────────
    account = await fetch_one(
        "SELECT premise_id, balance FROM consumer_accounts WHERE premise_id = $1",
        invoice.premise_id
    )
    if account is None:
        raise HTTPException(
            status_code=404,
            detail=f"Account '{invoice.premise_id}' not found"
        )

    # ── 2. Check for duplicate invoice ───────────────────────────
    existing = await fetch_one(
        """
        SELECT invoice_id FROM invoices
        WHERE premise_id   = $1
          AND period_start = $2
          AND period_end   = $3
        """,
        invoice.premise_id,
        invoice.period_start,
        invoice.period_end,
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Invoice for account '{invoice.premise_id}' "
                f"period {invoice.period_start} – {invoice.period_end} already exists"
            )
        )

    # ── 3. Compute amount_due server-side ─────────────────────────
    # Never trust the client for monetary totals.
    # Round to 2 decimal places — matches NUMERIC(12,2) in DB.
    amount_due = round(
        sum(item.amount for item in invoice.line_items),
        2
    )

    # ── 4. Serialise line_items for JSONB ─────────────────────────
    # asyncpg requires JSONB parameters as JSON strings.
    # model_dump(mode="json") converts date/datetime fields inside
    # InvoiceLineItem to strings before json.dumps().
    line_items_json = json.dumps(
        [item.model_dump(mode="json") for item in invoice.line_items]
    )

    # ── 5. Atomic transaction: INSERT invoice + UPDATE balance ────
    # Part A A.5 Case 5: these two statements are all-or-nothing.
    # If the balance UPDATE fails (e.g. constraint violation), the
    # invoice INSERT is rolled back automatically by PostgreSQL.
    current_balance = float(account["balance"])
    new_balance = round(current_balance - amount_due, 2)

    await execute_transaction([
        (
            """
            INSERT INTO invoices
                (premise_id, period_start, period_end,
                 consumption_kwh, amount_due, line_items, status)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb, 'issued')
            """,
            invoice.premise_id,
            invoice.period_start,
            invoice.period_end,
            invoice.consumption_kwh,
            amount_due,
            line_items_json,
        ),
        (
            """
            UPDATE consumer_accounts
            SET balance    = $1,
                updated_at = NOW()
            WHERE premise_id = $2
            """,
            new_balance,
            invoice.premise_id,
        ),
    ])

    # ── 6. Fetch and return the created invoice ───────────────────
    created = await fetch_one(
        """
        SELECT
            invoice_id, premise_id, period_start, period_end,
            consumption_kwh, amount_due, line_items, status, issued_at
        FROM invoices
        WHERE premise_id   = $1
          AND period_start = $2
          AND period_end   = $3
        """,
        invoice.premise_id,
        invoice.period_start,
        invoice.period_end,
    )

    return _decimal_to_float(created)