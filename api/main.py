# GridSense FastAPI application entry point.
# Lifespan connects all 5 databases on startup and disconnects on shutdown.

from contextlib import asynccontextmanager
from fastapi import FastAPI

import db.cassandra as cassandra_db
import db.neo4j     as neo4j_db
import db.mongo     as mongo_db
import db.postgres  as postgres_db
import db.redis     as redis_db

from routers.sensors   import router as sensors_router
from routers.grid      import router as grid_router
from routers.equipment import router as equipment_router
from routers.billing   import router as billing_router
from routers.alerts    import router as alerts_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup — any failed connect() aborts the app rather than running half-connected.
    print("[GridSense] Starting up — connecting to all databases...")
    cassandra_db.connect()          # synchronous driver
    await neo4j_db.connect()
    await mongo_db.connect()
    await postgres_db.connect()
    await redis_db.connect()
    print("[GridSense] All databases connected — API ready.")

    yield

    # Shutdown
    print("[GridSense] Shutting down — closing all database connections...")
    cassandra_db.disconnect()
    await neo4j_db.disconnect()
    await mongo_db.disconnect()
    await postgres_db.disconnect()
    await redis_db.disconnect()
    print("[GridSense] All connections closed.")


app = FastAPI(
    title="GridSense API",
    description=(
        "Polyglot persistence IoT platform for smart grid monitoring. "
        "Cassandra (time-series) · Neo4j (topology) · MongoDB (catalog) · "
        "PostgreSQL (billing) · Redis (cache + pub/sub)"
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Each router carries its own prefix (/sensors, /grid, /equipment, /billing, /alerts).
app.include_router(sensors_router)
app.include_router(grid_router)
app.include_router(equipment_router)
app.include_router(billing_router)
app.include_router(alerts_router)


@app.get("/health", tags=["Health"])
async def health():
    """Liveness probe — does not verify DB connectivity."""
    return {
        "status":  "ok",
        "service": "gridsense-api",
        "version": "1.0.0",
    }


@app.get("/", tags=["Health"])
async def root():
    return {
        "message": "GridSense API is running",
        "docs":    "/docs",
        "health":  "/health",
    }
