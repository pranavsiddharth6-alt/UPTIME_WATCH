import sys
import warnings
from getpass import GetPassWarning, getpass
from pathlib import Path

from dotenv import load_dotenv
from pymongo.errors import PyMongoError

from app.services.admin import AdminAlreadyExists, create_admin


def main():
    if not sys.stdin.isatty():
        print("Run admin setup in an interactive terminal.")
        return 1
    try:
        load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)
        username = input("Admin username: ").strip()
        with warnings.catch_warnings():
            warnings.simplefilter("error", GetPassWarning)
            password = getpass("Admin password: ")
            if password != getpass("Confirm password: "):
                print("Passwords do not match.")
                return 1
        create_admin(username, password)
    except AdminAlreadyExists:
        print("The administrator account already exists.")
        return 1
    except ValueError:
        print("Use a 3–64 character username and a password of 12–1024 characters.")
        return 1
    except (PyMongoError, RuntimeError, OSError, EOFError, KeyboardInterrupt, GetPassWarning):
        print("Admin setup could not complete.")
        return 1
    print("Administrator account created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
