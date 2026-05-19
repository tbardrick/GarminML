"""
FastAPI application for the run tracker.

Routes:
  POST   /runs/start             – create a new run
  POST   /runs/{run_id}/points   – append GPS points
  POST   /runs/{run_id}/finish   – finalise run & compute analytics
  GET    /runs/{run_id}          – fetch a single run (full)
  GET    /runs                   – list all runs (summaries)
  DELETE /runs/{run_id}          – delete a run
  GET    /                       – serve the SPA
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .analytics import compute_analytics
from .models import GpsPoint, Run, RunSummary
from .storage import RunStore, get_store

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Run Tracker API",
    description="A Strava-lite GPS run tracker prototype",
    version="0.1.0",
)

STATIC_DIR = Path(__file__).parent.parent / "static"


# ---------------------------------------------------------------------------
# Request / Response schemas (separate from domain models for flexibility)
# ---------------------------------------------------------------------------


class StartRunRequest(BaseModel):
    name: Optional[str] = Field(None, description="Optional run name / title")
    user_id: Optional[str] = Field(None)


class StartRunResponse(BaseModel):
    run_id: str
    started_at: datetime
    message: str


class GpsPointPayload(BaseModel):
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    alt: Optional[float] = Field(None)
    timestamp: datetime
    accuracy: Optional[float] = Field(None, ge=0)


class AddPointsRequest(BaseModel):
    points: List[GpsPointPayload] = Field(..., min_length=1)


class AddPointsResponse(BaseModel):
    run_id: str
    points_added: int
    total_points: int


class FinishRunResponse(BaseModel):
    run_id: str
    finished_at: datetime
    summary: RunSummary


class DeleteResponse(BaseModel):
    deleted: bool
    run_id: str


# ---------------------------------------------------------------------------
# Dependency helpers
# ---------------------------------------------------------------------------


def _store() -> RunStore:
    return get_store()


def _get_run_or_404(run_id: str) -> Run:
    run = _store().get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return run


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def serve_spa():
    """Serve the single-page application."""
    index = STATIC_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="Frontend not found")
    return FileResponse(str(index))


@app.post("/runs/start", response_model=StartRunResponse, status_code=201)
async def start_run(body: StartRunRequest):
    """
    Create a new run session and return its ID.

    The client should immediately begin sending GPS points.
    """
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    run = Run(
        run_id=run_id,
        user_id=body.user_id,
        name=body.name,
        started_at=now,
        status="active",
    )
    _store().save_run(run)
    return StartRunResponse(run_id=run_id, started_at=now, message="Run started. Begin sending GPS points.")


@app.post("/runs/{run_id}/points", response_model=AddPointsResponse)
async def add_points(run_id: str, body: AddPointsRequest):
    """
    Append a batch of GPS points to an active run.

    Points may arrive in any order; the analytics engine sorts them.
    The client should batch points every few seconds rather than
    sending one HTTP request per GPS sample to save battery / bandwidth.
    """
    run = _get_run_or_404(run_id)
    if run.status != "active":
        raise HTTPException(status_code=409, detail=f"Run is '{run.status}', cannot add points")

    new_points = [
        GpsPoint(
            lat=p.lat,
            lon=p.lon,
            alt=p.alt,
            timestamp=p.timestamp,
            accuracy=p.accuracy,
        )
        for p in body.points
    ]
    run.points.extend(new_points)
    _store().save_run(run)

    return AddPointsResponse(
        run_id=run_id,
        points_added=len(new_points),
        total_points=len(run.points),
    )


@app.post("/runs/{run_id}/finish", response_model=FinishRunResponse)
async def finish_run(run_id: str):
    """
    Finalise a run: compute full analytics and mark it finished.

    After this call the run is immutable (no more points can be added).
    Re-calling finish on an already-finished run is idempotent.
    """
    run = _get_run_or_404(run_id)
    if run.status == "finished":
        return FinishRunResponse(
            run_id=run_id,
            finished_at=run.finished_at,
            summary=RunSummary.from_run(run),
        )

    now = datetime.now(timezone.utc)
    run.finished_at = now
    run.status = "finished"
    run.analytics = compute_analytics(run.points)
    _store().save_run(run)

    return FinishRunResponse(
        run_id=run_id,
        finished_at=now,
        summary=RunSummary.from_run(run),
    )


@app.get("/runs", response_model=List[RunSummary])
async def list_runs():
    """Return all runs as lightweight summaries, newest first."""
    runs = _store().list_runs()
    return [RunSummary.from_run(r) for r in runs]


@app.get("/runs/{run_id}", response_model=Run)
async def get_run(run_id: str):
    """Return a single run with all GPS points and full analytics."""
    return _get_run_or_404(run_id)


@app.delete("/runs/{run_id}", response_model=DeleteResponse)
async def delete_run(run_id: str):
    """Permanently delete a run and all its data."""
    deleted = _store().delete_run(run_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return DeleteResponse(deleted=True, run_id=run_id)


# ---------------------------------------------------------------------------
# Serve static files (JS, CSS assets if any) — after API routes
# ---------------------------------------------------------------------------

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
