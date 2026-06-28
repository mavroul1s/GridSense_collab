# Redis connection module — cache-aside helpers and Pub/Sub for alerts.

import os
import json
import redis.asyncio as aioredis
from redis.asyncio import Redis
from dotenv import load_dotenv

load_dotenv()

_redis: Redis | None = None


async def connect() -> None:
    """Open the client (decode_responses=True returns str) and ping to verify."""
    global _redis

    redis_url = os.getenv("REDIS_URL", "redis://cache:6379")

    _redis = aioredis.from_url(redis_url, decode_responses=True)

    await _redis.ping()
    print(f"[Redis] Connected to {redis_url}")


async def disconnect() -> None:
    global _redis
    if _redis:
        await _redis.aclose()
        print("[Redis] Connection closed")
    _redis = None


def get_redis() -> Redis:
    if _redis is None:
        raise RuntimeError("Redis client not initialised — call connect() first")
    return _redis


# ── Cache-aside ───────────────────────────────────────────────────

async def cache_get(key: str) -> dict | None:
    """Return the cached dict, or None on miss."""
    r = get_redis()
    value = await r.get(key)
    if value is None:
        return None
    return json.loads(value)


async def cache_set(key: str, data: dict, ttl: int = 30) -> None:
    """Store a dict as JSON with a TTL (Redis auto-evicts on expiry)."""
    r = get_redis()
    await r.setex(key, ttl, json.dumps(data))


async def cache_delete(key: str) -> None:
    """Evict a key early — called when a new reading makes the summary stale."""
    r = get_redis()
    await r.delete(key)


# ── Transformer status (5s TTL) ───────────────────────────────────

async def set_transformer_status(asset_id: str, status: str) -> None:
    r = get_redis()
    key = f"transformer:{asset_id}:status"
    await r.setex(key, 5, status)


async def get_transformer_status(asset_id: str) -> str | None:
    r = get_redis()
    key = f"transformer:{asset_id}:status"
    return await r.get(key)


# ── Pub/Sub + rolling buffer ──────────────────────────────────────
# Pure Pub/Sub keeps no history; a Redis list gives GET /alerts/active a recent buffer.

ALERTS_CHANNEL = "gridsense:alerts"


async def publish_alert(alert: dict) -> None:
    """Fire-and-forget publish to subscribers currently connected."""
    r = get_redis()
    await r.publish(ALERTS_CHANNEL, json.dumps(alert))


async def get_active_alerts(max_alerts: int = 50) -> list[dict]:
    """Return recent alerts from the buffer list, newest first."""
    r = get_redis()
    raw_alerts = await r.lrange("gridsense:alerts:recent", 0, max_alerts - 1)
    return [json.loads(a) for a in raw_alerts]


async def push_alert_to_buffer(alert: dict, max_size: int = 200) -> None:
    """LPUSH + LTRIM — bounded recent-items list."""
    r = get_redis()
    pipe = r.pipeline()
    await pipe.lpush("gridsense:alerts:recent", json.dumps(alert))
    await pipe.ltrim("gridsense:alerts:recent", 0, max_size - 1)
    await pipe.execute()
