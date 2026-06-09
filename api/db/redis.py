# api/db/redis.py
# Redis async connection module for GridSense API
#
# redis-py v5.x ships with a native async interface under redis.asyncio.
# No run_in_executor needed — fully compatible with FastAPI's event loop.
#
# Two responsibilities:
#   1. Cache-aside pattern for sensor summaries (Week 4 Slide 24)
#   2. Pub/Sub for real-time alert distribution (alerts endpoints)

import os
import json
import redis.asyncio as aioredis
from redis.asyncio import Redis
from dotenv import load_dotenv

load_dotenv()

# ── CONNECTION STATE ──────────────────────────────────────────────
# Single async Redis client — redis-py manages an internal connection pool.
_redis: Redis | None = None


# ── CONNECT ───────────────────────────────────────────────────────
async def connect() -> None:
    """
    Initialise the async Redis client at application startup.
    Called from main.py lifespan context manager.

    URI: redis://cache:6379
    'cache' is the Docker service name (Trap 10 fix).
    Week 4 Slide 16: containers discover each other by service name.

    decode_responses=True: Redis stores bytes internally.
    With decode_responses=True, all responses are automatically
    decoded to Python strings — no manual .decode('utf-8') needed.
    """
    global _redis

    redis_url = os.getenv("REDIS_URL", "redis://cache:6379")

    _redis = aioredis.from_url(
        redis_url,
        decode_responses=True,   # return str not bytes
    )

    # Ping to verify connectivity at startup
    await _redis.ping()
    print(f"[Redis] Connected to {redis_url}")


# ── DISCONNECT ────────────────────────────────────────────────────
async def disconnect() -> None:
    """
    Close the Redis connection at application shutdown.
    Called from main.py lifespan context manager on exit.
    """
    global _redis
    if _redis:
        await _redis.aclose()
        print("[Redis] Connection closed")
    _redis = None


# ── CLIENT ACCESSOR ───────────────────────────────────────────────
def get_redis() -> Redis:
    """
    Returns the active async Redis client.
    Raises RuntimeError if connect() has not been called.
    """
    if _redis is None:
        raise RuntimeError("Redis client not initialised — call connect() first")
    return _redis


# ── CACHE-ASIDE HELPERS ───────────────────────────────────────────
# Week 4 Slide 24: the cache-aside pattern
#   1. Check Redis first
#   2. Cache HIT  → return immediately
#   3. Cache MISS → query database → store in Redis with TTL → return

async def cache_get(key: str) -> dict | None:
    """
    Retrieve a cached JSON value by key.
    Returns parsed dict on cache HIT, None on cache MISS.

    The router checks the return value:
        cached = await cache_get(key)
        if cached:
            return cached          # HIT — served from RAM
        data = await query_db()   # MISS — hit the database
        await cache_set(key, data, ttl=30)
        return data

    Args:
        key: Redis key string (e.g. 'summary:SENSOR_042')

    Returns:
        Parsed dict if key exists and has not expired, None otherwise
    """
    r = get_redis()
    value = await r.get(key)
    if value is None:
        return None
    return json.loads(value)


async def cache_set(key: str, data: dict, ttl: int = 30) -> None:
    """
    Store a dict as JSON in Redis with a TTL expiry.

    Week 4 Slide 25: SETEX sets a value with automatic expiry.
    After `ttl` seconds Redis evicts the key automatically —
    no background job or scheduled DELETE required.

    Week 4 Slide 41 lab example:
        SETEX laptop:ram 10 "16GB"
        GET laptop:ram      # Returns "16GB"
        # Wait 10 seconds
        GET laptop:ram      # Returns (nil) — auto-expired

    Default TTL=30 seconds matches the assignment's 5-second transformer
    status cache requirement (we use 30s for summaries, callers can override).

    Args:
        key : Redis key string
        data: dict to serialise and store
        ttl : expiry in seconds (default 30)
    """
    r = get_redis()
    await r.setex(key, ttl, json.dumps(data))


