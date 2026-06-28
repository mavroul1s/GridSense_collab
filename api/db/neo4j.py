# Neo4j connection module — uses the driver's native async support.

import os
from neo4j import AsyncGraphDatabase, AsyncDriver
from dotenv import load_dotenv

load_dotenv()

_driver: AsyncDriver | None = None


async def connect() -> None:
    """Open the async driver and fail fast if Neo4j is unreachable."""
    global _driver

    neo4j_password = os.getenv("NEO4J_PASSWORD")
    if not neo4j_password:
        raise RuntimeError("NEO4J_PASSWORD not set in environment")

    _driver = AsyncGraphDatabase.driver(
        "bolt://graph-db:7687",
        auth=("neo4j", neo4j_password),
    )

    await _driver.verify_connectivity()
    print("[Neo4j] Connected to bolt://graph-db:7687")


async def disconnect() -> None:
    global _driver
    if _driver:
        await _driver.close()
        print("[Neo4j] Connection closed")
    _driver = None


def get_driver() -> AsyncDriver:
    if _driver is None:
        raise RuntimeError("Neo4j driver not initialised — call connect() first")
    return _driver


async def node_exists(node_id: str) -> bool:
    """Whether any node carries the given node_id — used to distinguish 404 from empty result."""
    driver = get_driver()
    async with driver.session(database="neo4j") as session:
        result = await session.run(
            "MATCH (n {node_id: $node_id}) RETURN count(n) AS cnt",
            node_id=node_id
        )
        record = await result.single()
        return record["cnt"] > 0


async def run_query(cypher: str, parameters: dict | None = None) -> list[dict]:
    """Execute a read query and return records as plain dicts."""
    driver = get_driver()
    async with driver.session(database="neo4j") as session:
        result = await session.run(cypher, parameters or {})
        records = await result.data()
        return records


async def run_write(cypher: str, parameters: dict | None = None) -> list[dict]:
    """Execute a write inside a managed transaction (auto-retried on transient errors)."""
    driver = get_driver()

    async def _tx(tx):
        result = await tx.run(cypher, parameters or {})
        return await result.data()

    async with driver.session(database="neo4j") as session:
        return await session.execute_write(_tx)
