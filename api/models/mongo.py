# api/models/mongo.py
# Pydantic models for MongoDB-backed equipment catalog endpoints
#
# Week 5 Slide 24: schema flexibility — different equipment types
# have different field sets. MongoDB stores them in one collection;
# Pydantic models validate each type at the API boundary.
#
# Week 5 Slides 27-29: schema versioning — schema_version field on
# every document allows old records to coexist with new ones without
# migration. Application code branches on schema_version when needed.
#
# Three equipment schema shapes (assignment requirement: 3+ shapes):
#   1. Transformer    — electrical ratings, oil monitoring, tap changer
#   2. SmartMeter     — firmware, comms protocol, metrology class
#   3. ProtectionRelay — protection curves, pickup settings, auto-reclose
#
# extra_fields: dict on every model captures arbitrary manufacturer-
# specific telemetry (Part A A.5 Case 4 — the 40 non-standard fields).

from typing import Optional, Literal, Any
from datetime import date, datetime
from pydantic import BaseModel, Field


# ── EQUIPMENT TYPE DISCRIMINATOR ──────────────────────────────────
# Constrains the equipment_type field to known valid values.
# Prevents arbitrary strings entering the catalog collection.

EquipmentType = Literal[
    "Transformer",
    "SmartMeter",
    "ProtectionRelay"
]


# ── BASE EQUIPMENT MODEL ──────────────────────────────────────────
# Fields shared by ALL equipment types.
# Subclasses add type-specific fields on top.
#
# asset_id links MongoDB documents to Neo4j nodes —
# the same asset_id appears in both stores. This is the referencing
# pattern (Week 5 Slide 68): MongoDB owns catalog detail,
# Neo4j owns topology relationships.

class EquipmentBase(BaseModel):
    asset_id:        str          = Field(..., min_length=1, max_length=100,
                                          description="Unique asset identifier — matches Neo4j node_id")
    equipment_type:  EquipmentType = Field(..., description="Equipment category")
    manufacturer:    str          = Field(..., min_length=1, max_length=100)
    model:           str          = Field(..., min_length=1, max_length=100)
    serial_number:   str          = Field(..., min_length=1, max_length=100)
    installed:       date         = Field(..., description="Installation date")
    last_inspection: Optional[date] = Field(default=None, description="Most recent inspection date")
    status:          str          = Field(default="active",
                                          description="Operational status: active, maintenance, decommissioned")
    schema_version:  int          = Field(default=1,
                                          description="Schema version — Week 5 Slide 27 versioning pattern")
    # Captures arbitrary manufacturer-specific telemetry fields.
    # Part A A.5 Case 4: a new meter with 40 non-standard fields
    # stores them here without schema migration.
    # Week 5 Slide 24: duplicate data is acceptable when it saves
    # expensive restructuring operations.
    extra_fields:    dict[str, Any] = Field(
                         default_factory=dict,
                         description="Arbitrary manufacturer-specific fields — no migration required"
                     )


# ── SCHEMA SHAPE 1: TRANSFORMER ───────────────────────────────────
# Transformer-specific catalog fields.
# Links to Neo4j Transformer nodes via asset_id.
# Links to Cassandra sensor_readings via sensor_id prefix.

class TransformerIn(EquipmentBase):
    equipment_type:      Literal["Transformer"] = "Transformer"
    rating_kVA:          int     = Field(..., gt=0, description="Rated capacity in kVA")
    primary_voltage_kV:  float   = Field(..., gt=0, description="Primary winding voltage")
    secondary_voltage_kV: float  = Field(..., gt=0, description="Secondary winding voltage")
    vector_group:        str     = Field(default="Dyn11",
                                         description="Winding vector group (e.g. Dyn11, YNyn0)")
    cooling_type:        str     = Field(default="ONAN",
                                         description="Cooling method: ONAN, ONAF, OFAF")
    tap_changer:         bool    = Field(default=False,
                                         description="True if on-load tap changer fitted")
    tap_position:        Optional[int] = Field(default=None,
                                               description="Current tap position if tap_changer=True")
    oil_temp_sensor:     bool    = Field(default=False,
                                         description="True if oil temperature sensor fitted")
    max_oil_temp_C:      Optional[float] = Field(default=None,
                                                  description="Maximum rated oil temperature in Celsius")

    model_config = {"json_schema_extra": {
        "example": {
            "asset_id":            "TX_1",
            "equipment_type":      "Transformer",
            "manufacturer":        "ABB",
            "model":               "TrafoBloc-250",
            "serial_number":       "ABB-2018-00123",
            "installed":           "2018-03-15",
            "last_inspection":     "2024-01-10",
            "rating_kVA":          250,
            "primary_voltage_kV":  11.0,
            "secondary_voltage_kV": 0.4,
            "vector_group":        "Dyn11",
            "cooling_type":        "ONAN",
            "tap_changer":         True,
            "tap_position":        0,
            "oil_temp_sensor":     True,
            "max_oil_temp_C":      85.0,
            "extra_fields":        {}
        }
    }}


# ── SCHEMA SHAPE 2: SMART METER ───────────────────────────────────
# SmartMeter-specific catalog fields.
# Links to Neo4j SmartMeter nodes and PostgreSQL consumer_accounts
# via premise_id.