async def cache_delete(key: str) -> None:
    """
    Explicitly evict a cache key before its TTL expires.
    Used when a sensor reading is posted and the cached summary
    for that sensor is now stale.

    Args:
        key: Redis key string to delete
    """
    r = get_redis()
    await r.delete(key)


# ── TRANSFORMER STATUS CACHE ──────────────────────────────────────
# Part A A.5 Case 3: cache overload status of 22,000 transformers
# with 5-second TTL. Dedicated helpers with the correct key prefix
# and TTL so the router does not need to know the implementation.

async def set_transformer_status(asset_id: str, status: str) -> None:
    """
    Cache a transformer's overload status for 5 seconds.

    Part A A.5 Case 3:
        SETEX transformer:TX_001:status 5 "OVERLOADED"
    After 5 seconds Redis evicts automatically — stale status
    cannot outlive the underlying reality.

    Args:
        asset_id: transformer asset_id (e.g. 'TX_001')
        status  : status string ('NORMAL', 'OVERLOADED', 'FAULT')
    """
    r = get_redis()
    key = f"transformer:{asset_id}:status"
    await r.setex(key, 5, status)


async def get_transformer_status(asset_id: str) -> str | None:
    """
    Retrieve a transformer's cached overload status.
    Returns None if the key has expired or was never set.

    Args:
        asset_id: transformer asset_id

    Returns:
        Status string or None
    """
    r = get_redis()
    key = f"transformer:{asset_id}:status"
    return await r.get(key)


# ── PUB/SUB HELPERS ───────────────────────────────────────────────
# Used by POST /alerts/publish and GET /alerts/active

ALERTS_CHANNEL = "gridsense:alerts"


async def publish_alert(alert: dict) -> None:
    """
    Publish an alert dict to the gridsense:alerts Redis channel.

    Redis Pub/Sub delivers the message to all current subscribers
    immediately. Subscribers that are not connected at publish time
    do not receive the message — Pub/Sub is fire-and-forget, not
    a persistent queue.

    For durable fault event storage, the relay_events Cassandra table
    is the system of record (Part A A.3.a + A.4.c).

    Args:
        alert: dict containing alert payload (sensor_id, type, value, etc.)
    """
    r = get_redis()
    await r.publish(ALERTS_CHANNEL, json.dumps(alert))


async def get_active_alerts(max_alerts: int = 50) -> list[dict]:
    """
    Retrieve recently published alerts from a Redis list used as a
    rolling buffer.

    Pure Pub/Sub has no history — a client that connects after publish
    sees nothing. To support GET /alerts/active, publish_alert() also
    pushes to a Redis list with LPUSH and caps it at max_alerts with
    LTRIM. This gives the endpoint a queryable recent-alerts buffer
    while keeping memory bounded.

    The list key: 'gridsense:alerts:recent'
    LPUSH prepends — index 0 is always the most recent alert.
    LTRIM keeps only the last max_alerts entries.

    Args:
        max_alerts: maximum number of recent alerts to return (default 50)

    Returns:
        List of alert dicts, most recent first
    """
    r = get_redis()
    raw_alerts = await r.lrange("gridsense:alerts:recent", 0, max_alerts - 1)
    return [json.loads(a) for a in raw_alerts]


async def push_alert_to_buffer(alert: dict, max_size: int = 200) -> None:
    """
    Push an alert to the rolling recent-alerts list and trim to max_size.
    Called alongside publish_alert() so GET /alerts/active has history.

    LPUSH + LTRIM is the standard Redis pattern for a bounded recent-items
    list — O(1) push, O(N) trim where N is max_size (constant).

    Args:
        alert   : alert dict to store
        max_size: maximum list length (default 200)
    """
    r = get_redis()
    pipe = r.pipeline()
    await pipe.lpush("gridsense:alerts:recent", json.dumps(alert))
    await pipe.ltrim("gridsense:alerts:recent", 0, max_size - 1)
    await pipe.execute()