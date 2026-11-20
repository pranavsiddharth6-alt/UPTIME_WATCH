from functools import wraps

from flask import current_app, request
from werkzeug.exceptions import RequestEntityTooLarge


URL_BODY_LIMIT = 16 * 1024


def bounded_body(max_bytes):
    def decorate(view):
        @wraps(view)
        def bounded(*args, **kwargs):
            if request.content_length is not None and request.content_length > max_bytes:
                raise RequestEntityTooLarge()
            # Read one extra byte to detect oversized streams without Content-Length.
            request.max_content_length = max_bytes + 1
            if len(request.get_data()) > max_bytes:
                raise RequestEntityTooLarge()
            return view(*args, **kwargs)
        return bounded
    return decorate


def json_object():
    try:
        payload = request.get_json(silent=True)
    except RecursionError:
        return {}
    return payload if isinstance(payload, dict) else {}


def rate_limit_client():
    if current_app.config["USE_DIRECT_CLIENT_ADDRESS"]:
        return request.remote_addr or "unknown"
    # The app is reachable via more than one proxy path in production.
    return "production-shared"
