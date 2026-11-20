import os
from functools import wraps
from hmac import compare_digest

from flask import Blueprint, current_app, jsonify, make_response, request
from pymongo.errors import PyMongoError

from .request_security import bounded_body, json_object, rate_limit_client
from .services import admin, google_signin, sessions
from .services.limits import RateLimited
from .services.targets import list_targets


admin_routes = Blueprint("admin", __name__, url_prefix="/api/admin")
auth_routes = Blueprint("auth", __name__, url_prefix="/api/auth")


def _cookie_name(name="uptimewatch_admin"):
    return f"__Host-{name}" if current_app.config["ADMIN_COOKIE_SECURE"] else name


def _cookie_options():
    return {
        "path": "/", "secure": current_app.config["ADMIN_COOKIE_SECURE"],
        "httponly": True, "samesite": "Lax",
    }


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
    return compare_digest(supplied.encode(), sessions.csrf_token(token).encode())


def _uncached(result):
    response = make_response(result)
    response.headers["Cache-Control"] = "no-store"
    response.vary.add("Origin")
    return response


@admin_routes.after_request
@auth_routes.after_request
def no_store(response):
    return _uncached(response)


def _unavailable():
    return jsonify(error="Account service temporarily unavailable."), 503


def _session_required():
    return jsonify(error="Sign in required."), 401


def _csrf_error():
    return jsonify(error="Request could not be verified. Reload and try again."), 403


def _rate_limited(error):
    return jsonify(error="Too many sign-in attempts. Try again later."), 429, {
        "Retry-After": str(error.retry_after),
    }


def _session_response(token, profile, *, expires_at=None):
    data = {"user": profile, "csrf_token": sessions.csrf_token(token)}
    if profile["role"] == "admin":
        data["username"] = profile["name"]  # Compatibility for existing admin clients.
    if expires_at is not None:
        data["expires_at"] = expires_at
    return jsonify(data)


def _signed_in(token, profile):
    response = _session_response(token, profile)
    response.set_cookie(_cookie_name(), token, max_age=sessions.SESSION_SECONDS, **_cookie_options())
    response.delete_cookie(_cookie_name("uptimewatch_google"), **_cookie_options())
    return response


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
                account = sessions.profile(token)
            except (PyMongoError, RuntimeError):
                return _uncached(_unavailable())
            if account is None:
                return _uncached(_session_required())
            if account["user"]["role"] != "admin":
                return _uncached((jsonify(error="Management access required."), 403))
            if mutating and not _valid_csrf(token):
                return _uncached(_csrf_error())
        return _uncached(view(*args, **kwargs))
    return protected


@admin_routes.post("/login")
@auth_routes.post("/local")
@bounded_body(8192)
def login():
    if not _origin_allowed(required=True):
        return _csrf_error()

    try:
        payload = json_object()
        username, password = payload.get("username"), payload.get("password")
        if (
            isinstance(username, str) and admin.USERNAME.fullmatch(username)
            and isinstance(password, str) and 1 <= len(password) <= 1024
        ):
            password.encode()
            token = admin.login(username, password, rate_limit_client(), _session_token())
            if token:
                return _signed_in(token, {"role": "admin", "name": username})
    except UnicodeError:
        pass
    except RateLimited as error:
        return _rate_limited(error)
    except (PyMongoError, RuntimeError):
        return _unavailable()
    return jsonify(error="Invalid username or password."), 401


@auth_routes.post("/google/challenge")
@bounded_body(0)
def google_challenge():
    if not _origin_allowed(required=True):
        return _csrf_error()
    client_id = current_app.config["GOOGLE_CLIENT_ID"]
    if not client_id:
        return jsonify(error="Google sign-in is unavailable."), 503
    try:
        token, nonce = google_signin.create_challenge(rate_limit_client())
    except RateLimited as error:
        return _rate_limited(error)
    except (PyMongoError, RuntimeError):
        return _unavailable()
    response = jsonify(client_id=client_id, nonce=nonce)
    response.set_cookie(_cookie_name("uptimewatch_google"), token,
                        max_age=google_signin.CHALLENGE_SECONDS, **_cookie_options())
    return response


@auth_routes.post("/google")
@bounded_body(16 * 1024)
def google_login():
    if not _origin_allowed(required=True):
        return _csrf_error()
    client_id = current_app.config["GOOGLE_CLIENT_ID"]
    if not client_id:
        return jsonify(error="Google sign-in is unavailable."), 503
    try:
        token, profile = google_signin.sign_in(
            json_object().get("credential"), client_id, request.cookies.get(_cookie_name("uptimewatch_google")),
            rate_limit_client(), _session_token(),
        )
        return _signed_in(token, profile)
    except google_signin.InvalidGoogleSignIn:
        return jsonify(error="Google sign-in could not be verified. Please try again."), 401
    except RateLimited as error:
        return _rate_limited(error)
    except (PyMongoError, RuntimeError):
        return _unavailable()


def _read_session(*, admin_only):
    if not _origin_allowed(required=False):
        return _session_required()
    token = _session_token()
    try:
        account = sessions.profile(token)
    except (PyMongoError, RuntimeError):
        return _unavailable()
    if account is None or (admin_only and account["user"]["role"] != "admin"):
        return _session_required()
    return _session_response(token, account["user"], expires_at=None if admin_only else account["expires_at"])


@auth_routes.get("/session")
def session():
    return _read_session(admin_only=False)


@admin_routes.get("/session")
def legacy_session():
    return _read_session(admin_only=True)


def _logout(*, admin_only):
    if not _origin_allowed(required=True):
        return _csrf_error()
    token = _session_token()
    try:
        account = sessions.profile(token)
        if account is None or (admin_only and account["user"]["role"] != "admin"):
            return _session_required()
        if not _valid_csrf(token):
            return _csrf_error()
        sessions.revoke(token)
    except (PyMongoError, RuntimeError):
        return _unavailable()
    response = make_response("", 204)
    response.delete_cookie(_cookie_name(), **_cookie_options())
    response.delete_cookie(_cookie_name("uptimewatch_google"), **_cookie_options())
    return response


@auth_routes.post("/logout")
@bounded_body(0)
def logout():
    return _logout(admin_only=False)


@admin_routes.post("/logout")
@bounded_body(0)
def legacy_logout():
    return _logout(admin_only=True)


@admin_routes.get("/targets")
@admin_required
def targets():
    try:
        return jsonify(list_targets(include_disabled=True))
    except (PyMongoError, RuntimeError):
        return _unavailable()
