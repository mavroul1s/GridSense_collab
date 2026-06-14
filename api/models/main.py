# api/main.py
# GridSense FastAPI application entry point
#
# Lifespan context manager handles all DB connections:
#   startup  → connect all 5 databases
#   shutdown → disconnect all 5 databases cleanly
#
# All 5 routers registered with the app here.
# After this file exists, docker compose up --build should boot
# the full stack successfully for the first time.

from contextlib import asynccontextmanager
from fastapi import FastAPI

# ── DB CONNECTION MODULES ─────────────────────────────────────────
import db.cassandra as cassandra_db
import db.neo4j     as neo4j_db
import db.mongo     as mongo_db
import db.postgres  as postgres_db
import db.redis     as redis_db

# ── ROUTERS ───────────────────────────────────────────────────────
from routers.sensors   import router as sensors_router
from routers.grid      import router as grid_router
from routers.equipment import router as equipment_router
from routers.billing   import router as billing_router
from routers.alerts    import router as alerts_router


# ── LIFESPAN ──────────────────────────────────────────────────────
# FastAPI lifespan replaces the deprecated @app.on_event("startup")
# pattern. Everything before `yield` runs at startup; everything
# after runs at shutdown.
#
# Startup order matters:
#   Cassandra — synchronous connect(), safe to call first
#   Neo4j     — async, verify_connectivity() fails fast if unreachable
#   MongoDB   — async, ping() fails fast if unreachable
#   PostgreSQL — async pool creation
#   Redis     — async, ping() fails fast if unreachable
#
# If any connect() raises, the application refuses to start and
# Docker logs the error — no silent half-connected state.

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── STARTUP ───────────────────────────────────────────────────
    print("[GridSense] Starting up — connecting to all databases...")

    # Cassandra — synchronous driver, wrapped in executor in execute_async
    cassandra_db.connect()

    # Neo4j — native async driver
    await neo4j_db.connect()

    # MongoDB — Motor native async
    await mongo_db.connect()

    # PostgreSQL — asyncpg connection pool
    await postgres_db.connect()

    # Redis — redis.asyncio native async
    await redis_db.connect()

    print("[GridSense] All databases connected — API ready.")

    yield   # ← application runs here

    # ── SHUTDOWN ──────────────────────────────────────────────────
    print("[GridSense] Shutting down — closing all database connections...")

    cassandra_db.disconnect()
    await neo4j_db.disconnect()
    await mongo_db.disconnect()
    await postgres_db.disconnect()
    await redis_db.disconnect()

    print("[GridSense] All connections closed.")


# ── APP INSTANCE ──────────────────────────────────────────────────
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


# ── ROUTER REGISTRATION ───────────────────────────────────────────
# Each router carries its own prefix (/sensors, /grid, /equipment,
# /billing, /alerts) — defined in the individual router files.
app.include_router(sensors_router)
app.include_router(grid_router)
app.include_router(equipment_router)
app.include_router(billing_router)
app.include_router(alerts_router)


# ── HEALTH CHECK ──────────────────────────────────────────────────
# Simple liveness endpoint — returns 200 if the API process is running.
# Does not verify DB connectivity (that is done at startup).
# Useful for docker compose healthcheck and load balancer probes.

@app.get("/health", tags=["Health"])
async def health():
    return {
        "status":  "ok",
        "service": "gridsense-api",
        "version": "1.0.0",
    }


# ── ROOT ──────────────────────────────────────────────────────────
@app.get("/", tags=["Health"])
async def root():
    return {
        "message": "GridSense API is running",
        "docs":    "/docs",
        "health":  "/health",
    }