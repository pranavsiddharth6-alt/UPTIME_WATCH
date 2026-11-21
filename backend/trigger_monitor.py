import http.client
import json
import os
import time
from uuid import uuid4

from worker_trigger_protocol import (
    HOST, PATH, REQUEST_ID_HEADER, SECRET_FORMAT, SIGNATURE_HEADER, TIMESTAMP_HEADER, signature,
)


def main():
    secret = os.getenv("WORKER_TRIGGER_SECRET", "")
    if not SECRET_FORMAT.fullmatch(secret):
        print("::error::Set WORKER_TRIGGER_SECRET to a dedicated 32-byte URL-safe token.")
        return 1

    # HTTPSConnection validates certificates and does not follow redirects.
    connection = http.client.HTTPSConnection(HOST, timeout=100)
    timestamp = str(int(time.time()))
    request_id = uuid4().hex
    try:
        connection.request("POST", PATH, body=b"", headers={
            TIMESTAMP_HEADER: timestamp,
            REQUEST_ID_HEADER: request_id,
            SIGNATURE_HEADER: signature(secret, timestamp, request_id, b""),
        })
        response = connection.getresponse()
        if response.status != 200:
            print(f"::error::Monitoring trigger returned HTTP {response.status}.")
            return 1
        payload = json.loads(response.read(512))
    except (OSError, http.client.HTTPException, ValueError):
        print("::error::Monitoring trigger could not be verified. Check Render before retrying.")
        return 1
    finally:
        connection.close()

    status = payload.get("status") if isinstance(payload, dict) else None
    if status not in ("saved", "no_due_targets", "skipped"):
        print("::error::Monitoring trigger returned an unexpected response.")
        return 1
    print(f"Monitoring dispatch: {status}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
