from datetime import datetime, timedelta, timezone
from hashlib import sha256

from ..database import get_database, get_mongo_client
from .history import save_check
from .limits import (
    CLIENT_CHECKS_PER_WINDOW,
    COOLDOWN_SECONDS,
    GLOBAL_CHECKS_PER_WINDOW,
    ONE_TIME_CHECKS_PER_WINDOW,
    ONE_TIME_CLIENT_CHECKS_PER_WINDOW,
    RateLimited,
    consume_budget,
    ensure_budget_index,
)
from .monitor import check_website, normalize_url
from .targets import TARGET_COLLECTION, TargetNotFound, due_filter


def run_one_time_check(value, *, client_address):
    url = normalize_url(value)
    client_key = sha256(client_address.encode()).hexdigest()

    with get_mongo_client() as client:
        database = get_database(client)
        ensure_budget_index(database)
        now = datetime.now(timezone.utc)
        consume_budget(database, f"once:client:{client_key}", ONE_TIME_CLIENT_CHECKS_PER_WINDOW, now)
        consume_budget(database, "once:checks", ONE_TIME_CHECKS_PER_WINDOW, now)

    return check_website(url)


def run_registered_check(value, *, client_address=None):
    url = normalize_url(value)
    with get_mongo_client() as client:
        database = get_database(client)
        targets = database[TARGET_COLLECTION]
        target = targets.find_one({"url": url, "enabled": True}, {"_id": 1})
        if target is None:
            raise TargetNotFound()

        now = datetime.now(timezone.utc)
        if client_address is not None:
            client_key = sha256(client_address.encode()).hexdigest()
            consume_budget(database, f"client:{client_key}", CLIENT_CHECKS_PER_WINDOW, now)
        consume_budget(database, "checks", GLOBAL_CHECKS_PER_WINDOW, now)

        now = datetime.now(timezone.utc)
        claim = targets.update_one(
            {"_id": target["_id"], **due_filter(now)},
            {"$set": {"next_check_at": now + timedelta(seconds=COOLDOWN_SECONDS)}},
        )
        if not claim.matched_count:
            raise RateLimited(COOLDOWN_SECONDS)

    result = check_website(url)
    save_check(result)
    return result
