# Redis connection module — cache-aside helpers and Pub/Sub for alerts.

import os
import json
import redis.asyncio as aioredis
from redis.asyncio import Redis
from dotenv import load_dotenv

load_dotenv()

# Connection state — single client with an internal pool.
_redis: Redis | None = None


# Open the client and ping to verify the server responds.
async def connect() -> None:
    """Open the client (decode_responses returns str) and ping to verify."""
    global _redis

    # Read the URL (defaults to the Docker service name).
    redis_url = os.getenv("REDIS_URL", "redis://cache:6379")

    # Create the client.
    _redis = aioredis.from_url(redis_url, decode_responses=True)

    # Confirm the server responds.
    await _redis.ping()
    print(f"[Redis] Connected to {redis_url}")


# Close the client and clear connection state.
async def disconnect() -> None:
    """Close the client and clear connection state."""
    global _redis
    if _redis:
        await _redis.aclose()
        print("[Redis] Connection closed")
    _redis = None


# Return the active client (raises if connect() was not called).
def get_redis() -> Redis:
    """Return the active client, or raise if connect() was not called."""
    if _redis is None:
        raise RuntimeError("Redis client not initialised — call connect() first")
    return _redis


# ── Cache-aside helpers ───────────────────────────────────────────

# Return the cached dict for a key (None on a miss).
async def cache_get(key: str) -> dict | None:
    """Return the cached dict for a key, or None on a miss."""
    r = get_redis()
    value = await r.get(key)
    if value is None:
        return None
    return json.loads(value)


# Store a dict as JSON with a TTL (auto-evicted on expiry).
async def cache_set(key: str, data: dict, ttl: int = 30) -> None:
    """Store a dict as JSON with a TTL; Redis auto-evicts on expiry."""
    r = get_redis()
    await r.setex(key, ttl, json.dumps(data))


# Evict a key immediately (e.g. when its source data changed).
async def cache_delete(key: str) -> None:
    """Evict a key immediately (e.g. when its source data changed)."""
    r = get_redis()
    await r.delete(key)


# ── Transformer overload status (5s TTL) ──────────────────────────

# Cache a transformer's overload status for 5 seconds.
async def set_transformer_status(asset_id: str, status: str) -> None:
    """Cache a transformer's overload status for 5 seconds."""
    r = get_redis()
    key = f"transformer:{asset_id}:status"
    await r.setex(key, 5, status)


# Return a transformer's cached status (None if expired/unset).
async def get_transformer_status(asset_id: str) -> str | None:
    """Return a transformer's cached status, or None if expired/unset."""
    r = get_redis()
    key = f"transformer:{asset_id}:status"
    return await r.get(key)


# ── Pub/Sub + rolling buffer ──────────────────────────────────────
# Pub/Sub has no history, so a Redis list mirrors recent alerts for replay.

ALERTS_CHANNEL = "gridsense:alerts"


# Publish an alert to currently-connected subscribers (fire-and-forget).
async def publish_alert(alert: dict) -> None:
    """Publish an alert to subscribers currently connected (fire-and-forget)."""
    r = get_redis()
    await r.publish(ALERTS_CHANNEL, json.dumps(alert))


# Return recent alerts from the buffer list, newest first.
async def get_active_alerts(max_alerts: int = 50) -> list[dict]:
    """Return recent alerts from the buffer list, newest first."""
    r = get_redis()
    raw_alerts = await r.lrange("gridsense:alerts:recent", 0, max_alerts - 1)
    return [json.loads(a) for a in raw_alerts]


# Prepend an alert to the buffer list and trim it to a bounded size.
async def push_alert_to_buffer(alert: dict, max_size: int = 200) -> None:
    """Prepend an alert to the buffer list and trim it to a bounded size."""
    r = get_redis()
    pipe = r.pipeline()
    await pipe.lpush("gridsense:alerts:recent", json.dumps(alert))
    await pipe.ltrim("gridsense:alerts:recent", 0, max_size - 1)
    await pipe.execute()
