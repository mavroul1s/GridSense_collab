# MongoDB connection module — Motor async client.

import os
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from dotenv import load_dotenv

load_dotenv()

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


async def connect() -> None:
    """Open the client and ping so a dead Mongo fails startup (Motor connects lazily)."""
    global _client, _db

    mongo_uri = os.getenv("MONGO_URI", "mongodb://catalog-db:27017")

    _client = AsyncIOMotorClient(mongo_uri)
    _db = _client.gridsense

    await _client.admin.command("ping")
    print(f"[MongoDB] Connected to {mongo_uri} — database: gridsense")


async def disconnect() -> None:
    global _client, _db
    if _client:
        _client.close()
        print("[MongoDB] Connection closed")
    _client = None
    _db = None


def get_db() -> AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("MongoDB client not initialised — call connect() first")
    return _db


def serialise_doc(doc: dict | None) -> dict | None:
    """Convert ObjectId _id to str so FastAPI can JSON-encode the document."""
    if doc is None:
        return None
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc


def equipment_collection():
    return get_db().equipment
