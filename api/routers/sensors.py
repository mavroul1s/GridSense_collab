# Cassandra-backed sensor ingestion and query endpoints.

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from db.cassandra import execute_async
from db.redis import cache_get, cache_set, cache_delete
from models.cassandra import SensorReadingIn, SensorReadingOut, SensorSummaryOut

router = APIRouter(prefix="/sensors", tags=["Sensors"])


@router.post("/readings", status_code=201)
async def ingest_reading(reading: SensorReadingIn):
    """Write a reading to both Cassandra tables and invalidate the summary cache."""
    # Default to server UTC so devices with bad clocks don't corrupt time ordering.
    reading_time = reading.reading_time or datetime.now(timezone.utc)

    # Minute-level bucket → one partition holds all sensors' readings for that minute.
    time_bucket = reading_time.strftime('%Y-%m-%dT%H:%M')

    # Per-sensor table.
    await execute_async(
        """
        INSERT INTO sensor_readings
            (sensor_id, reading_time, metric_type, value, unit, quality_flag)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            reading.sensor_id,
            reading_time,
            reading.metric_type,
            reading.value,
            reading.unit,
            reading.quality_flag,
        )
    )

    # Dashboard (by-time) table — 2x write amplification, by design.
    await execute_async(
        """
        INSERT INTO sensor_readings_by_time
            (time_bucket, reading_time, sensor_id, metric_type, value)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (
            time_bucket,
            reading_time,
            reading.sensor_id,
            reading.metric_type,
            reading.value,
        )
    )

    await cache_delete(f"summary:{reading.sensor_id}")

    return {
        "status":       "created",
        "sensor_id":    reading.sensor_id,
        "reading_time": reading_time.isoformat(),
        "metric_type":  reading.metric_type,
    }


@router.get("/{sensor_id}/readings", response_model=list[SensorReadingOut])
async def get_readings(
    sensor_id:   str,
    limit:       int            = Query(default=10, ge=1, le=1000),
    metric_type: Optional[str]  = Query(default=None,
                                        description="Filter by metric type"),
    since:       Optional[datetime] = Query(default=None,
                                            description="Return readings after this UTC timestamp"),
):
    """Most recent N readings for a sensor, optionally filtered by time and/or metric."""
    # ALLOW FILTERING below is scoped to a single partition (sensor_id), not the cluster.
    if since is not None and metric_type is not None:
        rows = await execute_async(
            """
            SELECT sensor_id, reading_time, metric_type, value, unit, quality_flag
            FROM sensor_readings
            WHERE sensor_id   = %s
              AND reading_time >= %s
              AND metric_type  = %s
            LIMIT %s
            ALLOW FILTERING
            """,
            (sensor_id, since, metric_type, limit)
        )

    elif since is not None:
        # Range on the first clustering key — no ALLOW FILTERING needed.
        rows = await execute_async(
            """
            SELECT sensor_id, reading_time, metric_type, value, unit, quality_flag
            FROM sensor_readings
            WHERE sensor_id   = %s
              AND reading_time >= %s
            LIMIT %s
            """,
            (sensor_id, since, limit)
        )

    elif metric_type is not None:
        rows = await execute_async(
            """
            SELECT sensor_id, reading_time, metric_type, value, unit, quality_flag
            FROM sensor_readings
            WHERE sensor_id  = %s
              AND metric_type = %s
            LIMIT %s
            ALLOW FILTERING
            """,
            (sensor_id, metric_type, limit)
        )

    else:
        rows = await execute_async(
            """
            SELECT sensor_id, reading_time, metric_type, value, unit, quality_flag
            FROM sensor_readings
            WHERE sensor_id = %s
            LIMIT %s
            """,
            (sensor_id, limit)
        )

    result = list(rows)
    if not result:
        raise HTTPException(
            status_code=404,
            detail=f"No readings found for sensor '{sensor_id}'"
        )

    return [
        SensorReadingOut(
            sensor_id=row.sensor_id,
            reading_time=row.reading_time,
            metric_type=row.metric_type,
            value=row.value,
            unit=getattr(row, "unit", None),
            quality_flag=getattr(row, "quality_flag", None),
        )
        for row in result
    ]


@router.get("/{sensor_id}/summary", response_model=SensorSummaryOut)
async def get_sensor_summary(sensor_id: str):
    """Cache-aside summary: Redis hit returns immediately, miss recomputes from Cassandra (TTL 30s)."""
    cache_key = f"summary:{sensor_id}"

    cached = await cache_get(cache_key)
    if cached:
        cached["cached"] = True
        return SensorSummaryOut(**cached)

    rows = await execute_async(
        """
        SELECT sensor_id, reading_time, metric_type, value
        FROM sensor_readings
        WHERE sensor_id = %s
        LIMIT 100
        """,
        (sensor_id,)
    )

    result = list(rows)
    if not result:
        raise HTTPException(
            status_code=404,
            detail=f"No readings found for sensor '{sensor_id}'"
        )

    # Rows arrive reading_time DESC — first occurrence per metric_type is the latest.
    latest_values: dict[str, float] = {}
    times = []

    for row in result:
        if row.metric_type not in latest_values:
            latest_values[row.metric_type] = row.value
        times.append(row.reading_time)

    summary = SensorSummaryOut(
        sensor_id=sensor_id,
        latest_values=latest_values,
        reading_count=len(result),
        window_start=min(times) if times else None,
        window_end=max(times) if times else None,
        cached=False,
    )

    # mode="json" turns datetimes into ISO strings for Redis storage.
    await cache_set(cache_key, summary.model_dump(mode="json"), ttl=30)

    return summary
