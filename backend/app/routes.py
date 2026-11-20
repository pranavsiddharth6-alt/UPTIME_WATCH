from flask import Blueprint, jsonify, request
from pymongo.errors import PyMongoError
from werkzeug.exceptions import RequestEntityTooLarge

from .admin_routes import admin_required
from .services.history import get_history, get_uptime_analytics
from .services.limits import RateLimited
from .services.monitor import (
    ProbeUnavailable,
    SecureTransportUnavailable,
    UnsafeTarget,
    normalize_url,
)
from .services.monitoring import run_one_time_check, run_registered_check
from .services.targets import (
    DuplicateTarget,
    TargetNotFound,
    disable_target,
    list_targets,
    register_target,
    set_target_enabled,
)


routes = Blueprint("routes", __name__)
DATABASE_ERROR = "Monitoring database unavailable"
ONE_TIME_BODY_LIMIT = 16 * 1024


def _submitted_url():
    payload = request.get_json(silent=True)
    return payload.get("url") if isinstance(payload, dict) else None


def _rate_limit_response(error):
    return jsonify(error="Check limit reached. Try again later."), 429, {
        "Retry-After": str(error.retry_after),
    }


@routes.get("/")
def home():
    return "Welcome to the UptimeWatch API!"


@routes.get("/health")
def health():
    return jsonify(status="healthy", service="UptimeWatch API")


@routes.post("/api/check")
def check():
    try:
        result = run_registered_check(_submitted_url(), client_address=request.remote_addr or "unknown")
    except (UnsafeTarget, TargetNotFound):
        return jsonify(error="Website is unsafe, unregistered, or disabled"), 400
    except RateLimited as error:
        return _rate_limit_response(error)
    except SecureTransportUnavailable:
        return jsonify(error="Secure website checker unavailable"), 503
    except (PyMongoError, RuntimeError):
        return jsonify(error=DATABASE_ERROR), 503

    return jsonify(result)


@routes.post("/api/check-once")
def check_once():
    request.max_content_length = ONE_TIME_BODY_LIMIT
    try:
        result = run_one_time_check(_submitted_url(), client_address=request.remote_addr or "unknown")
    except RequestEntityTooLarge:
        return jsonify(error="Website check request is too large."), 413
    except UnsafeTarget:
        return jsonify(
            error="Use a public HTTPS URL on port 443 without credentials, a query, or a fragment. "
                  "Private and internal destinations are blocked."
        ), 400
    except RateLimited as error:
        return _rate_limit_response(error)
    except SecureTransportUnavailable:
        return jsonify(error="Secure website checker unavailable."), 503
    except (PyMongoError, RuntimeError):
        return jsonify(error="Check service temporarily unavailable. Try again later."), 503

    return jsonify(result)


@routes.get("/api/targets")
def targets():
    try:
        return jsonify(list_targets())
    except (PyMongoError, RuntimeError):
        return jsonify(error=DATABASE_ERROR), 503


@routes.post("/api/targets")
@admin_required
def create_target():
    request.max_content_length = ONE_TIME_BODY_LIMIT
    try:
        return jsonify(register_target(_submitted_url())), 201
    except RequestEntityTooLarge:
        return jsonify(error="Target request is too large."), 413
    except (UnsafeTarget, ProbeUnavailable):
        return jsonify(error="Target must be a public HTTPS hostname URL with public DNS"), 400
    except DuplicateTarget:
        return jsonify(error="Target is already registered"), 409
    except RateLimited as error:
        return _rate_limit_response(error)
    except (PyMongoError, RuntimeError):
        return jsonify(error=DATABASE_ERROR), 503


@routes.delete("/api/targets/<target_id>")
@admin_required
def delete_target(target_id):
    try:
        disable_target(target_id)
        return "", 204
    except TargetNotFound:
        return jsonify(error="Target was not found"), 404
    except (PyMongoError, RuntimeError):
        return jsonify(error=DATABASE_ERROR), 503


@routes.patch("/api/targets/<target_id>")
@admin_required
def update_target(target_id):
    request.max_content_length = 1024
    try:
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or not isinstance(payload.get("enabled"), bool):
            return jsonify(error="Enabled must be true or false."), 400
        set_target_enabled(target_id, payload["enabled"])
        return "", 204
    except RequestEntityTooLarge:
        return jsonify(error="Target request is too large."), 413
    except TargetNotFound:
        return jsonify(error="Target was not found"), 404
    except (PyMongoError, RuntimeError):
        return jsonify(error=DATABASE_ERROR), 503


@routes.get("/api/history")
def history():
    url = request.args.get("url")
    if url is not None:
        try:
            url = normalize_url(url)
        except UnsafeTarget:
            return jsonify(error="URL is not allowed"), 400

    try:
        limit = int(request.args.get("limit", "20"))
    except ValueError:
        return jsonify(error="Limit must be an integer from 1 to 100"), 400
    if not 1 <= limit <= 100:
        return jsonify(error="Limit must be an integer from 1 to 100"), 400

    try:
        return jsonify(get_history(url, limit))
    except (PyMongoError, RuntimeError):
        return jsonify(error=DATABASE_ERROR), 503


@routes.get("/api/analytics")
def analytics():
    try:
        return jsonify(get_uptime_analytics(include_latest=request.args.get("include_latest") == "1"))
    except (PyMongoError, RuntimeError):
        return jsonify(error=DATABASE_ERROR), 503
