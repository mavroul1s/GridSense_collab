# GridSense FastAPI application entry point.

from contextlib import asynccontextmanager
from fastapi import FastAPI

# Database connection modules — one per backing store.
import db.cassandra as cassandra_db
import db.neo4j     as neo4j_db
import db.mongo     as mongo_db
import db.postgres  as postgres_db
import db.redis     as redis_db

# Routers — one per domain area, each with its own prefix.
from routers.sensors   import router as sensors_router
from routers.grid      import router as grid_router
from routers.equipment import router as equipment_router
from routers.billing   import router as billing_router
from routers.alerts    import router as alerts_router


# Manage the app lifecycle: open DBs on startup, close them on shutdown.
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Open all DB connections on startup, close them on shutdown."""
    # Connect to every database; a failure here aborts startup.
    print("[GridSense] Starting up — connecting to all databases...")
    cassandra_db.connect()
    await neo4j_db.connect()
    await mongo_db.connect()
    await postgres_db.connect()
    await redis_db.connect()
    print("[GridSense] All databases connected — API ready.")

    # Hand control to the running application.
    yield

    # Tear down every connection in turn.
    print("[GridSense] Shutting down — closing all database connections...")
    cassandra_db.disconnect()
    await neo4j_db.disconnect()
    await mongo_db.disconnect()
    await postgres_db.disconnect()
    await redis_db.disconnect()
    print("[GridSense] All connections closed.")


# Build the app instance and wire in the lifespan handler.
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

# Mount each domain router on the app.
app.include_router(sensors_router)
app.include_router(grid_router)
app.include_router(equipment_router)
app.include_router(billing_router)
app.include_router(alerts_router)


# Liveness probe — reports the process is up (does not check DBs).
@app.get("/health", tags=["Health"])
async def health():
    return {
        "status":  "ok",
        "service": "gridsense-api",
        "version": "1.0.0",
    }


# Root endpoint — points callers to the docs and health check.
@app.get("/", tags=["Health"])
async def root():
    return {
        "message": "GridSense API is running",
        "docs":    "/docs",
        "health":  "/health",
    }
