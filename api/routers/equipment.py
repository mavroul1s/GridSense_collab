# api/routers/equipment.py
# MongoDB-backed equipment catalog endpoints
#
# Follows Week 5 Slide 83 CRUD pattern exactly:
#   POST   /equipment            → insert_one + model_dump()
#   GET    /equipment/{asset_id}  → find_one + serialise_doc()
#   PATCH  /equipment/{asset_id}  → update_one with $set (partial update)
#
# Week 5 Slide 24: schema flexibility — equipment_type discriminator
# selects the correct Pydantic submodel (Transformer/SmartMeter/
# ProtectionRelay) at the API boundary; MongoDB stores all three
# shapes in one collection without rigid schema enforcement.

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

# Union of the three valid equipment shapes (assignment requirement:
# 3+ different schema shapes in one collection — Week 5 Slide 24).
EquipmentIn = Union[TransformerIn, SmartMeterIn, ProtectionRelayIn]


# ── POST /equipment ───────────────────────────────────────────────
@router.post("", status_code=201)
async def create_equipment(item: EquipmentIn = Body(...)):
    """
    Insert a new equipment record into the catalog.

    Week 5 Slide 83:
        await db.students.insert_one(s.model_dump())
        return {"status": "created"}

    FastAPI validates the incoming JSON against whichever of
    TransformerIn / SmartMeterIn / ProtectionRelayIn matches the
    equipment_type discriminator field. Wrong type or missing
    required field → 422 before MongoDB is touched.

    Duplicate asset_id check: MongoDB does not enforce uniqueness
    on asset_id by default (no unique index created here per
    Week 5 — schema enforcement stays in application code per the
    flexible-schema philosophy). We check explicitly to avoid
    silent duplicate catalog entries.
    """
    collection = equipment_collection()

    existing = await collection.find_one({"asset_id": item.asset_id})
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Equipment with asset_id '{item.asset_id}' already exists"
        )

    # model_dump(mode="json") converts date objects to ISO strings —
    # MongoDB/BSON handles dates natively but this keeps the document
    # consistent with what GET returns (Week 5 Slide 15: BSON adds
    # Date type internally, JSON does not — mode="json" avoids
    # mismatched representations between insert and read).
    doc = item.model_dump(mode="json")

    await collection.insert_one(doc)

    return {
        "status":         "created",
        "asset_id":       item.asset_id,
        "equipment_type": item.equipment_type,
    }


# ── GET /equipment/{asset_id} ─────────────────────────────────────
@router.get("/{asset_id}")
async def get_equipment(asset_id: str):
    """
    Retrieve one equipment record by asset_id.

    Week 5 Slide 83:
        doc = await db.students.find_one({"student_id": student_id})
        if doc:
            doc["_id"] = str(doc["_id"])
        return doc

    serialise_doc() (db/mongo.py) performs the _id → str conversion —
    without it FastAPI raises TypeError: Object of type ObjectId is
    not JSON serializable.

    Returns the raw document dict — not a fixed Pydantic response
    model — because the three equipment shapes have genuinely
    different fields (Week 5 Slide 24: schema flexibility is the
    point, not a workaround).
    """
    collection = equipment_collection()
    doc = await collection.find_one({"asset_id": asset_id})

    if doc is None:
        raise HTTPException(
            status_code=404,
            detail=f"Equipment with asset_id '{asset_id}' not found"
        )

    return serialise_doc(doc)


# ── PATCH /equipment/{asset_id} ───────────────────────────────────
@router.patch("/{asset_id}")
async def update_equipment(asset_id: str, update: EquipmentUpdate):
    """
    Partially update an equipment record.

    Week 5 Slide 59: PATCH updates only the specified fields —
    unlike PUT, which would replace the entire document.

    exclude_unset=True: only fields the client actually included in
    the request body are included in the $set operation. A field
    explicitly omitted from the request is left untouched in MongoDB
    — critical for extra_fields, where a client updating 'status'
    should not wipe out the existing extra_fields dict.

    extra_fields handling: if the client sends extra_fields, those
    keys are MERGED into the existing extra_fields subdocument rather
    than replacing it wholesale — this is the mechanism described in
    Part A A.5 Case 4 for adding new manufacturer telemetry fields
    without disturbing existing ones.
    """
    collection = equipment_collection()

    existing = await collection.find_one({"asset_id": asset_id})
    if existing is None:
        raise HTTPException(
            status_code=404,
            detail=f"Equipment with asset_id '{asset_id}' not found"
        )

    # Only fields explicitly provided in the PATCH body
    update_data = update.model_dump(exclude_unset=True, mode="json")

    if not update_data:
        raise HTTPException(
            status_code=400,
            detail="PATCH body must contain at least one field to update"
        )

    # Special handling: merge extra_fields rather than overwrite.
    # If the client sends {"extra_fields": {"new_key": "val"}}, the
    # existing extra_fields dict is updated with the new key(s)
    # rather than replaced entirely — old telemetry fields survive.
    if "extra_fields" in update_data:
        merged_extra = {**existing.get("extra_fields", {}), **update_data["extra_fields"]}
        update_data["extra_fields"] = merged_extra

    result = await collection.update_one(
        {"asset_id": asset_id},
        {"$set": update_data}
    )

    if result.modified_count == 0:
        # Document matched but nothing changed — still a success,
        # not an error (e.g. PATCH with same values as current state)
        pass

    updated_doc = await collection.find_one({"asset_id": asset_id})
    return serialise_doc(updated_doc)