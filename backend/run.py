"""Start VYRA:  python run.py   (run this inside the backend folder, with the virtual environment active)."""
import os
import sys

import uvicorn

from app.config import ConfigError, Settings


def main() -> None:
    try:
        Settings.from_env()  # fail early with a readable message if .env is wrong
    except ConfigError as exc:
        sys.exit(f"\nConfiguration problem: {exc}\n")
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    print(f"\n  VYRA is starting on http://{host}:{port}\n  API docs: http://{host}:{port}/api/docs\n")
    uvicorn.run("app.main:app_from_env", factory=True, host=host, port=port, reload=False, log_level="warning")


if __name__ == "__main__":
    main()
