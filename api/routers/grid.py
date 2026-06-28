# Neo4j-backed network topology endpoints.

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

# Downstream power-flow relationship types, shared by both traversals.
DOWNSTREAM_RELS = "FEEDS|SUPPLIES|CONNECTS_TO"


@router.get("/fault-impact/{node_id}", response_model=FaultImpactOut)
async def fault_impact(node_id: str, params: FaultImpactParams = FaultImpactParams()):
    """All nodes downstream of node_id within max_depth hops."""
    # max_depth is f-string-interpolated because Cypher rejects parameters as path bounds;
    # safe only because FaultImpactParams already validated it as an int in 1-10.
    # depth comes from length(path), not shortestPath() in RETURN (avoids per-row full scans).
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
        # Empty could mean "no downstream" or "node missing" — disambiguate.
        if not await node_exists(node_id):
            raise HTTPException(
                status_code=404,
                detail=f"Node '{node_id}' not found in network topology"
            )
        # Node exists but is a leaf (e.g. end-of-line SmartMeter).
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


@router.get("/restore-paths/{node_id}", response_model=RestorePathOut)
async def restore_paths(node_id: str):
    """Alternative feed paths to a substation via :ALTERNATIVE_FEED tie switches."""
    # 1. origin -[:ALTERNATIVE_FEED]- neighbour (undirected — ties aren't directional)
    # 2. confirm the neighbour has its own live upstream supply
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
        # Exists but has no tie switches — valid, empty result.
        return RestorePathOut(origin_node_id=node_id, paths=[], path_count=0)

    paths: list[list[RestorePathNodeOut]] = []

    for row in rows:
        path_nodes = [
            RestorePathNodeOut(
                node_id=row["origin_id"],
                node_type="Substation",
                name=row["origin_name"],
            ),
            RestorePathNodeOut(
                node_id=row["neighbour_id"],
                node_type="Substation",
                name=row["neighbour_name"],
            ),
        ]
        # Append the upstream GSP only if the OPTIONAL MATCH found one.
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


@router.post("/nodes", response_model=NodeOut, status_code=201)
async def create_node(node: NodeIn):
    """Create or idempotently update a node (MERGE on node_id)."""
    try:
        node_id_value = node.node_id  # raises ValueError if missing
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # label is Literal-constrained, so f-string interpolation is safe (Cypher can't parameterise labels).
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


@router.post("/relationships", response_model=RelationshipOut, status_code=201)
async def create_relationship(rel: RelationshipIn):
    """Create or idempotently update a relationship between two existing nodes (MERGE)."""
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

    # rel_type is Literal-constrained, so f-string interpolation is safe.
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
