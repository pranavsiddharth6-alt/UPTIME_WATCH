import hmac
import re


HOST = "uptime-watch-q67r.onrender.com"
PATH = "/internal/monitor/run"
TIMESTAMP_HEADER = "X-UptimeWatch-Timestamp"
REQUEST_ID_HEADER = "X-UptimeWatch-Request-Id"
SIGNATURE_HEADER = "X-UptimeWatch-Signature"
TIMESTAMP_WINDOW = 120
REQUEST_ID = re.compile(r"[0-9a-f]{32}")
SECRET_FORMAT = re.compile(r"[A-Za-z0-9_-]{43}")
SUBPROCESS_TIMEOUT = 18
WORKER_ALARM = 17
SAVED, NO_DUE, SKIPPED, REPLAY, BUSY, FAILED = (0, 10, 11, 12, 13, 14)


def signature(secret, timestamp, request_id, body):
    prefix = f"v1\nPOST\n{PATH}\n{timestamp}\n{request_id}\n".encode("ascii")
    return hmac.digest(secret.encode("ascii"), prefix + body, "sha256").hex()
