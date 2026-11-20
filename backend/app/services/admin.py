import re
from datetime import datetime, timezone
from functools import lru_cache
from hmac import compare_digest
from secrets import token_urlsafe
from threading import BoundedSemaphore

from pymongo.errors import DuplicateKeyError
from werkzeug.security import check_password_hash, generate_password_hash

from ..database import get_database, get_mongo_client
from .limits import consume_budget, ensure_budget_index
from .sessions import create_session, digest

try:
    from argon2 import PasswordHasher
    from argon2.exceptions import HashingError, InvalidHashError, VerificationError
except ImportError:
    PasswordHasher = None


ACCOUNTS = "admin_accounts"
ACCOUNT_ID = "admin"
USERNAME = re.compile(r"[A-Za-z0-9_.@-]{3,64}")
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
        consume_budget(database, f"admin:login:{digest(client_address)}", 5, now)
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

        return create_session(database, {"role": "admin", "username": account["username"]}, previous_token)
