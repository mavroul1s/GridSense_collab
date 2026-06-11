# api/models/graph.py
# Pydantic models for Neo4j-backed endpoints
#
# Week 5 Slide 82: BaseModel validates all inputs before any Cypher runs.
#
# Critical design note — Trap 4 fix:
# max_depth is validated HERE as a bounded integer (1–10).
# The router uses it as: f"MATCH (o)-[:FEEDS|...]*1..{max_depth}]->(n)"
# It is NEVER passed as a Cypher parameter ($depth) because Cypher
# does not allow parameters as variable-length path bounds.
# Validation here ensures the f-string interpolation is safe —
# only a clean integer in range 1-10 ever reaches the query string.

from typing import Optional, Literal
from pydantic import BaseModel, Field


# ── VALID LABEL AND RELATIONSHIP CONSTANTS ────────────────────────
# From Part A A.4.b property graph model.
# Constraining to Literal types means Pydantic rejects any node label
# or relationship type not in the approved set — no arbitrary Cypher
# injection through the label field.

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


# ── FAULT IMPACT — QUERY PARAMETERS ──────────────────────────────
# Used by: GET /grid/fault-impact/{node_id}
#
# max_depth: the traversal depth bound.
# TRAP 4 FIX: this value is validated here as int in range 1-10.
# The router interpolates it directly into the Cypher f-string:
#     f"MATCH (o)-[:FEEDS|SUPPLIES|CONNECTS_TO*1..{params.max_depth}]->(n)"
# It is NEVER passed as $depth — that syntax is invalid in Cypher.

class FaultImpactParams(BaseModel):
    max_depth: int = Field(
        default=6,
        ge=1,
        le=10,
        description=(
            "Maximum traversal depth (1–10). "
            "Injected as f-string bound in Cypher — never as $parameter. "
            "Validated here to prevent unbounded graph scans."
        )
    )


# ── AFFECTED NODE — RESPONSE ITEM ────────────────────────────────
# One downstream node returned by fault-impact traversal.
# Different node types have different properties — Optional fields
# are None when the node type does not carry that property.
#
# depth: hops from origin — comes from length(path) in the Cypher MATCH.
# TRAP 5 FIX: never computed via shortestPath() in RETURN clause.

class AffectedNodeOut(BaseModel):
    node_type:   str            = Field(..., description="Node label: Substation, Transformer, SmartMeter")
    node_id:     str            = Field(..., description="Unique node identifier property")
    name:        Optional[str]  = Field(default=None, description="Human-readable name if available")
    depth:       int            = Field(..., description="Hops from fault origin — from length(path)")
    voltage_kV:  Optional[float] = Field(default=None, description="Nominal voltage (Substation/GSP only)")
    rating_kVA:  Optional[int]   = Field(default=None, description="Transformer rating in kVA")
    premise_id:  Optional[str]   = Field(default=None, description="Premise ID (SmartMeter only)")
    tariff_class: Optional[str]  = Field(default=None, description="Tariff class (SmartMeter only)")

    model_config = {"from_attributes": True}


# ── FAULT IMPACT — FULL RESPONSE ──────────────────────────────────
# Used by: GET /grid/fault-impact/{node_id}
# Returns the origin node identity + all downstream affected nodes.

class FaultImpactOut(BaseModel):
    origin_node_id:   str
    origin_node_type: str
    max_depth_used:   int
    affected_count:   int
    affected_nodes:   list[AffectedNodeOut]


# ── RESTORE PATH — RESPONSE ITEM ─────────────────────────────────
# Used by: GET /grid/restore-paths/{node_id}
# Returns alternative feed paths (ALTERNATIVE_FEED relationships)
# that could restore supply to the affected substation.

class RestorePathNodeOut(BaseModel):
    node_id:    str
    node_type:  str
    name:       Optional[str] = None

    model_config = {"from_attributes": True}


class RestorePathOut(BaseModel):
    origin_node_id: str
    paths:          list[list[RestorePathNodeOut]]
    path_count:     int


# ── NODE CREATION — INPUT ─────────────────────────────────────────
# Used by: POST /grid/nodes
#
# label is constrained to NodeLabel Literal — prevents arbitrary labels.
# properties dict carries node-type-specific fields.
# node_id must be present in properties — it is the MERGE key in Cypher.

class NodeIn(BaseModel):
    label:      NodeLabel = Field(..., description="Node label — must be one of the four valid types")
    properties: dict      = Field(..., description="Node properties dict — must include node_id key")

    @property
    def node_id(self) -> str:
        """Convenience accessor — raises if node_id missing from properties."""
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


# ── NODE CREATION — RESPONSE ──────────────────────────────────────
class NodeOut(BaseModel):
    label:      str
    node_id:    str
    properties: dict

    model_config = {"from_attributes": True}


# ── RELATIONSHIP CREATION — INPUT ─────────────────────────────────
# Used by: POST /grid/relationships
#
# rel_type constrained to RelationshipType Literal.
# from_id / to_id are node_id property values — the router looks up
# the actual nodes via MATCH (n {node_id: $from_id}) before MERGE.
# properties dict carries relationship-specific fields (feeder_id, etc.)

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


# ── RELATIONSHIP CREATION — RESPONSE ─────────────────────────────
class RelationshipOut(BaseModel):
    from_id:    str
    to_id:      str
    rel_type:   str
    properties: dict

    model_config = {"from_attributes": True}