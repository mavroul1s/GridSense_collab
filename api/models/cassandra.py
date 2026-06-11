# api/models/cassandra.py
# Pydantic models for Cassandra-backed endpoints
#
# Week 5 Slide 82: BaseModel validates incoming JSON automatically.
# Wrong type or missing field → FastAPI returns 422 before DB is touched.
# model_dump() serialises a validated model to a dict for DB writes.
#
# All types match cql/init.cql exactly:
#   TEXT       → str
#   FLOAT      → float
#   INT        → int       (quality_flag — NOT tinyint, Trap 7 fix)
#   TIMESTAMP  → datetime
#   TIMEUUID   → UUID
#   BOOLEAN    → bool

from datetime import datetime
from uuid import UUID
from typing import Optional
from pydantic import BaseModel, Field, model_validator


# ── SENSOR READING — INPUT ────────────────────────────────────────
# Used by: POST /sensors/readings
# Written to: sensor_readings AND sensor_readings_by_time tables
#
# reading_time is Optional — if not provided by the client, the router
# inserts the current UTC time. This prevents clock-skew issues where
# a client with a bad clock submits readings with wrong timestamps.

class SensorReadingIn(BaseModel):
    sensor_id:    str           = Field(..., min_length=1, max_length=100,
                                        description="Unique sensor identifier")
    reading_time: Optional[datetime] = Field(
                                        default=None,
                                        description="UTC timestamp — defaults to server time if omitted")
    metric_type:  str           = Field(..., min_length=1, max_length=50,
                                        description="Metric type: voltage, current, power_factor, temperature")
    value:        float         = Field(..., description="Measured value")
    unit:         str           = Field(..., min_length=1, max_length=20,
                                        description="Unit of measurement: V, A, kW, degC")
    quality_flag: int           = Field(default=0, ge=0, le=2,
                                        description="0=good, 1=suspect, 2=bad")

    model_config = {"json_schema_extra": {
        "example": {
            "sensor_id":    "SENSOR_042",
            "metric_type":  "voltage",
            "value":        231.4,
            "unit":         "V",
            "quality_flag": 0
        }
    }}


# ── SENSOR READING — RESPONSE ─────────────────────────────────────
# Used by: GET /sensors/{sensor_id}/readings
# Read from: sensor_readings table
#
# reading_time comes back from Cassandra as a Python datetime —
# Pydantic serialises it to ISO 8601 string in the JSON response.

class SensorReadingOut(BaseModel):
    sensor_id:    str
    reading_time: datetime
    metric_type:  str
    value:        float
    unit:         Optional[str]  = None
    quality_flag: Optional[int]  = None

    # Allow datetime objects from Cassandra ResultSet rows
    model_config = {"from_attributes": True}


# ── SENSOR SUMMARY — RESPONSE ─────────────────────────────────────
# Used by: GET /sensors/{sensor_id}/summary
# Source: Redis cache (cache-aside) → Cassandra on cache miss
#
# Summary aggregates the last N readings for a sensor:
# latest value per metric_type, reading count, time window

class SensorSummaryOut(BaseModel):
    sensor_id:     str
    latest_values: dict[str, float] = Field(
                       default_factory=dict,
                       description="Latest value per metric_type: {'voltage': 231.4, 'current': 12.1}")
    reading_count: int              = Field(default=0,
                                            description="Number of readings in the summary window")
    window_start:  Optional[datetime] = Field(default=None,
                                              description="Earliest reading_time in the window")
    window_end:    Optional[datetime] = Field(default=None,
                                              description="Most recent reading_time in the window")
    cached:        bool             = Field(default=False,
                                            description="True if result was served from Redis cache")

    model_config = {"from_attributes": True}


# ── RELAY EVENT — INPUT ───────────────────────────────────────────
# Used by: POST /alerts/publish (also writes to relay_events table)
#
# event_time is Optional — if omitted, router generates a TIMEUUID
# using uuid1() which embeds the current timestamp at nanosecond
# resolution. This is the correct Cassandra TIMEUUID pattern.

class RelayEventIn(BaseModel):
    feeder_id:  str           = Field(..., min_length=1, max_length=50,
                                      description="Feeder identifier (e.g. F_001)")
    relay_id:   str           = Field(..., min_length=1, max_length=50,
                                      description="Relay device identifier")
    event_type: str           = Field(..., min_length=1, max_length=50,
                                      description="Event type: TRIP, RECLOSE, LOCKOUT, ALARM")
    fault_type: Optional[str] = Field(default=None, max_length=50,
                                      description="Fault classification: OVERCURRENT, EARTH_FAULT, etc.")
    current_kA: Optional[float] = Field(default=None,
                                        description="Fault current in kiloamperes")
    resolved:   bool          = Field(default=False,
                                      description="True if fault has been cleared")

    model_config = {"json_schema_extra": {
        "example": {
            "feeder_id":  "F_001",
            "relay_id":   "RELAY_SS001_F001",
            "event_type": "TRIP",
            "fault_type": "OVERCURRENT",
            "current_kA": 3.2,
            "resolved":   False
        }
    }}


# ── RELAY EVENT — RESPONSE ────────────────────────────────────────
# Used by: GET /alerts/active
# event_time is UUID (TIMEUUID from Cassandra) — serialised to string

class RelayEventOut(BaseModel):
    feeder_id:  str
    event_time: UUID
    relay_id:   str
    event_type: str
    fault_type: Optional[str]  = None
    current_kA: Optional[float] = None
    resolved:   bool

    model_config = {"from_attributes": True}


# ── QUERY PARAMETERS ──────────────────────────────────────────────
# Reusable parameter model for GET /sensors/{sensor_id}/readings
# Encapsulates limit + optional time window filter

class SensorReadingQuery(BaseModel):
    limit:      int                  = Field(default=10,  ge=1, le=1000,
                                             description="Maximum readings to return")
    metric_type: Optional[str]       = Field(default=None,
                                             description="Filter by metric type")
    since:       Optional[datetime]  = Field(default=None,
                                             description="Return readings after this UTC timestamp")