import os
from datetime import timezone

from pymongo import MongoClient
from pymongo.errors import PyMongoError


def get_mongo_client():
    """Create a lazy client. The caller is responsible for closing it."""
    uri = os.getenv("MONGODB_URI")
    if not uri or not uri.strip():
        raise RuntimeError("MONGODB_URI is not configured.")

    try:
        return MongoClient(uri, connect=False, serverSelectionTimeoutMS=3000)
    except (PyMongoError, ValueError):
        raise RuntimeError("MongoDB client could not be configured.") from None


def get_database(client):
    """Select the configured database using an existing client."""
    return client[os.getenv("MONGODB_DB_NAME", "uptimewatch")]


def utc_timestamp(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def ping_database(*, raise_errors=False):
    """Return whether MongoDB responds, or raise on failure for diagnostics."""
    try:
        with get_mongo_client() as client:
            client.admin.command("ping")
    except (PyMongoError, RuntimeError):
        if raise_errors:
            raise
        return False
    return True
