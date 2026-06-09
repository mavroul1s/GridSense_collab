# api/db/neo4j.py
# Neo4j async connection module for GridSense API
#
# The official neo4j Python driver (v5.x) supports native async via
# AsyncGraphDatabase.driver — no run_in_executor wrapper needed.
# This is different from cassandra-driver which is synchronous only.

import os
from neo4j import AsyncGraphDatabase, AsyncDriver
from dotenv import load_dotenv

load_dotenv()

# ── CONNECTION STATE ──────────────────────────────────────────────
# Module-level driver — initialised once at startup, closed at shutdown
_driver: AsyncDriver | None = None


# ── CONNECT ───────────────────────────────────────────────────────
async def connect() -> None:
    """
    Initialise the Neo4j async driver at application startup.
    Called from main.py lifespan context manager.

    URI: bolt://graph-db:7687
    'graph-db' is the Docker service name (Trap 10 fix).
    Week 4 Slide 16: containers discover each other by service name.

    Bolt protocol (port 7687) is the binary wire protocol used by
    all Neo4j drivers — faster than HTTP (port 7474 is browser only).
    """
    global _driver

    neo4j_password = os.getenv("NEO4J_PASSWORD")
    if not neo4j_password:
        raise RuntimeError("NEO4J_PASSWORD not set in environment")

    _driver = AsyncGraphDatabase.driver(
        "bolt://graph-db:7687",          # service name — NOT localhost
        auth=("neo4j", neo4j_password),
    )

    # Verify connectivity at startup — fails fast if Neo4j is unreachable
    await _driver.verify_connectivity()
    print("[Neo4j] Connected to bolt://graph-db:7687")


# ── DISCONNECT ────────────────────────────────────────────────────
async def disconnect() -> None:
    """
    Close the Neo4j driver at application shutdown.
    Called from main.py lifespan context manager on exit.
    """
    global _driver
    if _driver:
        await _driver.close()
        print("[Neo4j] Connection closed")
    _driver = None


# ── DRIVER ACCESSOR ───────────────────────────────────────────────
def get_driver() -> AsyncDriver:
    """
    Returns the active Neo4j async driver.
    Raises RuntimeError if connect() has not been called.
    """
    if _driver is None:
        raise RuntimeError("Neo4j driver not initialised — call connect() first")
    return _driver


# ── NODE EXISTS HELPER ────────────────────────────────────────────
async def node_exists(node_id: str) -> bool:
    """
    Check whether any node with the given node_id property exists
    in the graph.

    This helper is required by the grid router endpoints that must
    return 404 when a node_id is not found rather than an empty list.

    The assignment's sample code references '_node_exists' but never
    defines it — this is Trap 6. Implemented here and imported by
    api/routers/grid.py.

    Args:
        node_id: the node_id property value to look up

    Returns:
        True if at least one matching node exists, False otherwise
    """
    driver = get_driver()
    async with driver.session(database="neo4j") as session:
        result = await session.run(
            "MATCH (n {node_id: $node_id}) RETURN count(n) AS cnt",
            node_id=node_id
        )
        record = await result.single()
        return record["cnt"] > 0


# ── GENERIC QUERY HELPER ──────────────────────────────────────────
async def run_query(cypher: str, parameters: dict | None = None) -> list[dict]:
    """
    Execute a Cypher read query and return results as a list of dicts.

    Abstracts session management so router functions stay clean —
    they call run_query() without managing sessions directly.

    Args:
        cypher    : Cypher query string
        parameters: dict of query parameters, or None

    Returns:
        List of result records as plain dicts
    """
    driver = get_driver()
    async with driver.session(database="neo4j") as session:
        result = await session.run(cypher, parameters or {})
        records = await result.data()
        return records


# ── GENERIC WRITE HELPER ──────────────────────────────────────────
async def run_write(cypher: str, parameters: dict | None = None) -> list[dict]:
    """
    Execute a Cypher write query inside an explicit write transaction.

    Write transactions are retried automatically by the driver on
    transient errors (e.g. leader election during Neo4j failover).
    This is the correct pattern for POST /grid/nodes and
    POST /grid/relationships endpoints.

    Args:
        cypher    : Cypher write query string
        parameters: dict of query parameters, or None

    Returns:
        List of result records as plain dicts (empty for pure writes)
    """
    driver = get_driver()

    async def _tx(tx):
        result = await tx.run(cypher, parameters or {})
        return await result.data()

    async with driver.session(database="neo4j") as session:
        return await session.execute_write(_tx)