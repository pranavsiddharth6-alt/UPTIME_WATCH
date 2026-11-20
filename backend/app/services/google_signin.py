import re
from datetime import datetime, timedelta, timezone
from functools import partial
from hmac import compare_digest
from secrets import token_urlsafe

import requests
from google.auth.exceptions import GoogleAuthError, TransportError
from google.auth.transport.requests import Request
from google.oauth2.id_token import verify_oauth2_token
from pymongo.errors import DuplicateKeyError

from ..database import get_database, get_mongo_client
from .limits import consume_budget, ensure_budget_index
from .sessions import create_session, digest, token_digest


CLIENT_ID = re.compile(r"[0-9]+-[A-Za-z0-9_-]+\.apps\.googleusercontent\.com")
CREDENTIAL = re.compile(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+){2}")
CHALLENGES = "google_signin_challenges"
USERS = "google_users"
CHALLENGE_SECONDS = 10 * 60


class InvalidGoogleSignIn(ValueError):
    pass


def _nonce(token):
    return digest(f"uptimewatch-google-nonce:{token}")


def _limit(database, operation, address, now):
    ensure_budget_index(database)
    consume_budget(database, f"google:{operation}:client:{digest(address)}", 20, now)
    consume_budget(database, f"google:{operation}", 60, now)


def create_challenge(address):
    token = token_urlsafe(32)
    now = datetime.now(timezone.utc)
    with get_mongo_client() as client:
        database = get_database(client)
        _limit(database, "challenge", address, now)
        challenges = database[CHALLENGES]
        challenges.create_index("expires_at", expireAfterSeconds=0)
        challenges.insert_one({"_id": digest(token), "expires_at": now + timedelta(seconds=CHALLENGE_SECONDS)})
    return token, _nonce(token)


def verify_credential(credential, client_id):
    try:
        with requests.Session() as http:
            return verify_oauth2_token(credential, partial(Request(session=http), timeout=5), audience=client_id)
    except TransportError:
        raise RuntimeError("Google token verification unavailable") from None
    except (GoogleAuthError, ValueError, TypeError, KeyError, OverflowError):
        raise InvalidGoogleSignIn() from None


def sign_in(credential, client_id, challenge_token, address, previous_token):
    challenge_id = token_digest(challenge_token)
    if not challenge_id or not isinstance(credential, str) or not CREDENTIAL.fullmatch(credential):
        raise InvalidGoogleSignIn()

    with get_mongo_client() as client:
        database = get_database(client)
        now = datetime.now(timezone.utc)
        _limit(database, "login", address, now)
        challenges = database[CHALLENGES]
        if not challenges.find_one({"_id": challenge_id, "expires_at": {"$gt": now}}, {"_id": 1}):
            raise InvalidGoogleSignIn()

        claims = verify_credential(credential, client_id)
        nonce, subject, email = claims.get("nonce"), claims.get("sub"), claims.get("email")
        if (
            not isinstance(nonce, str) or not nonce.isascii() or not compare_digest(nonce, _nonce(challenge_token))
            or not isinstance(subject, str) or not re.fullmatch(r"[!-~]{1,255}", subject)
            or not isinstance(email, str) or not 1 <= len(email) <= 254
            or claims.get("email_verified") is not True
        ):
            raise InvalidGoogleSignIn()
        name = claims.get("name")
        name = name.strip()[:120] if isinstance(name, str) else ""
        profile = {"name": name or email, "email": email, "role": "user"}

        now = datetime.now(timezone.utc)
        if not challenges.find_one_and_delete({"_id": challenge_id, "expires_at": {"$gt": now}}):
            raise InvalidGoogleSignIn()
        users = database[USERS]
        changes = {"$set": {**profile, "email_verified": True}, "$setOnInsert": {"created_at": now}}
        try:
            users.update_one({"_id": subject}, changes, upsert=True)
        except DuplicateKeyError:
            users.update_one({"_id": subject}, changes)
        token = create_session(database, {**profile, "google_sub": subject}, previous_token)
    return token, profile
