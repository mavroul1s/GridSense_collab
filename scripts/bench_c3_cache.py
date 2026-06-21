# scripts/bench_c3_cache.py
# Part C.3 — Redis cache effectiveness (1000 requests, warm vs cold)
#
# Tests the real /sensors/{id}/summary endpoint over HTTP — the actual
# cache-aside code path in routers/sensors.py (Week 4 Slide 24 pattern).
#
# COLD phase: Redis key deleted before every request -> forces a
#             Cassandra query (~100 rows) every time. Worst case.
# WARM phase: cache primed once, then all requests should hit Redis.
#
# httpx is already installed as a fastapi dependency — no new packages.

import asyncio
import time
import statistics
import sys
sys.path.insert(0, "/app")

import httpx
from dotenv import load_dotenv
load_dotenv()

import db.redis as redis_db
from db.redis import cache_delete

SENSOR_ID  = "SENSOR_001"
CACHE_KEY  = f"summary:{SENSOR_ID}"
BASE_URL   = "http://api:8000"
N_REQUESTS = 1000


async def cold_phase(client):
    """Force a cache miss before every request — every read hits Cassandra."""
    latencies = []
    for i in range(N_REQUESTS):
        await cache_delete(CACHE_KEY)
        start = time.perf_counter()
        resp = await client.get(f"{BASE_URL}/sensors/{SENSOR_ID}/summary")
        elapsed_ms = (time.perf_counter() - start) * 1000
        resp.raise_for_status()
        latencies.append(elapsed_ms)
        if (i + 1) % 200 == 0:
            print(f"  COLD {i+1}/{N_REQUESTS}")
    return latencies


async def warm_phase(client):
    """Prime the cache once, then every subsequent request should hit Redis."""
    await cache_delete(CACHE_KEY)
    await client.get(f"{BASE_URL}/sensors/{SENSOR_ID}/summary")  # priming, discarded

    latencies = []
    for i in range(N_REQUESTS):
        start = time.perf_counter()
        resp = await client.get(f"{BASE_URL}/sensors/{SENSOR_ID}/summary")
        elapsed_ms = (time.perf_counter() - start) * 1000
        resp.raise_for_status()
        latencies.append(elapsed_ms)
        if (i + 1) % 200 == 0:
            print(f"  WARM {i+1}/{N_REQUESTS}")
    return latencies


def percentile(data, p):
    s = sorted(data)
    idx = min(int(len(s) * p / 100), len(s) - 1)
    return s[idx]


def print_stats(name, latencies):
    avg = statistics.mean(latencies)
    mn, mx = min(latencies), max(latencies)
    std = statistics.stdev(latencies)
    p50, p95, p99 = percentile(latencies, 50), percentile(latencies, 95), percentile(latencies, 99)
    throughput = len(latencies) / (sum(latencies) / 1000)

    print(f"\n{name}")
    print(f"  Requests     : {len(latencies)}")
    print(f"  Avg          : {avg:.3f} ms")
    print(f"  Min          : {mn:.3f} ms")
    print(f"  Max          : {mx:.3f} ms")
    print(f"  StdDev       : {std:.3f} ms")
    print(f"  P50 (median) : {p50:.3f} ms")
    print(f"  P95          : {p95:.3f} ms")
    print(f"  P99          : {p99:.3f} ms")
    print(f"  Throughput   : {throughput:.1f} req/sec")
    return avg, p50, p95, p99


async def main():
    await redis_db.connect()

    print("=" * 70)
    print("C.3 — Redis Cache Effectiveness")
    print(f"Sensor: {SENSOR_ID} | {N_REQUESTS} requests per phase")
    print("=" * 70)

    async with httpx.AsyncClient(timeout=30.0) as client:
        print("\nRunning COLD phase (cache miss every request)...")
        cold_latencies = await cold_phase(client)

        print("\nRunning WARM phase (cache primed once)...")
        warm_latencies = await warm_phase(client)

    cold_avg, cold_p50, cold_p95, cold_p99 = print_stats(
        "COLD — cache miss -> Cassandra every request", cold_latencies)
    warm_avg, warm_p50, warm_p95, warm_p99 = print_stats(
        "WARM — cache hit -> Redis every request", warm_latencies)

    print("\n" + "=" * 70)
    print("Speedup Analysis")
    print("=" * 70)
    print(f"  Avg speedup : {cold_avg/warm_avg:6.1f}x   ({cold_avg:.2f}ms -> {warm_avg:.2f}ms)")
    print(f"  P50 speedup : {cold_p50/warm_p50:6.1f}x   ({cold_p50:.2f}ms -> {warm_p50:.2f}ms)")
    print(f"  P95 speedup : {cold_p95/warm_p95:6.1f}x   ({cold_p95:.2f}ms -> {warm_p95:.2f}ms)")
    print(f"  P99 speedup : {cold_p99/warm_p99:6.1f}x   ({cold_p99:.2f}ms -> {warm_p99:.2f}ms)")

    await redis_db.disconnect()


if __name__ == "__main__":
    asyncio.run(main())