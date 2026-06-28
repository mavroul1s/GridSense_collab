# Pydantic models for Neo4j-backed endpoints.
# Literal-constrained labels/types make the router's f-string Cypher safe from injection.

from typing import Optional, Literal
from pydantic import BaseModel, Field


NodeLabel = Literal[
    "GridSupplyPoint",
    "Substation",
    "Transformer",
    "SmartMeter"
]

RelationshipType = Literal[
    "FEEDS",
    "SUPPLIES",
    "CONNECTS_TO",
    "ALTERNATIVE_FEED"
]


class FaultImpactParams(BaseModel):
    # Validated here (1-10) because the router interpolates it as a Cypher path bound.
    max_depth: int = Field(
        default=6,
        ge=1,
        le=10,
        description="Maximum traversal depth (1–10) — bounds the graph scan."
    )


class AffectedNodeOut(BaseModel):
    node_type:   str            = Field(..., description="Node label: Substation, Transformer, SmartMeter")
    node_id:     str            = Field(..., description="Unique node identifier property")
    name:        Optional[str]  = Field(default=None, description="Human-readable name if available")
    depth:       int            = Field(..., description="Hops from fault origin")
    voltage_kV:  Optional[float] = Field(default=None, description="Nominal voltage (Substation/GSP only)")
    rating_kVA:  Optional[int]   = Field(default=None, description="Transformer rating in kVA")
    premise_id:  Optional[str]   = Field(default=None, description="Premise ID (SmartMeter only)")
    tariff_class: Optional[str]  = Field(default=None, description="Tariff class (SmartMeter only)")

    model_config = {"from_attributes": True}


class FaultImpactOut(BaseModel):
    origin_node_id:   str
    origin_node_type: str
    max_depth_used:   int
    affected_count:   int
    affected_nodes:   list[AffectedNodeOut]


class RestorePathNodeOut(BaseModel):
    node_id:    str
    node_type:  str
    name:       Optional[str] = None

    model_config = {"from_attributes": True}


class RestorePathOut(BaseModel):
    origin_node_id: str
    paths:          list[list[RestorePathNodeOut]]
    path_count:     int


class NodeIn(BaseModel):
    label:      NodeLabel = Field(..., description="Node label — must be one of the four valid types")
    properties: dict      = Field(..., description="Node properties dict — must include node_id key")

    @property
    def node_id(self) -> str:
        """node_id from properties — raises if missing (becomes a 422)."""
        nid = self.properties.get("node_id")
        if not nid:
            raise ValueError("properties dict must contain a 'node_id' key")
        return nid

    model_config = {"json_schema_extra": {
        "example": {
            "label": "Substation",
            "properties": {
                "node_id":     "SS_011",
                "name":        "New Northern Primary",
                "voltage_kV":  11,
                "lat":         51.54,
                "lon":        -1.22,
                "commissioned_year": 2025
            }
        }
    }}


class NodeOut(BaseModel):
    label:      str
    node_id:    str
    properties: dict

    model_config = {"from_attributes": True}


class RelationshipIn(BaseModel):
    from_id:    str              = Field(..., description="node_id of the source node")
    to_id:      str              = Field(..., description="node_id of the target node")
    rel_type:   RelationshipType = Field(..., description="Relationship type")
    properties: dict             = Field(default_factory=dict,
                                         description="Optional relationship properties")

    model_config = {"json_schema_extra": {
        "example": {
            "from_id":  "GSP_NORTH",
            "to_id":    "SS_011",
            "rel_type": "FEEDS",
            "properties": {
                "feeder_id":  "F_011",
                "voltage_kV": 11,
                "length_km":  3.7
            }
        }
    }}


class RelationshipOut(BaseModel):
    from_id:    str
    to_id:      str
    rel_type:   str
    properties: dict

    model_config = {"from_attributes": True}
