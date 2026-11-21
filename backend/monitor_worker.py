import os
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from pymongo.errors import PyMongoError

from app.services.limits import RateLimited
from app.services.monitor import SecureTransportUnavailable, UnsafeTarget, normalize_url
from app.services.monitoring import run_registered_check
from app.services.targets import TargetNotFound, list_due_urls


MAX_TARGETS_PER_RUN = 10
SKIPPED_TARGET = "Skipped (rate-limited or disabled)"


def check_and_save(url):
    try:
        result = run_registered_check(url)
    except (RateLimited, TargetNotFound):
        return True, SKIPPED_TARGET
    except UnsafeTarget:
        return False, "Website destination blocked"
    except SecureTransportUnavailable:
        return False, "Secure website checker unavailable"
    except (PyMongoError, RuntimeError):
        return False, "Monitoring database unavailable"
    except Exception:
        return False, "Website check failed"

    status = "online" if result["status"] == "online" else "offline"
    return True, f"{status} (saved)"


def main():
    summaries = []
    all_ok = True
    try:
        # Show only the summaries below, even if a dependency emits diagnostics.
        with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink):
            load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)
            if not os.getenv("MONGODB_URI", "").strip():
                raise RuntimeError("MONGODB_URI is required.")
            for url in list_due_urls(MAX_TARGETS_PER_RUN)[:MAX_TARGETS_PER_RUN]:
                ok, message = check_and_save(url)
                try:
                    hostname = urlsplit(normalize_url(url)).hostname
                except UnsafeTarget:
                    hostname = "Target"
                summaries.append(f"{hostname}: {message}")
                all_ok = all_ok and ok
    except (PyMongoError, RuntimeError):
        print("Monitoring database unavailable")
        return 1
    except Exception:
        print("Monitoring worker could not start")
        return 1

    if not summaries:
        print("No monitoring targets are due")
    for summary in summaries:
        print(summary)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
