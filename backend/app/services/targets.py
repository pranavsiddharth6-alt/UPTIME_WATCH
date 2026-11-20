from datetime import datetime, timezone

from bson import ObjectId
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from ..database import get_database, get_mongo_client, utc_timestamp
from .limits import REGISTRATIONS_PER_WINDOW, consume_budget, ensure_budget_index
from .monitor import normalize_url, validate_public_dns


TARGET_COLLECTION = "monitoring_targets"


class DuplicateTarget(Exception):
    pass


class TargetNotFound(Exception):
    pass


def list_targets(*, include_disabled=False):
    with get_mongo_client() as client:
        collection = get_database(client)[TARGET_COLLECTION]
        filters = {} if include_disabled else {"enabled": True}
        targets = collection.find(filters, sort=[("url", ASCENDING)])
        return [_serialize_target(target) for target in targets]


def register_target(value):
    url = normalize_url(value)
    with get_mongo_client() as client:
        database = get_database(client)
        ensure_budget_index(database)
        consume_budget(database, "registration", REGISTRATIONS_PER_WINDOW, datetime.now(timezone.utc))

        collection = database[TARGET_COLLECTION]
        collection.create_index("url", unique=True)
        if collection.find_one({"url": url}, {"_id": 1}) is not None:
            raise DuplicateTarget()
        validate_public_dns(url)

        now = datetime.now(timezone.utc)
        target = {"url": url, "enabled": True, "created_at": now, "next_check_at": now}
        try:
            target["_id"] = collection.insert_one(target).inserted_id
        except DuplicateKeyError:
            raise DuplicateTarget() from None
        return _serialize_target(target)


def disable_target(target_id):
    set_target_enabled(target_id, False)


def set_target_enabled(target_id, enabled):
    if not ObjectId.is_valid(target_id):
        raise TargetNotFound()
    with get_mongo_client() as client:
        result = get_database(client)[TARGET_COLLECTION].update_one(
            {"_id": ObjectId(target_id)}, {"$set": {"enabled": enabled}}
        )
        if not result.matched_count:
            raise TargetNotFound()


def due_filter(now):
    return {
        "enabled": True,
        "$or": [
            {"next_check_at": {"$lte": now}},
            {"next_check_at": {"$exists": False}},
        ],
    }


def list_due_urls(limit):
    with get_mongo_client() as client:
        collection = get_database(client)[TARGET_COLLECTION]
        targets = collection.find(
            due_filter(datetime.now(timezone.utc)),
            {"url": 1, "_id": 0},
            sort=[("next_check_at", ASCENDING), ("_id", ASCENDING)],
            limit=limit,
        )
        return [target["url"] for target in targets]


def _serialize_target(target):
    return {
        "id": str(target["_id"]),
        "url": target["url"],
        "enabled": target["enabled"],
        "created_at": utc_timestamp(target["created_at"]),
    }
