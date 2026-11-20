import re
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import token_urlsafe

from ..database import get_database, get_mongo_client, utc_timestamp


COLLECTION = "admin_sessions"  # Preserve existing sessions and their TTL index.
SESSION_SECONDS = 4 * 60 * 60
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")


def digest(value):
    return sha256(value.encode()).hexdigest()


def token_digest(token):
    return digest(token) if isinstance(token, str) and TOKEN.fullmatch(token) else None


def csrf_token(token):
    return digest(f"uptimewatch-admin-csrf:{token}")  # Stable for existing admin cookies.


def create_session(database, identity, previous_token):
    sessions = database[COLLECTION]
    sessions.create_index("expires_at", expireAfterSeconds=0)
    token = token_urlsafe(32)
    sessions.insert_one({
        **identity,
        "_id": digest(token),
        "expires_at": datetime.now(timezone.utc) + timedelta(seconds=SESSION_SECONDS),
    })
    previous_id = token_digest(previous_token)
    if previous_id:
        sessions.delete_one({"_id": previous_id})
    return token


def profile(token):
    session_id = token_digest(token)
    if session_id is None:
        return None
    with get_mongo_client() as client:
        record = get_database(client)[COLLECTION].find_one({
            "_id": session_id, "expires_at": {"$gt": datetime.now(timezone.utc)},
        }, {"role": 1, "username": 1, "google_sub": 1, "name": 1, "email": 1, "expires_at": 1})
    if not record:
        return None
    if record.get("role", "admin") == "admin" and "username" in record and "google_sub" not in record:
        user = {"role": "admin", "name": record["username"]}
    elif record.get("role") == "user" and "google_sub" in record:
        user = {"role": "user", "name": record["name"], "email": record["email"]}
    else:
        return None
    return {"user": user, "expires_at": utc_timestamp(record["expires_at"])}


def revoke(token):
    session_id = token_digest(token)
    if session_id:
        with get_mongo_client() as client:
            get_database(client)[COLLECTION].delete_one({"_id": session_id})
