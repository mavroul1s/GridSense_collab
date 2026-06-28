# PostgreSQL-backed billing endpoints.
# asyncpg uses $1/$2 placeholders and needs JSONB params passed as json.dumps() strings.

import json
from decimal import Decimal
from fastapi import APIRouter, HTTPException

from db.postgres import fetch_one, fetch_all, execute, execute_transaction, get_pool
from models.postgres import ConsumerAccountIn, ConsumerAccountOut, InvoiceIn, InvoiceOut

router = APIRouter(prefix="/billing", tags=["Billing"])


# Normalise a DB row for JSON output (Decimal→float, JSONB str→dict).
def _decimal_to_float(row: dict | None) -> dict | None:
    """Convert NUMERIC Decimals to float and parse JSONB string fields to dict."""
    if row is None:
        return None

    # Walk each column and normalise its type for JSON output.
    result = {}
    for k, v in row.items():
        if isinstance(v, Decimal):
            result[k] = float(v)
        elif k in ("tariff_info", "line_items") and isinstance(v, str):
            result[k] = json.loads(v)
        else:
            result[k] = v
    return result


# GET /billing/account/{id} — fetch a consumer account.
@router.get("/account/{premise_id}", response_model=ConsumerAccountOut)
async def get_account(premise_id: str):
    """Fetch a consumer account and its JSONB tariff structure."""
    # Look up the account by primary key.
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

    # 404 if no such account.
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Account '{premise_id}' not found"
        )

    return _decimal_to_float(row)


# GET /billing/accounts/tariff — list accounts by tariff class.
@router.get("/accounts/tariff")
async def get_accounts_by_tariff(tariff_class: str):
    """List accounts matching a tariff class via the @> containment operator (GIN index)."""
    # Build the JSONB subset to match against.
    subset = json.dumps({"tariff_class": tariff_class})

    # Containment query — uses the GIN index, not a full scan.
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


# POST /billing/invoice — create an invoice and adjust the balance.
@router.post("/invoice", response_model=InvoiceOut, status_code=201)
async def create_invoice(invoice: InvoiceIn):
    """Create an invoice and update the account balance in one atomic transaction."""
    # Verify the account exists.
    account = await fetch_one(
        "SELECT premise_id, balance FROM consumer_accounts WHERE premise_id = $1",
        invoice.premise_id
    )
    if account is None:
        raise HTTPException(
            status_code=404,
            detail=f"Account '{invoice.premise_id}' not found"
        )

    # Reject a duplicate invoice for the same period (clean 409 vs DB 500).
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

    # Compute the total server-side; never trust the client's figure.
    amount_due = round(
        sum(item.amount for item in invoice.line_items),
        2
    )

    # Serialise line items for the JSONB column.
    line_items_json = json.dumps(
        [item.model_dump(mode="json") for item in invoice.line_items]
    )

    # Compute the new balance.
    current_balance = float(account["balance"])
    new_balance = round(current_balance - amount_due, 2)

    # Insert the invoice and update the balance atomically.
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

    # Read back and return the created invoice.
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
