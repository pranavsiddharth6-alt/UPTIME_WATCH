from flask import Blueprint, jsonify, request
from pymongo.errors import PyMongoError

from .admin_routes import admin_required
from .request_security import URL_BODY_LIMIT, bounded_body, json_object, rate_limit_client
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
@bounded_body(URL_BODY_LIMIT)
def check():
    try:
        result = run_registered_check(json_object().get("url"), client_address=rate_limit_client())
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
@bounded_body(URL_BODY_LIMIT)
def check_once():
    try:
        result = run_one_time_check(json_object().get("url"), client_address=rate_limit_client())
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
@bounded_body(URL_BODY_LIMIT)
@admin_required
def create_target():
    try:
        return jsonify(register_target(json_object().get("url"))), 201
    except (UnsafeTarget, ProbeUnavailable):
        return jsonify(error="Target must be a public HTTPS hostname URL with public DNS"), 400
    except DuplicateTarget:
        return jsonify(error="Target is already registered"), 409
    except RateLimited as error:
        return _rate_limit_response(error)
    except (PyMongoError, RuntimeError):
        return jsonify(error=DATABASE_ERROR), 503


@routes.delete("/api/targets/<target_id>")
@bounded_body(0)
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
@bounded_body(1024)
@admin_required
def update_target(target_id):
    try:
        enabled = json_object().get("enabled")
        if not isinstance(enabled, bool):
            return jsonify(error="Enabled must be true or false."), 400
        set_target_enabled(target_id, enabled)
        return "", 204
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
