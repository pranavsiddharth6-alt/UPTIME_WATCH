import signal
import sys

from worker_trigger_protocol import (
    BUSY, FAILED, NO_DUE, REPLAY, REQUEST_ID, SAVED, SKIPPED, TIMESTAMP_WINDOW, WORKER_ALARM,
)


def dispatch(request_id):
    from pymongo import ReturnDocument, timeout
    from pymongo.errors import DuplicateKeyError, PyMongoError
    from pymongo.write_concern import WriteConcern

    from app.database import get_database, get_mongo_client
    from monitor_worker import SKIPPED_TARGET, check_and_save, list_due_urls

    # Reserve time after the operation budget for release. The OS alarm bounds both phases.
    with get_mongo_client() as client:
        records = get_database(client).get_collection("monitoring_dispatch", write_concern=WriteConcern(w="majority"))
        owned = False
        try:
            with timeout(14):
                records.create_index("expires_at", expireAfterSeconds=0)
                try:
                    replay_ttl_ms = (2 * TIMESTAMP_WINDOW + 60) * 1000
                    claim = records.update_one(
                        {"_id": f"request:{request_id}"}, [{"$set": {
                            "expires_at": {"$ifNull": ["$expires_at", {"$add": ["$$NOW", replay_ttl_ms]}]},
                        }}], upsert=True,
                    )
                except DuplicateKeyError:
                    return REPLAY
                if claim.upserted_id is None:
                    return REPLAY
                available = {"$lte": [{"$ifNull": ["$expires_at", "$$NOW"]}, "$$NOW"]}
                try:
                    lease = records.find_one_and_update(
                        {"_id": "dispatch"}, [{"$set": {
                            "owner": {"$cond": [available, request_id, "$owner"]},
                            "expires_at": {"$cond": [available, {"$add": ["$$NOW", 60_000]}, "$expires_at"]},
                        }}], upsert=True, return_document=ReturnDocument.AFTER,
                    )
                except DuplicateKeyError:
                    return BUSY
                if not lease or lease["owner"] != request_id:
                    return BUSY
                owned = True
                targets = list_due_urls(1)
                if not targets:
                    return NO_DUE
                ok, result = check_and_save(targets[0])
                if not ok:
                    return FAILED
                return SKIPPED if result == SKIPPED_TARGET else SAVED
        finally:
            if owned:
                try:
                    with timeout(1):
                        records.delete_one({"_id": "dispatch", "owner": request_id})
                except PyMongoError:
                    pass  # Let the lease expire if Atlas cannot confirm release.


def main():
    if len(sys.argv) != 2 or not REQUEST_ID.fullmatch(sys.argv[1]) or not hasattr(signal, "SIGALRM"):
        return FAILED
    # Default SIGALRM termination also applies if the supervising HTTP process exits first.
    signal.signal(signal.SIGALRM, signal.SIG_DFL)
    signal.alarm(WORKER_ALARM)
    try:
        return dispatch(sys.argv[1])
    except Exception:
        return FAILED


if __name__ == "__main__":
    raise SystemExit(main())
