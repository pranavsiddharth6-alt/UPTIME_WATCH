import hmac
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

from flask import Blueprint, jsonify, request

from worker_trigger_protocol import (
    BUSY, NO_DUE, PATH, REPLAY, REQUEST_ID, REQUEST_ID_HEADER, SAVED, SECRET_FORMAT,
    SIGNATURE_HEADER, SKIPPED, SUBPROCESS_TIMEOUT, TIMESTAMP_HEADER, TIMESTAMP_WINDOW, signature,
)


worker_routes = Blueprint("worker", __name__)
BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _response(status, code):
    return jsonify(status=status), code, {"Cache-Control": "no-store"}


def _worker_secret():
    secret = os.getenv("WORKER_TRIGGER_SECRET", "")
    if not SECRET_FORMAT.fullmatch(secret):
        return None
    try:
        mongo = urlsplit(os.getenv("MONGODB_URI", ""))
    except ValueError:
        return None
    reused = (
        os.getenv("ADMIN_API_TOKEN"), os.getenv("GOOGLE_CLIENT_ID"), os.getenv("GOOGLE_CLIENT_SECRET"),
        unquote(mongo.username or ""), unquote(mongo.password or ""),
    )
    return None if secret in reused else secret


@worker_routes.post(PATH)
def dispatch():
    if request.content_length or "Transfer-Encoding" in request.headers:
        return _response("body_not_allowed", 413)
    if request.headers.get("Origin") is not None or request.headers.get("Sec-Fetch-Site") is not None:
        return _response("unauthorized", 401)

    secret = _worker_secret()
    if not secret:
        return _response("unavailable", 503)

    timestamp = request.headers.get(TIMESTAMP_HEADER, "")
    request_id = request.headers.get(REQUEST_ID_HEADER, "")
    supplied = request.headers.get(SIGNATURE_HEADER, "")
    if not (
        re.fullmatch(r"[0-9]{10}", timestamp) and REQUEST_ID.fullmatch(request_id)
        and re.fullmatch(r"[0-9a-f]{64}", supplied)
    ):
        return _response("unauthorized", 401)
    if abs(int(time.time()) - int(timestamp)) > TIMESTAMP_WINDOW or not hmac.compare_digest(
        supplied, signature(secret, timestamp, request_id, b""),
    ):
        return _response("unauthorized", 401)
    request.max_content_length = 1
    if request.get_data():
        return _response("body_not_allowed", 413)

    try:
        worker = subprocess.run(
            [sys.executable, "-B", str(BACKEND_ROOT / "triggered_monitor.py"), request_id],
            cwd=BACKEND_ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=SUBPROCESS_TIMEOUT, check=False,
        )
    except subprocess.TimeoutExpired:
        return _response("deadline_exceeded", 504)
    except OSError:
        return _response("unavailable", 503)

    if worker.returncode == -getattr(signal, "SIGALRM", 0) and worker.returncode < 0:
        return _response("deadline_exceeded", 504)
    status, code = {
        SAVED: ("saved", 200), NO_DUE: ("no_due_targets", 200), SKIPPED: ("skipped", 200),
        REPLAY: ("replay", 409), BUSY: ("busy", 409),
    }.get(worker.returncode, ("monitoring_failed", 503))
    return _response(status, code)
