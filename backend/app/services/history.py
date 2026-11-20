from datetime import datetime, timezone

from pymongo import DESCENDING

from ..database import get_database, get_mongo_client, utc_timestamp
from .targets import TARGET_COLLECTION


COLLECTION_NAME = "monitoring_checks"


def save_check(result):
    document = {**result, "checked_at": datetime.now(timezone.utc)}
    with get_mongo_client() as client:
        get_database(client)[COLLECTION_NAME].insert_one(document)


def get_history(url, limit):
    filters = {"url": url} if url is not None else {}
    with get_mongo_client() as client:
        records = get_database(client)[COLLECTION_NAME].find(
            filters,
            sort=[("checked_at", DESCENDING), ("_id", DESCENDING)],
            limit=limit,
        )
        return [serialize_check(record) for record in records]


def serialize_check(record):
    return {
        "id": str(record["_id"]),
        "url": record["url"],
        "status": record["status"],
        "http_status": record.get("http_status"),
        "response_time_ms": record.get("response_time_ms"),
        "checked_at": utc_timestamp(record["checked_at"]),
    }


def get_uptime_analytics(*, include_latest=False):
    grouping = {
        "_id": "$url",
        "total": {"$sum": 1},
        "online": {"$sum": {"$cond": [{"$eq": ["$status", "online"]}, 1, 0]}},
        "average_ms": {"$avg": "$response_time_ms"},
    }
    if include_latest:
        grouping["latest"] = {"$top": {
            "sortBy": {"checked_at": -1, "_id": -1},
            "output": {"status": "$status", "checked_at": "$checked_at"},
        }}
    pipeline = [{"$group": grouping}]
    with get_mongo_client() as client:
        database = get_database(client)
        groups = {
            group["_id"]: group
            for group in database[COLLECTION_NAME].aggregate(pipeline)
        }
        urls = set(groups)
        for target in database[TARGET_COLLECTION].find({"enabled": True}, {"url": 1, "_id": 0}):
            urls.add(target["url"])

    websites = []
    for url in sorted(urls):
        group = groups.get(url, {})
        total = group.get("total", 0)
        online = group.get("online", 0)
        average_ms = group.get("average_ms")
        summary = {
            "url": url,
            "total_checks": total,
            "online_checks": online,
            "offline_checks": total - online,
            "uptime_percent": round(online / total * 100, 2) if total else None,
            "average_response_time_ms": round(average_ms, 2) if average_ms is not None else None,
        }
        if include_latest:
            latest = group.get("latest")
            summary["latest_check"] = {
                "status": latest["status"], "checked_at": utc_timestamp(latest["checked_at"]),
            } if latest else None
        websites.append(summary)
    return websites
