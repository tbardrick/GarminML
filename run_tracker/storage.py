"""
JSON file-based storage for runs.

All runs are persisted to a single `runs_db.json` file in the working directory.
This is intentionally simple — a production system would use PostgreSQL with
PostGIS for spatial queries, and object storage (S3) for raw FIT files.

Thread-safety note: writes are protected by a threading.Lock so concurrent
API requests (via uvicorn's threadpool) don't corrupt the file.  A true
multi-process deployment would need file locking (fcntl / portalocker) or
a proper database.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Dict, List, Optional

from .models import Run

_DEFAULT_DB_PATH = Path("runs_db.json")


class RunStore:
    """Load-and-save JSON store for Run objects."""

    def __init__(self, db_path: Path | str = _DEFAULT_DB_PATH) -> None:
        self._path = Path(db_path)
        self._lock = threading.Lock()
        self._cache: Dict[str, Run] = {}
        self._load()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Read the JSON file into the in-memory cache."""
        if not self._path.exists():
            self._cache = {}
            return
        try:
            raw = self._path.read_text(encoding="utf-8")
            data = json.loads(raw)
            self._cache = {run_id: Run.model_validate(run_data) for run_id, run_data in data.items()}
        except (json.JSONDecodeError, ValueError):
            # Corrupted file — start fresh rather than crashing
            self._cache = {}

    def _save(self) -> None:
        """Flush in-memory cache to the JSON file (called while lock is held)."""
        data = {run_id: run.model_dump(mode="json") for run_id, run in self._cache.items()}
        # Write to a tmp file then rename for atomicity on POSIX systems
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        tmp.replace(self._path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def save_run(self, run: Run) -> None:
        """Insert or update a run in the store."""
        with self._lock:
            self._cache[run.run_id] = run
            self._save()

    def get_run(self, run_id: str) -> Optional[Run]:
        """Return a Run by ID, or None if not found."""
        with self._lock:
            return self._cache.get(run_id)

    def list_runs(self) -> List[Run]:
        """Return all runs, newest first."""
        with self._lock:
            runs = list(self._cache.values())
        runs.sort(key=lambda r: r.started_at, reverse=True)
        return runs

    def delete_run(self, run_id: str) -> bool:
        """Delete a run. Returns True if it existed."""
        with self._lock:
            if run_id not in self._cache:
                return False
            del self._cache[run_id]
            self._save()
            return True

    def run_exists(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._cache


# Module-level singleton used by the API layer
_store: Optional[RunStore] = None


def get_store(db_path: Path | str = _DEFAULT_DB_PATH) -> RunStore:
    """Return (or create) the module-level singleton store."""
    global _store
    if _store is None:
        _store = RunStore(db_path)
    return _store
