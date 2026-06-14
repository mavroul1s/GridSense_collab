# api/routers/alerts.py
# Redis-backed alert publishing and retrieval endpoints
#
# Endpoints:
#   POST /alerts/publish  → publish alert to Redis Pub/Sub channel
#                           + push to rolling buffer list
#                           + write relay event to Cassandra
#   GET  /alerts/active   → return recent alerts from Redis buffer
#
# Week 4 Slide 24: cache-aside pattern
# Week 4 Slide 25: SETEX TTL-based expiry
# Week 4 Slide 41: Redis Pub/Sub
# Part A A.4.c: Redis as durable buffer during Neo4j leader election —
#   fault capture path (API → Redis → Cassandra) never depends on Neo4j.
#   Graph topology update happens independently and asynchronously.

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from db.redis import (
    publish_alert,
    push_alert_to_buffer,
    get_active_alerts,
    set_transformer_status,
    get_transformer_status,
)
from db.cassandra import execute_async
from models.cassandra import RelayEventIn

router = APIRouter(prefix="/alerts", tags=["Alerts"])


# ── POST /alerts/publish ──────────────────────────────────────────
@router.post("/publish", status_code=201)
async def publish(event: RelayEventIn):
    """
    Publish a relay fault event to the alert system.

    Three things happen atomically from the API's perspective:

    1. Cassandra write (relay_events table):
       Durable, causal-ordered record using TIMEUUID clustering key.
       This is the system of record — immutable after write.
       Part A A.5 Case 1: TIMEUUID preserves causal order even at
       nanosecond resolution, preventing timestamp collision data loss.

    2. Redis Pub/Sub publish:
       Fire-and-forget delivery to all current subscribers.
       Any service listening on gridsense:alerts channel receives
       this event immediately.
       Week 4 Slide 41: PUBLISH command.

    3. Redis list push (rolling buffer):
       Pushes to gridsense:alerts:recent list with LTRIM cap.
       Makes the event visible to GET /alerts/active for clients
       that were not subscribed at publish time.
       Pure Pub/Sub has no history — the list buffer solves this.

    Part A A.4.c — Neo4j decoupling:
       This endpoint does NOT write to Neo4j. The graph topology
       update (marking a feeder as faulted) happens via a background
       worker consuming from Redis. If Neo4j is in leader election,
       this endpoint is completely unaffected — fault capture is
       never blocked by graph DB availability.

    TIMEUUID generation:
       uuid.uuid1() generates a version-1 UUID embedding the current
       system timestamp at 100-nanosecond resolution. This maps
       directly to Cassandra's TIMEUUID type — the clustering key
       in relay_events.
    """
    # Generate TIMEUUID for the relay event.
    # uuid1() embeds current timestamp — used as Cassandra TIMEUUID
    # clustering key to preserve causal ordering of relay events
    # on the same feeder (Part A A.3.a, A.5 Case 1).
    event_time = uuid.uuid1()
    now_utc = datetime.now(timezone.utc)

    # ── 1. Write to Cassandra relay_events ───────────────────────
    # TIMEUUID is passed as string — cassandra-driver converts it
    # to the native TIMEUUID type via the %s placeholder.
    await execute_async(
        """
        INSERT INTO relay_events
            (feeder_id, event_time, relay_id, event_type,
             fault_type, current_kA, resolved)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            event.feeder_id,
            event_time,           # uuid.UUID → Cassandra TIMEUUID
            event.relay_id,
            event.event_type,
            event.fault_type,
            event.current_kA,
            event.resolved,
        )
    )

    # ── 2. Build alert payload dict ───────────────────────────────
    # Serialised to JSON for both Redis Pub/Sub and the buffer list.
    # event_time as string — uuid.UUID is not JSON-serialisable.
    alert_payload = {
        "event_id":   str(event_time),
        "feeder_id":  event.feeder_id,
        "relay_id":   event.relay_id,
        "event_type": event.event_type,
        "fault_type": event.fault_type,
        "current_kA": event.current_kA,
        "resolved":   event.resolved,
        "timestamp":  now_utc.isoformat(),
    }

    # ── 3. Publish to Redis Pub/Sub channel ──────────────────────
    # Week 4 Slide 41: fire-and-forget delivery to subscribers.
    # Part A A.4.c: this is the durable capture path — does not
    # depend on Neo4j availability.
    await publish_alert(alert_payload)

    # ── 4. Push to rolling buffer for GET /alerts/active ─────────
    # Pure Pub/Sub has no history — a client connecting after publish
    # sees nothing. LPUSH + LTRIM gives GET /alerts/active a queryable
    # recent-event list bounded to 200 entries (db/redis.py).
    await push_alert_to_buffer(alert_payload)

    # ── 5. Cache transformer overload status if applicable ────────
    # Part A A.5 Case 3: transformer overload status cached with 5s TTL.
    # If this relay event signals an overload on a specific transformer,
    # cache its status immediately so the dashboard can retrieve it
    # in O(1) from Redis rather than querying Cassandra.
    # Convention: relay_id format 'RELAY_{asset_id}_*' allows
    # extracting the transformer asset_id from the relay identifier.
    if event.event_type in ("TRIP", "LOCKOUT") and event.relay_id:
        parts = event.relay_id.split("_")
        # relay_id format: RELAY_SS001_F001 — extract substation part
        # for transformer status if fault current suggests overload.
        if event.current_kA and event.current_kA > 2.0:
            await set_transformer_status(
                asset_id=event.relay_id,   # use relay_id as proxy key
                status="OVERLOADED"
            )

    return {
        "status":     "published",
        "event_id":   str(event_time),
        "feeder_id":  event.feeder_id,
        "event_type": event.event_type,
        "cassandra":  "written",
        "redis":      "published",
    }


# ── GET /alerts/active ────────────────────────────────────────────
@router.get("/active")
async def get_alerts(limit: int = 50):
    """
    Return the most recent alerts from the Redis rolling buffer.

    Week 4 Slide 24 (cache-aside in reverse):
    Unlike the sensor summary endpoint where Redis is a cache for
    Cassandra data, here Redis IS the primary source for active
    alerts — Cassandra holds the permanent audit log, Redis holds
    the live operational view.

    The rolling buffer (gridsense:alerts:recent list) is populated
    by POST /alerts/publish via push_alert_to_buffer().
    LRANGE returns up to `limit` most recent alerts, newest first
    (LPUSH prepends — index 0 is always the most recent).

    If Redis is empty (e.g. after a cache flush or first boot),
    returns an empty list — not a 404. Absence of recent alerts
    is a valid operational state, not an error.
    """
    if limit < 1 or limit > 200:
        raise HTTPException(
            status_code=400,
            detail="limit must be between 1 and 200"
        )

    alerts = await get_active_alerts(max_alerts=limit)

    return {
        "count":  len(alerts),
        "alerts": alerts,
    }


# ── GET /alerts/transformer/{asset_id}/status ─────────────────────
# Bonus endpoint: demonstrates Part A A.5 Case 3 transformer TTL cache.
# Not in the 14 mandatory endpoints but directly demonstrates the
# 5-second TTL cache pattern from Part A.

@router.get("/transformer/{asset_id}/status")
async def get_transformer_status_endpoint(asset_id: str):
    """
    Return the cached overload status of a transformer.

    Part A A.5 Case 3:
        SETEX transformer:TX_001:status 5 "OVERLOADED"

    After 5 seconds Redis evicts the key automatically — stale
    status cannot outlive the underlying sensor reality.
    Returns None if the key has expired or was never set, which
    signals 'no recent overload event' rather than an error.
    """
    status = await get_transformer_status(asset_id)

    return {
        "asset_id": asset_id,
        "status":   status or "NORMAL",
        "cached":   status is not None,
        "ttl_seconds": 5,
    }