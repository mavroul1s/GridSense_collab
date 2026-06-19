# scripts/bench_c4_mongo_vs_postgres.py
# Part C.4 — MongoDB vs PostgreSQL JSONB query comparison
# Same 30 records, 3 equivalent queries, both stores
#
# Methodology: an identical 30-record synthetic dataset is seeded into
# a temporary MongoDB collection (bench_equipment) and a temporary
# PostgreSQL table with a JSONB column + GIN index (bench_equipment).
# The SAME 3 queries are run against both — this isolates the JSON
# query engine itself, rather than comparing differently-shaped
# production data (MongoDB's 30 equipment docs vs PostgreSQL's 100
# billing accounts, which differ in both row count and structure).

import asyncio
import time
import statistics
import json
import random
import sys
sys.path.insert(0, "/app")

from dotenv import load_dotenv
load_dotenv()

import db.mongo as mongo_db
import db.postgres as postgres_db

N_RECORDS  = 30
N_ITER     = 50   # query iterations for timing stability


def build_dataset():
    """30 identical synthetic records — same shape for both stores."""
    categories = ["Transformer", "SmartMeter", "ProtectionRelay"]
    statuses   = ["active", "maintenance", "decommissioned"]
    records = []
    for i in range(1, N_RECORDS + 1):
        records.append({
            "asset_id":  f"BENCH_{i:03d}",
            "category":  categories[i % 3],
            "rating_kVA": random.randint(50, 800),
            "metadata": {
                "status":       statuses[i % 3],
                "manufacturer": random.choice(["ABB", "Siemens", "GE"]),
                "install_year": random.randint(2010, 2024),
            }
        })
    return records


# ── MONGODB SETUP + QUERIES ───────────────────────────────────────
async def setup_mongo(records):
    db = mongo_db.get_db()
    col = db.bench_equipment
    await col.delete_many({})   # clean slate
    await col.insert_many(records)
    print(f"[MongoDB] Seeded {len(records)} records into bench_equipment")
    return col


async def mongo_q1_exact_match(col):
    cursor = col.find({"category": "Transformer"})
    return await cursor.to_list(length=None)


async def mongo_q2_range(col):
    cursor = col.find({"rating_kVA": {"$gt": 300}})
    return await cursor.to_list(length=None)


async def mongo_q3_nested(col):
    cursor = col.find({"metadata.status": "active"})
    return await cursor.to_list(length=None)


# ── POSTGRESQL SETUP + QUERIES ────────────────────────────────────
async def setup_postgres(records):
    await postgres_db.execute("DROP TABLE IF EXISTS bench_equipment")
    await postgres_db.execute("""
        CREATE TABLE bench_equipment (
            asset_id TEXT PRIMARY KEY,
            data     JSONB NOT NULL
        )
    """)
    await postgres_db.execute(
        "CREATE INDEX idx_bench_gin ON bench_equipment USING GIN (data)"
    )
    for r in records:
        await postgres_db.execute(
            "INSERT INTO bench_equipment (asset_id, data) VALUES ($1, $2::jsonb)",
            r["asset_id"], json.dumps(r)
        )
    print(f"[PostgreSQL] Seeded {len(records)} records into bench_equipment")


async def pg_q1_exact_match():
    return await postgres_db.fetch_all(
        "SELECT * FROM bench_equipment WHERE data @> $1::jsonb",
        json.dumps({"category": "Transformer"})
    )


async def pg_q2_range():
    return await postgres_db.fetch_all(
        "SELECT * FROM bench_equipment WHERE (data->>'rating_kVA')::int > 300"
    )


async def pg_q3_nested():
    return await postgres_db.fetch_all(
        "SELECT * FROM bench_equipment WHERE data @> $1::jsonb",
        json.dumps({"metadata": {"status": "active"}})
    )


# ── TIMING HARNESS ────────────────────────────────────────────────
async def time_query(fn, n=N_ITER):
    latencies = []
    result_count = None
    for _ in range(n):
        start = time.perf_counter()
        result = await fn()
        elapsed_ms = (time.perf_counter() - start) * 1000
        latencies.append(elapsed_ms)
        result_count = len(result)
    return {
        "avg": statistics.mean(latencies),
        "min": min(latencies),
        "max": max(latencies),
        "std": statistics.stdev(latencies),
        "count": result_count,
    }


def print_row(label, stats):
    print(f"  {label:30s} | avg {stats['avg']:7.3f}ms | min {stats['min']:7.3f}ms | "
          f"max {stats['max']:7.3f}ms | std {stats['std']:6.3f}ms | rows={stats['count']}")


async def main():
    await mongo_db.connect()
    await postgres_db.connect()

    random.seed(42)   # reproducible dataset
    records = build_dataset()

    print("=" * 90)
    print("C.4 — MongoDB vs PostgreSQL JSONB Query Comparison")
    print(f"Identical {N_RECORDS}-record dataset seeded into both stores | {N_ITER} iterations/query")
    print("=" * 90)

    pg_records = [dict(r) for r in records]   # deep-enough copy before Mongo mutates records
    pg_records = [dict(r) for r in records]
    mongo_col = await setup_mongo(records)
    await setup_postgres(pg_records)

    print()
    print(f"{'Query':30s} | {'Engine':10s}")
    print("-" * 90)

    queries = [
        ("Q1 exact match (category)",  mongo_q1_exact_match, pg_q1_exact_match, mongo_col),
        ("Q2 numeric range (rating)",   mongo_q2_range,        pg_q2_range,        mongo_col),
        ("Q3 nested field (status)",    mongo_q3_nested,        pg_q3_nested,        mongo_col),
    ]

    summary = []

    for label, mongo_fn, pg_fn, col in queries:
        print(f"\n{label}")
        mongo_stats = await time_query(lambda: mongo_fn(col))
        pg_stats    = await time_query(pg_fn)
        print_row("MongoDB", mongo_stats)
        print_row("PostgreSQL JSONB", pg_stats)
        summary.append((label, mongo_stats["avg"], pg_stats["avg"]))

    print("\n" + "=" * 90)
    print("Summary — Average Latency Comparison")
    print("=" * 90)
    print(f"{'Query':30s} | {'MongoDB':>12s} | {'PostgreSQL':>12s} | {'Faster'}")
    print("-" * 90)
    for label, m_avg, p_avg in summary:
        faster = "MongoDB" if m_avg < p_avg else "PostgreSQL"
        ratio = max(m_avg, p_avg) / min(m_avg, p_avg)
        print(f"{label:30s} | {m_avg:10.3f}ms | {p_avg:10.3f}ms | {faster} ({ratio:.2f}x)")

    # Cleanup
    await mongo_col.delete_many({})
    await postgres_db.execute("DROP TABLE IF EXISTS bench_equipment")
    print("\n[Cleanup] Benchmark tables/collections removed")

    await mongo_db.disconnect()
    await postgres_db.disconnect()


if __name__ == "__main__":
    asyncio.run(main())