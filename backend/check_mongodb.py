import os
import socket
import ssl
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


def classify_failure(error):
    from dns.exception import DNSException
    from pymongo.errors import (
        ConfigurationError,
        ConnectionFailure,
        InvalidURI,
        OperationFailure,
        ServerSelectionTimeoutError,
    )

    fallback = "Unknown error"
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, OperationFailure):
            if error.code == 18 or (error.details or {}).get("codeName") == "AuthenticationFailed":
                return "Authentication failure"
            if error.code == 8000 and "bad auth" in str(error).casefold():
                return "Authentication failure"
        if isinstance(error, (InvalidURI, ValueError)):
            return "Malformed URI"
        if isinstance(error, ssl.SSLError):
            return "TLS failure"
        if isinstance(error, (DNSException, socket.gaierror, ConnectionError, TimeoutError)):
            return "DNS/network failure"

        if isinstance(error, (ConfigurationError, ConnectionFailure)):
            # Driver wrappers can retain lower-level errors only as text.
            details = str(error).casefold()
            if any(hint in details for hint in (
                "ssl handshake failed", "tls handshake failed", "certificate_verify_failed",
                "certificate verify failed",
            )):
                return "TLS failure"
            if any(hint in details for hint in (
                "getaddrinfo failed", "name or service not known", "temporary failure in name resolution",
                "dns query name does not exist", "dns operation timed out", "connection refused",
                "network is unreachable",
            )):
                return "DNS/network failure"
            if isinstance(error, ServerSelectionTimeoutError):
                fallback = "IP access or server selection timeout"
            elif isinstance(error, ConnectionFailure) and fallback == "Unknown error":
                fallback = "DNS/network failure"

        error = error.__cause__ or error.__context__

    return fallback


def main():
    category = "Unknown error"
    environment_conflict = "unknown"
    connected = False

    try:
        # Keep dependency diagnostics out of the command's output.
        with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
            from dotenv import dotenv_values, load_dotenv

            from app.database import ping_database

            env_path = Path(__file__).resolve().with_name(".env")
            file_values = dotenv_values(env_path)
            has_conflict = any(
                file_values.get(key) is not None
                and key in os.environ
                and os.environ[key] != file_values[key]
                for key in ("MONGODB_URI", "MONGODB_DB_NAME")
            )
            environment_conflict = "yes" if has_conflict else "no"

            load_dotenv(env_path, override=False)
            if not os.getenv("MONGODB_URI", "").strip():
                category = "Missing configuration"
            else:
                try:
                    connected = ping_database(raise_errors=True)
                except Exception as error:
                    category = classify_failure(error)
    except Exception:
        pass

    if connected:
        print("MongoDB Atlas connection successful")
    else:
        print(f"MongoDB Atlas connection failed: {category}")

    print(f"Environment conflict: {environment_conflict}")
    return 0 if connected else 1


if __name__ == "__main__":
    raise SystemExit(main())
