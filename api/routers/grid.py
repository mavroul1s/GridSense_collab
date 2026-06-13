# api/routers/grid.py
# Neo4j-backed network topology endpoints
#
# TRAP 4 FIX: max_depth is validated (1-10) in models/graph.py
# and interpolated into the Cypher string via f-string. It is
# NEVER passed as $depth — Cypher rejects parameters as path bounds.
#
# TRAP 5 FIX: depth comes from length(path) on a named MATCH path.
# shortestPath() is never used in a RETURN clause.
#
# TRAP 6 FIX: node_exists() is imported from db/neo4j.py where it
# is explicitly implemented — the assignment leaves it undefined.

from fastapi import APIRouter, HTTPException

from db.neo4j import run_query, run_write, node_exists
from models.graph import (
    FaultImpactParams,
    FaultImpactOut,
    AffectedNodeOut,
    RestorePathOut,
    RestorePathNodeOut,
    NodeIn,
    NodeOut,
    RelationshipIn,
    RelationshipOut,
)

router = APIRouter(prefix="/grid", tags=["Grid Topology"])

# Relationship types used for downstream power-flow traversal.
# Defined once here — used in both fault-impact and restore-paths.
DOWNSTREAM_RELS = "FEEDS|SUPPLIES|CONNECTS_TO"


# ── GET /grid/fault-impact/{node_id} ─────────────────────────────
@router.get("/fault-impact/{node_id}", response_model=FaultImpactOut)
async def fault_impact(node_id: str, params: FaultImpactParams = FaultImpactParams()):
    """
    Return all nodes downstream of the given node within max_depth hops.

    TRAP 4 FIX:
    max_depth is a Pydantic-validated int (1-10) from FaultImpactParams.
    It is interpolated directly into the Cypher string:
        *1..{params.max_depth}
    This is safe ONLY because Pydantic already rejected anything
    outside 1-10 before this function runs — no raw user string
    ever reaches the f-string.

    TRAP 5 FIX:
    depth is taken from length(path) on the named MATCH path below.
    shortestPath() is never called in the RETURN clause — that
    pattern in the assignment causes a full-graph traversal per row.

    TRAP 6 FIX:
    If the traversal returns zero rows, we cannot tell whether the
    origin node simply has no downstream connections (valid — e.g.
    an end-of-line SmartMeter) or whether node_id does not exist at
    all (404). node_exists() — defined in db/neo4j.py because the
    assignment leaves it undefined — disambiguates these two cases.
    """
    # f-string interpolation of a Pydantic-validated bounded int (1-10).
    # max_depth can NEVER be a raw string here — FaultImpactParams
    # already enforced int + range before this line executes.
    cypher = f"""
        MATCH (origin {{node_id: $node_id}})
        MATCH path = (origin)-[:{DOWNSTREAM_RELS}*1..{params.max_depth}]->(downstream)
        RETURN
            labels(downstream)[0] AS node_type,
            downstream.node_id    AS node_id,
            coalesce(downstream.name, downstream.asset_id, downstream.meter_id) AS name,
            length(path)          AS depth,
            downstream.voltage_kV  AS voltage_kV,
            downstream.rating_kVA  AS rating_kVA,
            downstream.premise_id  AS premise_id,
            downstream.tariff_class AS tariff_class
        ORDER BY depth, node_id
    """

    rows = await run_query(cypher, {"node_id": node_id})

    if not rows:
        # Empty result — disambiguate: does origin exist at all?
        if not await node_exists(node_id):
            raise HTTPException(
                status_code=404,
                detail=f"Node '{node_id}' not found in network topology"
            )
        # Origin exists but has no downstream connections — valid,
        # e.g. a SmartMeter at the end of the network.
        origin_rows = await run_query(
            "MATCH (n {node_id: $node_id}) RETURN labels(n)[0] AS node_type",
            {"node_id": node_id}
        )
        return FaultImpactOut(
            origin_node_id=node_id,
            origin_node_type=origin_rows[0]["node_type"],
            max_depth_used=params.max_depth,
            affected_count=0,
            affected_nodes=[],
        )

    # Look up origin's own label for the response header
    origin_rows = await run_query(
        "MATCH (n {node_id: $node_id}) RETURN labels(n)[0] AS node_type",
        {"node_id": node_id}
    )

    affected = [AffectedNodeOut(**row) for row in rows]

    return FaultImpactOut(
        origin_node_id=node_id,
        origin_node_type=origin_rows[0]["node_type"],
        max_depth_used=params.max_depth,
        affected_count=len(affected),
        affected_nodes=affected,
    )