class SmartMeterIn(EquipmentBase):
    equipment_type:        Literal["SmartMeter"] = "SmartMeter"
    premise_id:            str    = Field(..., description="Links to consumer_accounts.premise_id")
    meter_id:              str    = Field(..., description="Links to Neo4j SmartMeter.meter_id")
    firmware_version:      str    = Field(..., description="Current firmware version string")
    communication_protocol: str   = Field(...,
                                          description="Comms protocol: DLMS, MBUS, ZIGBEE, LORAWAN")
    metrology_class:       str    = Field(default="A",
                                          description="Accuracy class: A, B, C per IEC 62053")
    phase:                 str    = Field(default="single",
                                          description="single or three-phase")
    tamper_detection:      bool   = Field(default=True,
                                          description="True if tamper detection hardware fitted")
    remote_disconnect:     bool   = Field(default=False,
                                          description="True if remote load switch fitted")
    hes_id:                Optional[str] = Field(default=None,
                                                  description="Head End System registration ID")

    model_config = {"json_schema_extra": {
        "example": {
            "asset_id":             "SM_1",
            "equipment_type":       "SmartMeter",
            "manufacturer":         "Landis+Gyr",
            "model":                "E360",
            "serial_number":        "LG-2022-SM001",
            "installed":            "2022-06-10",
            "premise_id":           "PREM_1",
            "meter_id":             "SM_1",
            "firmware_version":     "3.2.1",
            "communication_protocol": "DLMS",
            "metrology_class":      "B",
            "phase":                "single",
            "tamper_detection":     True,
            "remote_disconnect":    True,
            "extra_fields": {
                "custom_telemetry_field_1": "value1",
                "custom_telemetry_field_2": 42
            }
        }
    }}


# ── SCHEMA SHAPE 3: PROTECTION RELAY ─────────────────────────────
# Protection relay catalog fields.
# Relays generate the events stored in Cassandra relay_events table.
# asset_id prefixed 'RELAY_' — no Neo4j node but links via feeder_id.

class ProtectionRelayIn(EquipmentBase):
    equipment_type:       Literal["ProtectionRelay"] = "ProtectionRelay"
    feeder_id:            str    = Field(..., description="Feeder this relay protects")
    protection_function:  str    = Field(...,
                                         description="Primary function: OVERCURRENT, DISTANCE, DIFFERENTIAL")
    pickup_current_A:     float  = Field(..., gt=0,
                                         description="Overcurrent pickup threshold in Amperes")
    time_dial_setting:    float  = Field(..., gt=0,
                                         description="Time multiplier setting for IDMT curves")
    curve_type:           str    = Field(default="IEC_NI",
                                         description="Protection curve: IEC_NI, IEC_VI, IEC_EI, IEEE_MI")
    auto_reclose_enabled: bool   = Field(default=False,
                                         description="True if auto-reclose sequence is active")
    reclose_attempts:     int    = Field(default=3, ge=0, le=5,
                                         description="Number of auto-reclose attempts before lockout")
    reclose_delay_s:      float  = Field(default=0.5,
                                         description="Dead time between reclose attempts in seconds")
    communications_port:  Optional[str] = Field(default=None,
                                                 description="IEC 61850 / DNP3 comms port address")

    model_config = {"json_schema_extra": {
        "example": {
            "asset_id":            "RELAY_SS001_F001",
            "equipment_type":      "ProtectionRelay",
            "manufacturer":        "SEL",
            "model":               "SEL-351S",
            "serial_number":       "SEL-2019-00456",
            "installed":           "2019-09-01",
            "feeder_id":           "F_001",
            "protection_function": "OVERCURRENT",
            "pickup_current_A":    400.0,
            "time_dial_setting":   0.3,
            "curve_type":          "IEC_VI",
            "auto_reclose_enabled": True,
            "reclose_attempts":    3,
            "reclose_delay_s":     0.5,
            "extra_fields":        {}
        }
    }}


# ── UNION INPUT TYPE ──────────────────────────────────────────────
# The POST /equipment router accepts any of the three shapes.
# FastAPI uses the equipment_type discriminator to validate the
# correct submodel automatically.

from typing import Union
from pydantic import Discriminator

EquipmentIn = Union[TransformerIn, SmartMeterIn, ProtectionRelayIn]


# ── EQUIPMENT RESPONSE ────────────────────────────────────────────
# GET /equipment/{asset_id} returns a flexible dict — MongoDB documents
# have varying shapes and we do not want to force a rigid output schema.
# The router calls serialise_doc() from db/mongo.py then returns directly.
# This class is used for OpenAPI documentation only.

class EquipmentOut(BaseModel):
    id:             Optional[str]  = Field(default=None, alias="_id")
    asset_id:       str
    equipment_type: str
    manufacturer:   str
    model:          str
    serial_number:  str
    installed:      date
    status:         str
    schema_version: int

    model_config = {
        "from_attributes":  True,
        "populate_by_name": True,   # allow both '_id' and 'id'
    }


# ── EQUIPMENT PARTIAL UPDATE ──────────────────────────────────────
# Used by: PATCH /equipment/{asset_id}
# All fields Optional — PATCH updates only what is provided.
# Week 5 Slide 59: PATCH updates partial fields, PUT replaces entire doc.

class EquipmentUpdate(BaseModel):
    manufacturer:    Optional[str]  = None
    model:           Optional[str]  = None
    serial_number:   Optional[str]  = None
    last_inspection: Optional[date] = None
    status:          Optional[str]  = None
    extra_fields:    Optional[dict[str, Any]] = None

    model_config = {"json_schema_extra": {
        "example": {
            "last_inspection": "2025-03-20",
            "status":          "maintenance",
            "extra_fields": {
                "work_order": "WO-2025-0042",
                "technician": "A. Papadopoulos"
            }
        }
    }}