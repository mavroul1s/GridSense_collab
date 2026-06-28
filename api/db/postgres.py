# PostgreSQL connection module — asyncpg connection pool.

import os
import asyncpg
from asyncpg import Pool
from dotenv import load_dotenv

load_dotenv()

# Connection state — a shared pool of connections.
_pool: Pool | None = None


# Create the connection pool from credentials in the environment.
async def connect() -> None:
    """Create the connection pool from credentials in the environment."""
    global _pool

    # Read required credentials.
    user     = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")
    database = os.getenv("POSTGRES_DB")

    # Fail fast if any are missing.
    if not all([user, password, database]):
        raise RuntimeError(
            "Missing PostgreSQL credentials — "
            "POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB must be set in .env"
        )

    # Open the pool against the Docker service name.
    _pool = await asyncpg.create_pool(
        host="billing-db",
        port=5432,
        user=user,
        password=password,
        database=database,
        min_size=2,
        max_size=10,
    )
    print(f"[PostgreSQL] Pool connected to billing-db:5432 — database: {database}")


# Close the pool and clear connection state.
async def disconnect() -> None:
    """Close the pool and clear connection state."""
    global _pool
    if _pool:
        await _pool.close()
        print("[PostgreSQL] Connection pool closed")
    _pool = None


# Return the active pool (raises if connect() was not called).
def get_pool() -> Pool:
    """Return the active pool, or raise if connect() was not called."""
    if _pool is None:
        raise RuntimeError("PostgreSQL pool not initialised — call connect() first")
    return _pool


# Run a query and return the first row as a dict (or None).
async def fetch_one(query: str, *args) -> dict | None:
    """Run a query and return the first row as a dict, or None."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, *args)
        return dict(row) if row else None


# Run a query and return all rows as dicts.
async def fetch_all(query: str, *args) -> list[dict]:
    """Run a query and return all rows as dicts."""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
        return [dict(row) for row in rows]


# Run a single write statement and return its status tag.
async def execute(query: str, *args) -> str:
    """Run a single write statement and return its status tag."""
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.execute(query, *args)


# Run several statements atomically (all commit or all roll back).
async def execute_transaction(queries: list[tuple]) -> None:
    """Run several statements atomically — all commit or all roll back."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for query, *args in queries:
                await conn.execute(query, *args)


# Build a JSONB @> containment filter expression.
def jsonb_contains_filter(field: str, subset: dict) -> str:
    """Build a JSONB @> containment filter expression."""
    import json
    return f"{field} @> '{json.dumps(subset)}'"
