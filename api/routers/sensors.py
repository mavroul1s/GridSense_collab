# Cassandra-backed sensor ingestion and query endpoints.

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from db.cassandra import execute_async
from db.redis import cache_get, cache_set, cache_delete
from models.cassandra import SensorReadingIn, SensorReadingOut, SensorSummaryOut

router = APIRouter(prefix="/sensors", tags=["Sensors"])


# POST /sensors/readings — ingest one sensor reading.
@router.post("/readings", status_code=201)
async def ingest_reading(reading: SensorReadingIn):
    """Write a reading to both Cassandra tables and invalidate the summary cache."""
    # Fall back to server UTC when the client omits a timestamp.
    reading_time = reading.reading_time or datetime.now(timezone.utc)

    # Derive the minute-level bucket used by the dashboard table.
    time_bucket = reading_time.strftime('%Y-%m-%dT%H:%M')

    # Write to the per-sensor table.
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

    # Write the same reading to the by-time table (2x write amplification).
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

    # Drop the now-stale cached summary for this sensor.
    await cache_delete(f"summary:{reading.sensor_id}")

    return {
        "status":       "created",
        "sensor_id":    reading.sensor_id,
        "reading_time": reading_time.isoformat(),
        "metric_type":  reading.metric_type,
    }


# GET /sensors/{id}/readings — list a sensor's recent readings.
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
    # Time range + metric filter — ALLOW FILTERING stays within one partition.
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

    # Time range only — valid on the first clustering key without ALLOW FILTERING.
    elif since is not None:
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

    # Metric filter only — single-partition ALLOW FILTERING.
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

    # No filters — plain partition-key lookup, the fastest read.
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

    # 404 when the sensor has no readings at all.
    result = list(rows)
    if not result:
        raise HTTPException(
            status_code=404,
            detail=f"No readings found for sensor '{sensor_id}'"
        )

    # Map driver rows to the response model.
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


# GET /sensors/{id}/summary — cached per-sensor summary.
@router.get("/{sensor_id}/summary", response_model=SensorSummaryOut)
async def get_sensor_summary(sensor_id: str):
    """Cache-aside summary: serve from Redis on hit, recompute from Cassandra on miss (TTL 30s)."""
    cache_key = f"summary:{sensor_id}"

    # Return immediately on a cache hit.
    cached = await cache_get(cache_key)
    if cached:
        cached["cached"] = True
        return SensorSummaryOut(**cached)

    # Cache miss — pull the latest readings from Cassandra.
    rows = await execute_async(
        """
        SELECT sensor_id, reading_time, metric_type, value
        FROM sensor_readings
        WHERE sensor_id = %s
        LIMIT 100
        """,
        (sensor_id,)
    )

    # 404 when the sensor has no readings.
    result = list(rows)
    if not result:
        raise HTTPException(
            status_code=404,
            detail=f"No readings found for sensor '{sensor_id}'"
        )

    # Aggregate: latest value per metric and the time window (rows are DESC).
    latest_values: dict[str, float] = {}
    times = []

    for row in result:
        if row.metric_type not in latest_values:
            latest_values[row.metric_type] = row.value
        times.append(row.reading_time)

    # Build the summary response.
    summary = SensorSummaryOut(
        sensor_id=sensor_id,
        latest_values=latest_values,
        reading_count=len(result),
        window_start=min(times) if times else None,
        window_end=max(times) if times else None,
        cached=False,
    )

    # Cache it as JSON for 30 seconds, then return.
    await cache_set(cache_key, summary.model_dump(mode="json"), ttl=30)

    return summary