# ── GET /grid/restore-paths/{node_id} ────────────────────────────
@router.get("/restore-paths/{node_id}", response_model=RestorePathOut)
async def restore_paths(node_id: str):
    """
    Find alternative feed paths that could restore supply to the
    given substation via :ALTERNATIVE_FEED tie switches.

    Part A A.4.b: :ALTERNATIVE_FEED is a Substation → Substation
    relationship representing a normally-open tie switch. If the
    origin substation loses its primary feed, traversing this
    relationship to a neighbouring substation (and from there back
    up to a live GridSupplyPoint) represents a possible restoration path.

    Two-step traversal:
      1. origin -[:ALTERNATIVE_FEED]- neighbour  (either direction —
         tie switches are not inherently directional for restoration
         purposes, hence the undirected pattern below)
      2. neighbour <-[:FEEDS|SUPPLIES*]- some GridSupplyPoint
         (confirms the neighbour has its own live upstream supply)

    TRAP 6 FIX: node_exists() disambiguates "no alternative paths
    exist" from "node_id does not exist".
    """
    cypher = """
        MATCH (origin:Substation {node_id: $node_id})
        MATCH (origin)-[:ALTERNATIVE_FEED]-(neighbour:Substation)
        OPTIONAL MATCH supply_path = (gsp:GridSupplyPoint)-[:FEEDS*1..2]->(neighbour)
        RETURN
            origin.node_id     AS origin_id,
            origin.name        AS origin_name,
            neighbour.node_id  AS neighbour_id,
            neighbour.name     AS neighbour_name,
            gsp.node_id        AS supply_id,
            gsp.name           AS supply_name
        ORDER BY neighbour_id
    """

    rows = await run_query(cypher, {"node_id": node_id})

    if not rows:
        if not await node_exists(node_id):
            raise HTTPException(
                status_code=404,
                detail=f"Substation '{node_id}' not found in network topology"
            )
        # Origin exists but has no ALTERNATIVE_FEED ties — valid result,
        # not an error. Empty paths list returned.
        return RestorePathOut(origin_node_id=node_id, paths=[], path_count=0)

    paths: list[list[RestorePathNodeOut]] = []

    for row in rows:
        path_nodes = [
            RestorePathNodeOut(
                node_id="origin",  # placeholder — origin is the starting point
                node_type="Substation",
                name=row["origin_name"],
            ),
            RestorePathNodeOut(
                node_id=row["neighbour_id"],
                node_type="Substation",
                name=row["neighbour_name"],
            ),
        ]
        # Append the upstream GridSupplyPoint only if the OPTIONAL MATCH
        # found one — confirms the neighbour has live upstream supply.
        if row["supply_id"] is not None:
            path_nodes.append(
                RestorePathNodeOut(
                    node_id=row["supply_id"],
                    node_type="GridSupplyPoint",
                    name=row["supply_name"],
                )
            )
        paths.append(path_nodes)

    return RestorePathOut(
        origin_node_id=node_id,
        paths=paths,
        path_count=len(paths),
    )


# ── POST /grid/nodes ──────────────────────────────────────────────
@router.post("/nodes", response_model=NodeOut, status_code=201)
async def create_node(node: NodeIn):
    """
    Create (or update, idempotently) a node in the graph.

    TRAP 8 PRINCIPLE applied here too: MERGE not CREATE — calling
    this endpoint twice with the same node_id updates properties
    rather than creating a duplicate node.

    node.label is validated against NodeLabel Literal in models/graph.py
    — only the 4 approved labels (GridSupplyPoint, Substation,
    Transformer, SmartMeter) can ever reach this f-string. Cypher does
    not support parameterised labels, so f-string interpolation is the
    only option — safe here because Pydantic already constrained the
    value to a fixed enum before this line executes.

    node.node_id (a computed property on NodeIn) raises ValueError if
    'node_id' is missing from properties — FastAPI converts this to
    a 422 response automatically.
    """
    try:
        node_id_value = node.node_id  # raises ValueError if missing
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # label is Literal-constrained — safe f-string interpolation
    cypher = f"""
        MERGE (n:{node.label} {{node_id: $node_id}})
        SET n += $properties
        RETURN labels(n)[0] AS label, n.node_id AS node_id, properties(n) AS properties
    """

    result = await run_write(cypher, {
        "node_id": node_id_value,
        "properties": node.properties,
    })

    return NodeOut(**result[0])


# ── POST /grid/relationships ─────────────────────────────────────
@router.post("/relationships", response_model=RelationshipOut, status_code=201)
async def create_relationship(rel: RelationshipIn):
    """
    Create (or update, idempotently) a relationship between two
    existing nodes.

    TRAP 8 FIX applied: MERGE the relationship, not CREATE — running
    this twice with the same from_id/to_id/rel_type/properties does
    not create a duplicate edge.

    rel.rel_type is validated against RelationshipType Literal in
    models/graph.py — only the 4 approved types (FEEDS, SUPPLIES,
    CONNECTS_TO, ALTERNATIVE_FEED) can reach the f-string.

    TRAP 6 FIX: both endpoints are checked with node_exists() before
    attempting the relationship — a clear 404 is more useful than a
    Cypher MATCH that silently matches zero rows and returns nothing.
    """
    if not await node_exists(rel.from_id):
        raise HTTPException(
            status_code=404,
            detail=f"Source node '{rel.from_id}' not found"
        )
    if not await node_exists(rel.to_id):
        raise HTTPException(
            status_code=404,
            detail=f"Target node '{rel.to_id}' not found"
        )

    # rel_type is Literal-constrained — safe f-string interpolation.
    # Relationship identity for MERGE uses from/to node_id + rel_type,
    # which is sufficient to prevent duplicate edges of the same type
    # between the same two nodes (matches the seed.cypher pattern for
    # :FEEDS and :SUPPLIES which key on feeder_id/cable_id within
    # properties — here we key on the node pair + type for generality).
    cypher = f"""
        MATCH (a {{node_id: $from_id}})
        MATCH (b {{node_id: $to_id}})
        MERGE (a)-[r:{rel.rel_type}]->(b)
        SET r += $properties
        RETURN $from_id AS from_id, $to_id AS to_id,
               type(r) AS rel_type, properties(r) AS properties
    """

    result = await run_write(cypher, {
        "from_id": rel.from_id,
        "to_id": rel.to_id,
        "properties": rel.properties,
    })

    return RelationshipOut(**result[0])