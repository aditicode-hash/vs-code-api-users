from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.database import db, redis_client
from enum import Enum
import json
import string
import secrets
from bson import ObjectId

router = APIRouter()

class StatusEnum(str, Enum):
    active = "active"
    inactive = "inactive"

class User(BaseModel):
    id: int
    name: str
    company: str
    status: StatusEnum = StatusEnum.active

# Utility: Convert ObjectId to str in a document
def convert_object_ids(doc):
    return {
        k: str(v) if isinstance(v, ObjectId) else v
        for k, v in doc.items()
    }

# Utility: Generate a random API key
def generate_api_key(length=20):
    chars = string.ascii_letters + string.digits + "!@#$%^&*()_+-="
    return ''.join(secrets.choice(chars) for _ in range(length))

# Create a new user
@router.post("/users")
async def create_user(user: User):
    user_dict = user.dict()

    existing = await db.users.find_one({"id": user.id})
    if existing:
        raise HTTPException(status_code=400, detail="User ID already exists")

    result = await db.users.insert_one(user_dict)
    mongo_id = str(result.inserted_id)
    user_dict["_id"] = mongo_id

    api_key = generate_api_key()

    await db.keys.insert_one({
        "id": mongo_id,
        "keys": [{"key": api_key, "status": "valid"}]
    })

    # ✅ Store only in Redis lists (no hashes)
    user_json = json.dumps(convert_object_ids(user_dict), sort_keys=True)
    await redis_client.lrem("users_list", 0, user_json)
    await redis_client.rpush("users_list", user_json)

    api_doc = {
        "id": mongo_id,
        "keys": [{"key": api_key, "status": "valid"}]
    }
    api_json = json.dumps(api_doc, sort_keys=True)
    await redis_client.lrem("api_keys_list", 0, api_json)
    await redis_client.rpush("api_keys_list", api_json)

    return {
        "message": "User created",
        "user": user_dict,
        "api_key": api_key
    }

# Create a new API key for a user
@router.post("/users/{mongo_id}/create_api_key")
async def create_api_key(mongo_id: str):
    mongo_user = await db.users.find_one({"_id": ObjectId(mongo_id)})
    if not mongo_user:
        raise HTTPException(status_code=404, detail="User not found")

    new_key = generate_api_key()

    key_doc = await db.keys.find_one({"id": mongo_id})
    if not key_doc:
        key_doc = {"id": mongo_id, "keys": [{"key": new_key, "status": "valid"}]}
        await db.keys.insert_one(key_doc)
    else:
        key_doc["keys"].append({"key": new_key, "status": "valid"})
        await db.keys.update_one({"id": mongo_id}, {"$set": {"keys": key_doc["keys"]}})

    key_doc_serialized = convert_object_ids(key_doc)

    # Remove old entry from list and push updated version
    old_entries = await redis_client.lrange("api_keys_list", 0, -1)
    for entry in old_entries:
        entry_dict = json.loads(entry)
        if entry_dict.get("id") == mongo_id:
            await redis_client.lrem("api_keys_list", 0, entry)
            break

    updated_json = json.dumps(key_doc_serialized, sort_keys=True)
    await redis_client.rpush("api_keys_list", updated_json)

    return {"message": "New API key created", "new_key": new_key}

# Remove a specific API key for a user
@router.post("/users/{mongo_id}/remove_api_key")
async def remove_api_key(mongo_id: str, key_to_remove: str):
    key_doc = await db.keys.find_one({"id": mongo_id})
    if not key_doc:
        raise HTTPException(status_code=404, detail="API key document not found")

    filtered_keys = [k for k in key_doc["keys"] if k["key"] != key_to_remove]
    if len(filtered_keys) == len(key_doc["keys"]):
        raise HTTPException(status_code=404, detail="API key not found")

    await db.keys.update_one({"id": mongo_id}, {"$set": {"keys": filtered_keys}})

    updated_doc = {"id": mongo_id, "keys": filtered_keys}
    updated_json = json.dumps(updated_doc, sort_keys=True)

    # Remove old entry from list and push updated version
    old_entries = await redis_client.lrange("api_keys_list", 0, -1)
    for entry in old_entries:
        entry_dict = json.loads(entry)
        if entry_dict.get("id") == mongo_id:
            await redis_client.lrem("api_keys_list", 0, entry)
            break

    await redis_client.rpush("api_keys_list", updated_json)

    return {"message": "API key removed", "removed_key": key_to_remove}

# Get list of API keys (read from Redis list)
@router.get("/users/{mongo_id}/list_api_keys")
async def list_api_keys(mongo_id: str):
    all_entries = await redis_client.lrange("api_keys_list", 0, -1)
    for entry in all_entries:
        entry_dict = json.loads(entry)
        if entry_dict.get("id") == mongo_id:
            return entry_dict
    raise HTTPException(status_code=404, detail="API keys not found in Redis")

# Get user info (read from Redis list)
@router.get("/users/{user_id}")
async def get_user(user_id: int):
    all_entries = await redis_client.lrange("users_list", 0, -1)
    for entry in all_entries:
        entry_dict = json.loads(entry)
        if entry_dict.get("id") == user_id:
            return entry_dict
    raise HTTPException(status_code=404, detail="User not found in Redis")
