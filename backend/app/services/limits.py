from datetime import timedelta

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


RATE_LIMIT_COLLECTION = "monitoring_rate_limits"
WINDOW_SECONDS = 60
GLOBAL_CHECKS_PER_WINDOW = 30
CLIENT_CHECKS_PER_WINDOW = 6
ONE_TIME_CHECKS_PER_WINDOW = 20
ONE_TIME_CLIENT_CHECKS_PER_WINDOW = 5
REGISTRATIONS_PER_WINDOW = 10
COOLDOWN_SECONDS = 30


class RateLimited(Exception):
    def __init__(self, retry_after):
        self.retry_after = retry_after


def ensure_budget_index(database):
    database[RATE_LIMIT_COLLECTION].create_index("expires_at", expireAfterSeconds=0)


def consume_budget(database, scope, limit, now):
    start = int(now.timestamp())
    window = start // WINDOW_SECONDS
    retry_after = WINDOW_SECONDS - start % WINDOW_SECONDS
    bucket = {"_id": f"{scope}:{window}"}
    update = {
        "$inc": {"count": 1},
        "$setOnInsert": {"expires_at": now + timedelta(seconds=2 * WINDOW_SECONDS)},
    }
    collection = database[RATE_LIMIT_COLLECTION]
    try:
        count = collection.find_one_and_update(
            bucket, update, upsert=True, return_document=ReturnDocument.AFTER
        )
    except DuplicateKeyError:
        # Two processes can try to create a bucket at the same time.
        count = collection.find_one_and_update(
            bucket, update, return_document=ReturnDocument.AFTER
        )
    if count is None or count["count"] > limit:
        raise RateLimited(retry_after)
