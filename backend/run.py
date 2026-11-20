import os
from pathlib import Path

from dotenv import load_dotenv

from app import create_app


if __name__ == "__main__":
    if os.getenv("UPTIMEWATCH_ENV", "development") == "development":
        load_dotenv(Path(__file__).resolve().with_name(".env"), override=False)

app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=app.debug, load_dotenv=False)
