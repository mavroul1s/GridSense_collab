-- cql/init.cql
-- GridSense Cassandra Schema
-- Week 6 slide rules: SimpleStrategy for dev, RF=1
-- Data types: TEXT, TIMESTAMP, TIMEUUID, FLOAT, INT, BIGINT, BOOLEAN, LIST, SET, MAP, COUNTER only
-- Query-driven design: one table per query pattern (Week 6, Slide 31)

CREATE KEYSPACE IF NOT EXISTS gridsense
WITH replication = {
  'class': 'SimpleStrategy',
  'replication_factor': 1
};

USE gridsense;

-- ─────────────────────────────────────────────────────────────────────────────
-- TABLE 1: sensor_readings
-- Serves: POST /sensors/readings  |  GET /sensors/{sensor_id}/readings
--
-- Partition key  : sensor_id  → all readings for one sensor on one node
-- Clustering key : reading_time DESC, metric_type ASC
--
-- WHY metric_type is in the clustering key (Trap 9 fix):
-- A sensor emitting voltage + current + power_factor at the SAME timestamp
-- would create three rows with identical (sensor_id, reading_time).
-- Cassandra Last-Write-Wins would silently keep only the last write.
-- Adding metric_type gives each a distinct cell — no data loss.
--
-- CLUSTERING ORDER BY reading_time DESC: most recent readings are physically
-- first on disk → LIMIT 10 is a sequential read from the front of the SSTable.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sensor_readings (
    sensor_id    TEXT,
    reading_time TIMESTAMP,
    metric_type  TEXT,
    value        FLOAT,
    unit         TEXT,
    quality_flag INT,
    PRIMARY KEY ((sensor_id), reading_time, metric_type)
) WITH CLUSTERING ORDER BY (reading_time DESC, metric_type ASC)
  AND default_time_to_live = 7776000;  -- 90 days in seconds


-- ─────────────────────────────────────────────────────────────────────────────
-- TABLE 2: sensor_readings_by_time
-- Serves: cross-network dashboard queries (GET /sensors/{sensor_id}/summary
--         triggers a Redis cache-aside; this table is the cache miss fallback)
--
-- Partition key  : time_bucket (TEXT, e.g. '2026-05-15T10:05')
-- Clustering key : reading_time DESC, sensor_id, metric_type
--
-- WHY a second table:
-- sensor_readings partitions by sensor_id — efficient per-sensor reads but
-- requires a full cluster scatter scan for "all sensors in last 60 seconds".
-- This table partitions by minute bucket — one partition holds all readings
-- in that minute across all sensors, making the dashboard query a single
-- partition read.  Write amplification = 2x (acceptable per Part A A.3.c).
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS sensor_readings_by_time (
    time_bucket  TEXT,
    reading_time TIMESTAMP,
    sensor_id    TEXT,
    metric_type  TEXT,
    value        FLOAT,
    PRIMARY KEY ((time_bucket), reading_time, sensor_id, metric_type)
) WITH CLUSTERING ORDER BY (reading_time DESC, sensor_id ASC, metric_type ASC)
  AND default_time_to_live = 7776000;


-- ─────────────────────────────────────────────────────────────────────────────
-- TABLE 3: relay_events
-- Serves: forensic fault sequence replay (POST /alerts/publish writes here
--         in addition to Redis; GET /alerts/active reads from Redis first)
--
-- Partition key  : feeder_id  → all events on one feeder co-located
-- Clustering key : event_time ASC (TIMEUUID)
--
-- WHY TIMEUUID (not TIMESTAMP):
-- Week 6 Slide 34: TIMEUUID embeds a 100-nanosecond timestamp and a
-- unique node identifier — two events at the same millisecond get distinct
-- cells with causal ordering preserved.  Timestamp collisions at high event
-- rates would silently overwrite each other.
--
-- CLUSTERING ORDER BY ASC: chronological replay is a forward sequential scan.
-- ─────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS relay_events (
    feeder_id   TEXT,
    event_time  TIMEUUID,
    relay_id    TEXT,
    event_type  TEXT,
    fault_type  TEXT,
    current_kA  FLOAT,
    resolved    BOOLEAN,
    PRIMARY KEY ((feeder_id), event_time)
) WITH CLUSTERING ORDER BY (event_time ASC);