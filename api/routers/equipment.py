# MongoDB-backed equipment catalog endpoints.
# One collection holds three schema shapes (Transformer/SmartMeter/ProtectionRelay).

from fastapi import APIRouter, HTTPException, Body
from typing import Union

from db.mongo import equipment_collection, serialise_doc
from models.mongo import (
    TransformerIn,
    SmartMeterIn,
    ProtectionRelayIn,
    EquipmentUpdate,
)

router = APIRouter(prefix="/equipment", tags=["Equipment Catalog"])

# Accepted input shapes; FastAPI picks one via the equipment_type discriminator.
EquipmentIn = Union[TransformerIn, SmartMeterIn, ProtectionRelayIn]


# POST /equipment — insert a new equipment record.
@router.post("", status_code=201)
async def create_equipment(item: EquipmentIn = Body(...)):
    """Insert a new equipment record (validated against its equipment_type)."""
    collection = equipment_collection()

    # Reject a duplicate asset_id.
    existing = await collection.find_one({"asset_id": item.asset_id})
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Equipment with asset_id '{item.asset_id}' already exists"
        )

    # Dump with mode="json" so dates match what GET returns, then insert.
    doc = item.model_dump(mode="json")
    await collection.insert_one(doc)

    return {
        "status":         "created",
        "asset_id":       item.asset_id,
        "equipment_type": item.equipment_type,
    }


# GET /equipment/{asset_id} — fetch one equipment record.
@router.get("/{asset_id}")
async def get_equipment(asset_id: str):
    """Fetch one equipment record by asset_id (raw dict — shapes vary by type)."""
    collection = equipment_collection()

    # Look up the document; 404 if absent.
    doc = await collection.find_one({"asset_id": asset_id})
    if doc is None:
        raise HTTPException(
            status_code=404,
            detail=f"Equipment with asset_id '{asset_id}' not found"
        )

    # Stringify _id so it is JSON-serialisable.
    return serialise_doc(doc)


# PATCH /equipment/{asset_id} — partial update of a record.
@router.patch("/{asset_id}")
async def update_equipment(asset_id: str, update: EquipmentUpdate):
    """Partial update — only fields present in the body are written."""
    collection = equipment_collection()

    # 404 if the record does not exist.
    existing = await collection.find_one({"asset_id": asset_id})
    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"Equipment with asset_id '{asset_id}' not found"
        )

    # Keep only the fields the client actually sent.
    update_data = update.model_dump(exclude_unset=True, mode="json")

    # Reject an empty patch.
    if not update_data:
        raise HTTPException(
            status_code=400,
            detail="PATCH body must contain at least one field to update"
        )

    # Merge extra_fields rather than overwrite, so existing keys survive.
    if "extra_fields" in update_data:
        merged_extra = {**existing.get("extra_fields", {}), **update_data["extra_fields"]}
        update_data["extra_fields"] = merged_extra

    # Apply the update and return the refreshed document.
    await collection.update_one({"asset_id": asset_id}, {"$set": update_data})

    updated_doc = await collection.find_one({"asset_id": asset_id})
    return serialise_doc(updated_doc)
