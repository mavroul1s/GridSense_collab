-- GridSense Cassandra schema — one table per access pattern.

CREATE KEYSPACE IF NOT EXISTS gridsense
WITH replication = {
  'class': 'SimpleStrategy',
  'replication_factor': 1
};

USE gridsense;

-- Per-sensor reads. metric_type is in the clustering key so multiple metrics
-- at the same timestamp don't overwrite each other (Last-Write-Wins).
CREATE TABLE IF NOT EXISTS sensor_readings (
    sensor_id    TEXT,
    reading_time TIMESTAMP,
    metric_type  TEXT,
    value        FLOAT,
    unit         TEXT,
    quality_flag INT,
    PRIMARY KEY ((sensor_id), reading_time, metric_type)
) WITH CLUSTERING ORDER BY (reading_time DESC, metric_type ASC)
  AND default_time_to_live = 7776000;  -- 90 days


-- Cross-network dashboard reads. Partitioned by minute bucket so one partition
-- holds all sensors' readings for that minute (the per-sensor table can't serve this).
CREATE TABLE IF NOT EXISTS sensor_readings_by_time (
    time_bucket  TEXT,
    reading_time TIMESTAMP,
    sensor_id    TEXT,
    metric_type  TEXT,
    value        FLOAT,
    PRIMARY KEY ((time_bucket), reading_time, sensor_id, metric_type)
) WITH CLUSTERING ORDER BY (reading_time DESC, sensor_id ASC, metric_type ASC)
  AND default_time_to_live = 7776000;


-- Relay fault log. TIMEUUID clustering preserves causal order even on
-- same-millisecond events; ASC gives chronological replay.
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
