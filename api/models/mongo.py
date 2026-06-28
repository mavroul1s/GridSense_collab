# Pydantic models for MongoDB-backed equipment catalog endpoints.
# Three schema shapes share one collection; extra_fields holds arbitrary
# manufacturer telemetry without a migration.

from typing import Optional, Literal, Any
from datetime import date, datetime
from pydantic import BaseModel, Field


EquipmentType = Literal[
    "Transformer",
    "SmartMeter",
    "ProtectionRelay"
]


class EquipmentBase(BaseModel):
    # asset_id is the cross-store key (matches Neo4j node_id).
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
    schema_version:  int          = Field(default=1, description="Document schema version")
    extra_fields:    dict[str, Any] = Field(
                         default_factory=dict,
                         description="Arbitrary manufacturer-specific fields — no migration required"
                     )


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


from typing import Union
from pydantic import Discriminator

EquipmentIn = Union[TransformerIn, SmartMeterIn, ProtectionRelayIn]


# Used for OpenAPI docs only — GET returns the raw flexible document.
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
        "populate_by_name": True,
    }


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
