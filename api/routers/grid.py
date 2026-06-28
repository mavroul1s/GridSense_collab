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


# GET /grid/fault-impact/{id} — nodes downstream of a fault origin.
@router.get("/fault-impact/{node_id}", response_model=FaultImpactOut)
async def fault_impact(node_id: str, params: FaultImpactParams = FaultImpactParams()):
    """All nodes downstream of node_id within max_depth hops."""
    # Build the traversal. max_depth is interpolated (Cypher can't parameterise a
    # path bound) but is safe because the model validated it to an int in 1-10.
    # depth uses length(path), not shortestPath(), to avoid a per-row full scan.
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

    # Run the traversal.
    rows = await run_query(cypher, {"node_id": node_id})

    # Empty result — distinguish a missing node from a leaf node.
    if not rows:
        # Node truly absent → 404.
        if not await node_exists(node_id):
            raise HTTPException(
                status_code=404,
                detail=f"Node '{node_id}' not found in network topology"
            )
        # Node exists but has nothing downstream → return an empty impact set.
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

    # Look up the origin's label for the response header.
    origin_rows = await run_query(
        "MATCH (n {node_id: $node_id}) RETURN labels(n)[0] AS node_type",
        {"node_id": node_id}
    )

    # Map rows to the affected-node model and return.
    affected = [AffectedNodeOut(**row) for row in rows]

    return FaultImpactOut(
        origin_node_id=node_id,
        origin_node_type=origin_rows[0]["node_type"],
        max_depth_used=params.max_depth,
        affected_count=len(affected),
        affected_nodes=affected,
    )


# GET /grid/restore-paths/{id} — alternative backup feed paths.
@router.get("/restore-paths/{node_id}", response_model=RestorePathOut)
async def restore_paths(node_id: str):
    """Alternative feed paths to a substation via :ALTERNATIVE_FEED tie switches."""
    # Find tie-switch neighbours and confirm each has its own live upstream supply.
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

    # Run the query.
    rows = await run_query(cypher, {"node_id": node_id})

    # Empty result — distinguish a missing node from one with no tie switches.
    if not rows:
        if not await node_exists(node_id):
            raise HTTPException(
                status_code=404,
                detail=f"Substation '{node_id}' not found in network topology"
            )
        return RestorePathOut(origin_node_id=node_id, paths=[], path_count=0)

    # Build one path per neighbour.
    paths: list[list[RestorePathNodeOut]] = []

    for row in rows:
        # Start each path with origin → neighbour.
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
        # Append the upstream GSP when the optional match found one.
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


# POST /grid/nodes — create or update a topology node.
@router.post("/nodes", response_model=NodeOut, status_code=201)
async def create_node(node: NodeIn):
    """Create or idempotently update a node (MERGE on node_id)."""
    # Pull node_id out of properties; a missing key becomes a 422.
    try:
        node_id_value = node.node_id
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # MERGE on node_id. label is Literal-constrained, so the f-string is safe.
    cypher = f"""
        MERGE (n:{node.label} {{node_id: $node_id}})
        SET n += $properties
        RETURN labels(n)[0] AS label, n.node_id AS node_id, properties(n) AS properties
    """

    # Run the write and return the stored node.
    result = await run_write(cypher, {
        "node_id": node_id_value,
        "properties": node.properties,
    })

    return NodeOut(**result[0])


# POST /grid/relationships — create or update an edge between nodes.
@router.post("/relationships", response_model=RelationshipOut, status_code=201)
async def create_relationship(rel: RelationshipIn):
    """Create or idempotently update a relationship between two existing nodes (MERGE)."""
    # Both endpoints must exist — return a clear 404 otherwise.
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

    # MERGE the edge. rel_type is Literal-constrained, so the f-string is safe.
    cypher = f"""
        MATCH (a {{node_id: $from_id}})
        MATCH (b {{node_id: $to_id}})
        MERGE (a)-[r:{rel.rel_type}]->(b)
        SET r += $properties
        RETURN $from_id AS from_id, $to_id AS to_id,
               type(r) AS rel_type, properties(r) AS properties
    """

    # Run the write and return the stored relationship.
    result = await run_write(cypher, {
        "from_id": rel.from_id,
        "to_id": rel.to_id,
        "properties": rel.properties,
    })

    return RelationshipOut(**result[0])
