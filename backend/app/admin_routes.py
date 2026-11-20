import os
from functools import wraps
from hmac import compare_digest

from flask import Blueprint, current_app, jsonify, make_response, request
from pymongo.errors import PyMongoError
from werkzeug.exceptions import RequestEntityTooLarge

from .services import admin
from .services.limits import RateLimited
from .services.targets import list_targets


admin_routes = Blueprint("admin", __name__, url_prefix="/api/admin")


def _cookie_name():
    return "__Host-uptimewatch_admin" if current_app.config["ADMIN_COOKIE_SECURE"] else "uptimewatch_admin"


def _session_token():
    return request.cookies.get(_cookie_name())


def _origin_allowed(*, required):
    configured = current_app.config["ADMIN_FRONTEND_ORIGIN"]
    origin = request.headers.get("Origin")
    return (
        bool(configured)
        and request.headers.get("Sec-Fetch-Site") != "cross-site"
        and (origin == configured or (origin is None and not required))
    )


def _valid_csrf(token):
    supplied = request.headers.get("X-CSRF-Token", "")
    return compare_digest(supplied.encode(), admin.csrf_token(token).encode())


def _uncached(result):
    response = make_response(result)
    response.headers["Cache-Control"] = "no-store"
    response.vary.add("Origin")
    return response


@admin_routes.after_request
def no_store(response):
    return _uncached(response)


def _unavailable():
    return jsonify(error="Admin service temporarily unavailable."), 503


def _session_required():
    return jsonify(error="Admin session required."), 401


def _csrf_error():
    return jsonify(error="Admin request could not be verified. Reload and try again."), 403


def admin_required(view):
    @wraps(view)
    def protected(*args, **kwargs):
        authorization = request.headers.get("Authorization")
        if authorization is not None:
            token = os.getenv("ADMIN_API_TOKEN", "")
            scheme, _, supplied = authorization.partition(" ")
            if (
                request.headers.get("Origin") is not None
                or request.headers.get("Sec-Fetch-Site") is not None
                or not token.strip()
                or scheme.lower() != "bearer"
                or not compare_digest(token.encode(), supplied.encode())
            ):
                return _uncached((jsonify(error="Admin authorization required."), 401))
        else:
            mutating = request.method not in ("GET", "HEAD")
            if not _origin_allowed(required=mutating):
                return _uncached(_session_required())
            token = _session_token()
            try:
                if admin.session_username(token) is None:
                    return _uncached(_session_required())
            except (PyMongoError, RuntimeError):
                return _uncached(_unavailable())
            if mutating and not _valid_csrf(token):
                return _uncached(_csrf_error())
        return _uncached(view(*args, **kwargs))
    return protected


@admin_routes.post("/login")
def login():
    if not _origin_allowed(required=True):
        return _csrf_error()

    request.max_content_length = 8192
    try:
        payload = request.get_json(silent=True)
        if isinstance(payload, dict):
            username, password = payload.get("username"), payload.get("password")
            if (
                isinstance(username, str) and admin.USERNAME.fullmatch(username)
                and isinstance(password, str) and 1 <= len(password) <= 1024
            ):
                password.encode()
                token = admin.login(username, password, request.remote_addr or "unknown", _session_token())
                if token:
                    response = jsonify(username=username, csrf_token=admin.csrf_token(token))
                    response.set_cookie(
                        _cookie_name(), token, max_age=admin.SESSION_SECONDS, path="/",
                        secure=current_app.config["ADMIN_COOKIE_SECURE"], httponly=True, samesite="Lax",
                    )
                    return response
    except (UnicodeError, RequestEntityTooLarge):
        pass
    except RateLimited as error:
        return jsonify(error="Too many login attempts. Try again later."), 429, {
            "Retry-After": str(error.retry_after),
        }
    except (PyMongoError, RuntimeError):
        return _unavailable()
    return jsonify(error="Invalid username or password."), 401


@admin_routes.get("/session")
def session():
    if not _origin_allowed(required=False):
        return _session_required()
    token = _session_token()
    try:
        username = admin.session_username(token)
    except (PyMongoError, RuntimeError):
        return _unavailable()
    if username is None:
        return _session_required()
    return jsonify(username=username, csrf_token=admin.csrf_token(token))


@admin_routes.post("/logout")
def logout():
    if not _origin_allowed(required=True):
        return _csrf_error()
    token = _session_token()
    try:
        if admin.session_username(token) is None:
            return _session_required()
        if not _valid_csrf(token):
            return _csrf_error()
        admin.revoke_session(token)
    except (PyMongoError, RuntimeError):
        return _unavailable()
    response = make_response("", 204)
    response.delete_cookie(
        _cookie_name(), path="/", secure=current_app.config["ADMIN_COOKIE_SECURE"],
        httponly=True, samesite="Lax",
    )
    return response


@admin_routes.get("/targets")
@admin_required
def targets():
    try:
        return jsonify(list_targets(include_disabled=True))
    except (PyMongoError, RuntimeError):
        return _unavailable()
