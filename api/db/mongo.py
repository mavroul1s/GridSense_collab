# MongoDB connection module — Motor async client.

import os
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from dotenv import load_dotenv

load_dotenv()

# Connection state — client manages its own pool.
_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


# Open the client and ping to verify the server is reachable.
async def connect() -> None:
    """Open the client and ping so a dead Mongo fails startup (Motor connects lazily)."""
    global _client, _db

    # Read the URI (defaults to the Docker service name).
    mongo_uri = os.getenv("MONGO_URI", "mongodb://catalog-db:27017")

    # Create the client and select the database.
    _client = AsyncIOMotorClient(mongo_uri)
    _db = _client.gridsense

    # Force a round-trip to confirm the server is up.
    await _client.admin.command("ping")
    print(f"[MongoDB] Connected to {mongo_uri} — database: gridsense")


# Close the client and clear connection state.
async def disconnect() -> None:
    """Close the client and clear connection state."""
    global _client, _db
    if _client:
        _client.close()
        print("[MongoDB] Connection closed")
    _client = None
    _db = None


# Return the active database handle (raises if connect() was not called).
def get_db() -> AsyncIOMotorDatabase:
    """Return the active database handle, or raise if connect() was not called."""
    if _db is None:
        raise RuntimeError("MongoDB client not initialised — call connect() first")
    return _db


# Make a document JSON-serialisable by stringifying its ObjectId _id.
def serialise_doc(doc: dict | None) -> dict | None:
    """Convert ObjectId _id to str so FastAPI can JSON-encode the document."""
    if doc is None:
        return None
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc


# Return the equipment collection handle.
def equipment_collection():
    """Return the equipment collection."""
    return get_db().equipment
