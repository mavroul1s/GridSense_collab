# api/db/mongo.py
# MongoDB async connection module for GridSense API
#
# Motor (AsyncIOMotorClient) is natively async — no run_in_executor needed.
# This follows the exact pattern from Week 5 Slide 81 and the Week 5 lab,
# with one change: hostname is 'catalog-db' (Docker service name) not
# 'localhost' (Trap 10 fix).

import os
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from dotenv import load_dotenv

load_dotenv()

# ── CONNECTION STATE ──────────────────────────────────────────────
# Module-level client — Motor manages an internal connection pool.
# A single client instance is shared across all requests.
_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


# ── CONNECT ───────────────────────────────────────────────────────
async def connect() -> None:
    """
    Initialise the Motor async client at application startup.
    Called from main.py lifespan context manager.

    URI: mongodb://catalog-db:27017
    'catalog-db' is the Docker service name (Trap 10 fix).
    Week 4 Slide 16: containers discover each other by service name.

    Week 5 Slide 81: AsyncIOMotorClient is the correct async driver.
    Database name: gridsense — matches the collection namespace used
    in all equipment endpoints.
    """
    global _client, _db

    # catalog-db has no auth in docker-compose.yml (no MONGO_INITDB_ROOT_*)
    # so the URI is plain — no credentials required for dev
    mongo_uri = os.getenv("MONGO_URI", "mongodb://catalog-db:27017")

    _client = AsyncIOMotorClient(mongo_uri)
    _db = _client.gridsense

    # Motor is lazy — it does not connect until the first operation.
    # Ping the server explicitly so startup fails fast if Mongo is down.
    await _client.admin.command("ping")
    print(f"[MongoDB] Connected to {mongo_uri} — database: gridsense")


# ── DISCONNECT ────────────────────────────────────────────────────
async def disconnect() -> None:
    """
    Close the Motor client at application shutdown.
    Called from main.py lifespan context manager on exit.
    """
    global _client, _db
    if _client:
        _client.close()
        print("[MongoDB] Connection closed")
    _client = None
    _db = None


# ── DATABASE ACCESSOR ─────────────────────────────────────────────
def get_db() -> AsyncIOMotorDatabase:
    """
    Returns the active Motor database handle.
    Raises RuntimeError if connect() has not been called.

    Usage in routers:
        from db.mongo import get_db
        db = get_db()
        doc = await db.equipment.find_one({"asset_id": asset_id})
    """
    if _db is None:
        raise RuntimeError("MongoDB client not initialised — call connect() first")
    return _db


# ── OBJECT ID SERIALISER ──────────────────────────────────────────
def serialise_doc(doc: dict | None) -> dict | None:
    """
    Convert MongoDB's ObjectId _id field to a plain string.

    Week 5 Slide 83:
        if doc:
            doc["_id"] = str(doc["_id"])

    FastAPI uses Python's json.dumps() internally which cannot
    serialise bson.ObjectId — it raises a TypeError at runtime.
    Converting to str before returning from any endpoint fixes this.

    Also handles the case where doc is None (find_one returned nothing)
    by returning None — the router handles the 404 response.

    Args:
        doc: raw MongoDB document dict, or None

    Returns:
        Document with _id as string, or None
    """
    if doc is None:
        return None
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc


# ── COLLECTION ACCESSORS ──────────────────────────────────────────
# Convenience functions so routers import a named collection rather
# than calling get_db().collection_name directly.
# This makes router code cleaner and collection names easier to change.

def equipment_collection():
    """Returns the equipment collection — used by all /equipment endpoints."""
    return get_db().equipment