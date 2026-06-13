# api/routers/sensors.py
# Cassandra-backed sensor ingestion and query endpoints
#
# Endpoints:
#   POST /sensors/readings          → ingest reading into 2 Cassandra tables
#   GET  /sensors/{sensor_id}/readings → retrieve recent readings
#   GET  /sensors/{sensor_id}/summary  → Redis cache-aside → Cassandra on miss

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from db.cassandra import execute_async
from db.redis import cache_get, cache_set, cache_delete
from models.cassandra import SensorReadingIn, SensorReadingOut, SensorSummaryOut

router = APIRouter(prefix="/sensors", tags=["Sensors"])


# ── POST /sensors/readings ────────────────────────────────────────
@router.post("/readings", status_code=201)
async def ingest_reading(reading: SensorReadingIn):
    """
    Ingest a sensor reading into Cassandra.

    Writes to TWO tables — write amplification = 2x.
    Justified in Part A A.3.c: doubling writes gives O(1) dashboard
    reads without cluster-wide scatter scans.

    Table 1 — sensor_readings:
        Partition key: sensor_id
        → efficient per-sensor queries (GET /sensors/{id}/readings)

    Table 2 — sensor_readings_by_time:
        Partition key: time_bucket (minute-level)
        → efficient cross-network dashboard queries

    Also invalidates Redis summary cache for this sensor so the
    next GET /sensors/{id}/summary reflects the new reading.
    """
    # Default reading_time to server UTC if client omits it.
    # Prevents clock-skew from devices with bad clocks corrupting
    # the time-ordered SSTable layout in Cassandra.
    reading_time = reading.reading_time or datetime.now(timezone.utc)

    # Minute-level bucket: '2026-05-12T10:05'
    # Partitions by minute so one time-bucket partition = all readings
    # in that minute across all sensors — one partition read per dashboard query.
    time_bucket = reading_time.strftime('%Y-%m-%dT%H:%M')

    # ── WRITE 1: sensor_readings ──────────────────────────────────
    # cassandra-driver uses %s placeholders for non-prepared statements
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

    # ── WRITE 2: sensor_readings_by_time ─────────────────────────
    # Part A A.3.c: write amplification = 2x, acceptable because
    # Cassandra's append-only CommitLog makes both writes sequential.
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

    # ── CACHE INVALIDATION ────────────────────────────────────────
    # The cached summary for this sensor is now stale — delete it so
    # the next summary request recomputes from fresh Cassandra data.
    await cache_delete(f"summary:{reading.sensor_id}")

    return {
        "status":       "created",
        "sensor_id":    reading.sensor_id,
        "reading_time": reading_time.isoformat(),
        "metric_type":  reading.metric_type,
    }


# ── GET /sensors/{sensor_id}/readings ────────────────────────────
@router.get("/{sensor_id}/readings", response_model=list[SensorReadingOut])
async def get_readings(
    sensor_id:   str,
    limit:       int            = Query(default=10, ge=1, le=1000),
    metric_type: Optional[str]  = Query(default=None,
                                        description="Filter by metric type"),
    since:       Optional[datetime] = Query(default=None,
                                            description="Return readings after this UTC timestamp"),
):
    """
    Retrieve the most recent N readings for a sensor.

    Partition key (sensor_id) is always present — no cluster-wide scan.
    CLUSTERING ORDER BY reading_time DESC means LIMIT N is a sequential
    read from the front of the SSTable — no sort required.

    Filter logic:
      - No filters        → latest N readings, all metric types
      - since only        → readings after timestamp, all metric types
      - metric_type only  → filtered by type (ALLOW FILTERING — single
                            partition scan only, not a cluster scatter)
      - both              → filtered by time AND type (ALLOW FILTERING)
    """
    if since is not None and metric_type is not None:
        # Range on reading_time + equality on metric_type.
        # Needs ALLOW FILTERING: after a range on the first clustering
        # key, Cassandra cannot use the index for the second key.
        # Acceptable: scoped to ONE partition (sensor_id), not the full cluster.
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
        # Range query on clustering key — valid without ALLOW FILTERING.
        # Partition key provided + range on first clustering key.
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
        # Equality on second clustering key without constraining the first.
        # Requires ALLOW FILTERING — but only scans this sensor's partition,
        # not the full cluster. A single-partition ALLOW FILTERING scan is
        # acceptable; a cross-partition one is not.
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
        # Simplest case: latest N readings for this sensor, all metric types.
        # Pure partition key lookup — fastest possible Cassandra read.
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


# ── GET /sensors/{sensor_id}/summary ─────────────────────────────
@router.get("/{sensor_id}/summary", response_model=SensorSummaryOut)
async def get_sensor_summary(sensor_id: str):
    """
    Return a summary of recent readings for a sensor.

    Cache-aside pattern (Week 4 Slide 24):
      1. GET from Redis  → HIT: return immediately (sub-ms from RAM)
      2. MISS            → query Cassandra last 100 readings
      3. Aggregate       → latest value per metric_type
      4. SETEX in Redis  → TTL=30s (Week 4 Slide 25)
      5. Return result

    30-second TTL matches Part A A.2.c: the assignment states sensor
    dashboard aggregates may lag up to 30 seconds — CL ONE + 30s cache
    satisfies this with substantial margin.
    """
    cache_key = f"summary:{sensor_id}"

    # ── STEP 1: Redis cache check ─────────────────────────────────
    cached = await cache_get(cache_key)
    if cached:
        # Cache HIT — stamp the flag and return immediately
        cached["cached"] = True
        return SensorSummaryOut(**cached)

    # ── STEP 2: Cache MISS — query Cassandra ─────────────────────
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

    # ── STEP 3: Aggregate ─────────────────────────────────────────
    # Rows arrive DESC ordered by reading_time.
    # First occurrence of each metric_type is the most recent value.
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

    # ── STEP 4: Store in Redis, TTL = 30 seconds ─────────────────
    # model_dump(mode="json") converts datetime → ISO string so the
    # dict is JSON-serialisable for Redis storage.
    await cache_set(cache_key, summary.model_dump(mode="json"), ttl=30)

    return summary