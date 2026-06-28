# Redis-backed alert publishing and retrieval endpoints.

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


@router.post("/publish", status_code=201)
async def publish(event: RelayEventIn):
    """Persist a relay event to Cassandra, publish it to Redis, and buffer it for /alerts/active."""
    # uuid1() embeds the timestamp → maps to Cassandra TIMEUUID, preserving causal order.
    event_time = uuid.uuid1()
    now_utc = datetime.now(timezone.utc)

    # 1. Durable system-of-record write.
    await execute_async(
        """
        INSERT INTO relay_events
            (feeder_id, event_time, relay_id, event_type,
             fault_type, current_kA, resolved)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            event.feeder_id,
            event_time,
            event.relay_id,
            event.event_type,
            event.fault_type,
            event.current_kA,
            event.resolved,
        )
    )

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

    # 2 + 3. Live delivery to subscribers, plus a buffered copy for late readers.
    await publish_alert(alert_payload)
    await push_alert_to_buffer(alert_payload)

    # Cache an overload status (5s TTL) when a trip/lockout carries high fault current.
    if event.event_type in ("TRIP", "LOCKOUT") and event.relay_id:
        if event.current_kA and event.current_kA > 2.0:
            await set_transformer_status(
                asset_id=event.relay_id,   # relay_id used as a proxy key
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


@router.get("/active")
async def get_alerts(limit: int = 50):
    """Return the most recent alerts from the Redis buffer (empty list, not 404, when none)."""
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


@router.get("/transformer/{asset_id}/status")
async def get_transformer_status_endpoint(asset_id: str):
    """Return a transformer's cached overload status (NORMAL once the 5s TTL expires)."""
    status = await get_transformer_status(asset_id)

    return {
        "asset_id": asset_id,
        "status":   status or "NORMAL",
        "cached":   status is not None,
        "ttl_seconds": 5,
    }
