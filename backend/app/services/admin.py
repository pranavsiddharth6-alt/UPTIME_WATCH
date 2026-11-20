import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from hashlib import sha256
from hmac import compare_digest
from secrets import token_urlsafe
from threading import BoundedSemaphore

from pymongo.errors import DuplicateKeyError
from werkzeug.security import check_password_hash, generate_password_hash

from ..database import get_database, get_mongo_client
from .limits import consume_budget, ensure_budget_index

try:
    from argon2 import PasswordHasher
    from argon2.exceptions import HashingError, InvalidHashError, VerificationError
except ImportError:
    PasswordHasher = None


ACCOUNTS = "admin_accounts"
SESSIONS = "admin_sessions"
SESSION_SECONDS = 4 * 60 * 60
ACCOUNT_ID = "admin"
USERNAME = re.compile(r"[A-Za-z0-9_.@-]{3,64}")
SESSION_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")
SCRYPT = "scrypt:32768:8:3"
hasher = PasswordHasher() if PasswordHasher else None
password_slots = BoundedSemaphore(1)


class AdminAlreadyExists(Exception):
    pass


def hash_password(password):
    if hasher is not None:
        try:
            return hasher.hash(password)
        except HashingError:
            raise RuntimeError("Password hashing unavailable") from None
    return generate_password_hash(password, method=SCRYPT, salt_length=24)


def _verify_password(stored, password):
    if stored.startswith("$argon2id$"):
        if hasher is None:
            raise RuntimeError("Argon2 verification unavailable")
        try:
            return hasher.verify(stored, password)
        except (VerificationError, InvalidHashError):
            return False
    return stored.startswith(f"{SCRYPT}$") and check_password_hash(stored, password)


@lru_cache(maxsize=1)
def _dummy_hash():
    return hash_password(token_urlsafe(32))


def _digest(token):
    return sha256(token.encode()).hexdigest()


def csrf_token(token):
    return _digest(f"uptimewatch-admin-csrf:{token}")


def _session_id(token):
    if isinstance(token, str) and SESSION_TOKEN.fullmatch(token):
        return _digest(token)
    return None


def create_admin(username, password):
    if not USERNAME.fullmatch(username) or not 12 <= len(password) <= 1024:
        raise ValueError("Use a 3–64 character username and a password of 12–1024 characters.")
    document = {"_id": ACCOUNT_ID, "username": username, "password_hash": hash_password(password)}
    with get_mongo_client() as client:
        try:
            get_database(client)[ACCOUNTS].insert_one(document)
        except DuplicateKeyError:
            raise AdminAlreadyExists() from None


def login(username, password, client_address, previous_token):
    with get_mongo_client() as client:
        database = get_database(client)
        ensure_budget_index(database)
        now = datetime.now(timezone.utc)
        consume_budget(database, f"admin:login:{_digest(client_address)}", 5, now)
        consume_budget(database, "admin:logins", 30, now)

        account = database[ACCOUNTS].find_one({"_id": ACCOUNT_ID})
        if not password_slots.acquire(blocking=False):
            raise RateLimited(2)
        try:
            stored = account["password_hash"] if account else _dummy_hash()
            password_matches = _verify_password(stored, password)
        finally:
            password_slots.release()
        if not account or not compare_digest(username.encode(), account["username"].encode()) or not password_matches:
            return None

        sessions = database[SESSIONS]
        sessions.create_index("expires_at", expireAfterSeconds=0)
        previous_id = _session_id(previous_token)
        if previous_id:
            sessions.delete_one({"_id": previous_id})

        token = token_urlsafe(32)
        sessions.insert_one({
            "_id": _digest(token),
            "username": account["username"],
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=SESSION_SECONDS),
        })
        return token


def session_username(token):
    session_id = _session_id(token)
    if session_id is None:
        return None
    with get_mongo_client() as client:
        record = get_database(client)[SESSIONS].find_one({
            "_id": session_id, "expires_at": {"$gt": datetime.now(timezone.utc)},
        }, {"username": 1})
        return record["username"] if record else None


def revoke_session(token):
    session_id = _session_id(token)
    if session_id is None:
        return
    with get_mongo_client() as client:
        get_database(client)[SESSIONS].delete_one({"_id": session_id})
