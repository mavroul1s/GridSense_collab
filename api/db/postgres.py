# api/db/postgres.py
import os
import asyncpg
from asyncpg import Pool
from dotenv import load_dotenv

load_dotenv()

_pool: Pool | None = None


async def connect() -> None:
    global _pool

    user     = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")
    database = os.getenv("POSTGRES_DB")

    if not all([user, password, database]):
        raise RuntimeError(
            "Missing PostgreSQL credentials — "
            "POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB must be set in .env"
        )

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


async def disconnect() -> None:
    global _pool
    if _pool:
        await _pool.close()
        print("[PostgreSQL] Connection pool closed")
    _pool = None


def get_pool() -> Pool:
    if _pool is None:
        raise RuntimeError("PostgreSQL pool not initialised — call connect() first")
    return _pool


async def fetch_one(query: str, *args) -> dict | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, *args)
        return dict(row) if row else None


async def fetch_all(query: str, *args) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
        return [dict(row) for row in rows]


async def execute(query: str, *args) -> str:
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.execute(query, *args)


async def execute_transaction(queries: list[tuple]) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            for query, *args in queries:
                await conn.execute(query, *args)


def jsonb_contains_filter(field: str, subset: dict) -> str:
    import json
    return f"{field} @> '{json.dumps(subset)}'"