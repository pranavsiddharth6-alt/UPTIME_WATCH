import os
import re
from urllib.parse import urlsplit

from flask import Flask
from flask_cors import CORS

from .admin_routes import admin_routes
from .routes import routes


def _frontend_origin(development):
    value = os.getenv("UPTIMEWATCH_FRONTEND_ORIGIN", "").strip()
    if not value and development:
        return "http://localhost:5173"
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        port = url.port
    except ValueError:
        return None
    if (
        not re.fullmatch(r"[a-z0-9.-]+", host)
        or url.path not in ("", "/")
        or any(char in value for char in "?#@")
        or (url.scheme != "https" and not (
            development and url.scheme == "http" and host in ("localhost", "127.0.0.1")
        ))
        or port == 0
    ):
        return None
    default_port = 443 if url.scheme == "https" else 80
    return f"{url.scheme}://{host}" + (f":{port}" if port and port != default_port else "")


def create_app():
    app = Flask(__name__)
    development = os.getenv("UPTIMEWATCH_ENV") == "development"
    origin = _frontend_origin(development)
    app.config.update(
        DEBUG=development,
        ADMIN_FRONTEND_ORIGIN=origin,
        ADMIN_COOKIE_SECURE=not development,
    )
    app.register_blueprint(routes)
    app.register_blueprint(admin_routes)

    if origin:
        CORS(
            app, origins=[re.compile(r"\A" + re.escape(origin) + r"\Z")],
            supports_credentials=True, allow_headers=["Content-Type", "X-CSRF-Token"],
            expose_headers=["Retry-After"], always_send=False,
        )

    return app
