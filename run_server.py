"""
Entry point for the Run Tracker server.

Usage:
    python run_server.py                  # default: localhost:8000
    python run_server.py --host 0.0.0.0 --port 8080
    python run_server.py --reload         # dev mode with auto-reload

The server hosts:
  - FastAPI REST API under /runs/*
  - Single-page web app at /
  - Static assets under /static/*
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure the repo root is on the Python path so `run_tracker` is importable
# whether the script is invoked from the repo root or from another directory.
ROOT = Path(__file__).parent.resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Tracker development server")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Bind port (default: 8000)")
    parser.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload on code changes (development mode)",
    )
    parser.add_argument(
        "--db",
        default="runs_db.json",
        help="Path to the JSON database file (default: runs_db.json)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Inject the DB path into the storage layer before the app is imported by uvicorn
    import os
    os.environ.setdefault("RUN_TRACKER_DB", args.db)

    try:
        import uvicorn
    except ImportError:
        print("uvicorn is not installed. Run:  pip install uvicorn fastapi pydantic", file=sys.stderr)
        sys.exit(1)

    print(f"Starting Run Tracker server at http://{args.host}:{args.port}")
    print(f"  API docs: http://{args.host}:{args.port}/docs")
    print(f"  Web app:  http://{args.host}:{args.port}/")
    print(f"  Database: {args.db}")

    uvicorn.run(
        "run_tracker.api:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
