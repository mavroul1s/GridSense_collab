# Neo4j connection module — uses the driver's native async support.

import os
from neo4j import AsyncGraphDatabase, AsyncDriver
from dotenv import load_dotenv

load_dotenv()

# Connection state — single driver shared across requests.
_driver: AsyncDriver | None = None


# Open the async driver and verify the database is reachable.
async def connect() -> None:
    """Open the async driver and verify the database is reachable."""
    global _driver

    # Password is required — fail fast if it is missing.
    neo4j_password = os.getenv("NEO4J_PASSWORD")
    if not neo4j_password:
        raise RuntimeError("NEO4J_PASSWORD not set in environment")

    # Create the driver against the Docker service name over Bolt.
    _driver = AsyncGraphDatabase.driver(
        "bolt://graph-db:7687",
        auth=("neo4j", neo4j_password),
    )

    # Probe connectivity so a dead database aborts startup.
    await _driver.verify_connectivity()
    print("[Neo4j] Connected to bolt://graph-db:7687")


# Close the driver and clear connection state.
async def disconnect() -> None:
    """Close the driver and clear connection state."""
    global _driver
    if _driver:
        await _driver.close()
        print("[Neo4j] Connection closed")
    _driver = None


# Return the active driver (raises if connect() was not called).
def get_driver() -> AsyncDriver:
    """Return the active driver, or raise if connect() was not called."""
    if _driver is None:
        raise RuntimeError("Neo4j driver not initialised — call connect() first")
    return _driver


# Check whether a node with the given node_id exists.
async def node_exists(node_id: str) -> bool:
    """Return whether any node carries the given node_id (lets callers tell 404 from empty result)."""
    driver = get_driver()
    # Count matching nodes inside a read session.
    async with driver.session(database="neo4j") as session:
        result = await session.run(
            "MATCH (n {node_id: $node_id}) RETURN count(n) AS cnt",
            node_id=node_id
        )
        record = await result.single()
        return record["cnt"] > 0


# Execute a read query and return the records as plain dicts.
async def run_query(cypher: str, parameters: dict | None = None) -> list[dict]:
    """Execute a read query and return the records as plain dicts."""
    driver = get_driver()
    async with driver.session(database="neo4j") as session:
        result = await session.run(cypher, parameters or {})
        records = await result.data()
        return records


# Execute a write inside a managed (auto-retried) transaction.
async def run_write(cypher: str, parameters: dict | None = None) -> list[dict]:
    """Execute a write inside a managed transaction (auto-retried on transient errors)."""
    driver = get_driver()

    # Transaction body — runs the write and returns its rows.
    async def _tx(tx):
        result = await tx.run(cypher, parameters or {})
        return await result.data()

    # Run the body in a write transaction.
    async with driver.session(database="neo4j") as session:
        return await session.execute_write(_tx)
