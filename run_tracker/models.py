from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class GpsPoint(BaseModel):
    lat: float = Field(..., description="Latitude in decimal degrees")
    lon: float = Field(..., description="Longitude in decimal degrees")
    alt: Optional[float] = Field(None, description="Altitude in metres above sea level")
    timestamp: datetime = Field(..., description="UTC timestamp of the reading")
    accuracy: Optional[float] = Field(None, description="Horizontal accuracy in metres (lower is better)")


class Split(BaseModel):
    """Per-kilometre split information."""
    km: int = Field(..., description="Split number (1 = first km)")
    distance_m: float = Field(..., description="Actual distance covered in this split (metres)")
    elapsed_seconds: float = Field(..., description="Wall-clock seconds for this split")
    moving_seconds: float = Field(..., description="Moving seconds (pauses excluded)")
    pace_per_km: float = Field(..., description="Moving pace in seconds per km")
    avg_alt_m: Optional[float] = Field(None, description="Average altitude for this split")


class RunAnalytics(BaseModel):
    """Computed analytics for a finished run."""
    total_distance_m: float = Field(0.0, description="Total distance in metres")
    elapsed_seconds: float = Field(0.0, description="Wall-clock duration in seconds")
    moving_seconds: float = Field(0.0, description="Time spent actually moving, in seconds")
    avg_pace_sec_per_km: Optional[float] = Field(None, description="Average moving pace (s/km)")
    elevation_gain_m: float = Field(0.0, description="Cumulative positive elevation gain (m)")
    elevation_loss_m: float = Field(0.0, description="Cumulative negative elevation loss (m)")
    max_alt_m: Optional[float] = Field(None)
    min_alt_m: Optional[float] = Field(None)
    splits: List[Split] = Field(default_factory=list)


class Run(BaseModel):
    """A single run session."""
    run_id: str = Field(..., description="UUID for this run")
    user_id: Optional[str] = Field(None)
    name: Optional[str] = Field(None, description="User-given name for the run")
    started_at: datetime = Field(..., description="When the run was started")
    finished_at: Optional[datetime] = Field(None, description="When the run was finished")
    points: List[GpsPoint] = Field(default_factory=list)
    analytics: Optional[RunAnalytics] = Field(None)
    status: str = Field("active", description="active | finished | discarded")


class RunSummary(BaseModel):
    """Lightweight summary used in list endpoints."""
    run_id: str
    name: Optional[str]
    started_at: datetime
    finished_at: Optional[datetime]
    status: str
    total_distance_m: float = 0.0
    elapsed_seconds: float = 0.0
    moving_seconds: float = 0.0
    avg_pace_sec_per_km: Optional[float] = None
    elevation_gain_m: float = 0.0
    point_count: int = 0

    @classmethod
    def from_run(cls, run: Run) -> "RunSummary":
        analytics = run.analytics or RunAnalytics()
        return cls(
            run_id=run.run_id,
            name=run.name,
            started_at=run.started_at,
            finished_at=run.finished_at,
            status=run.status,
            total_distance_m=analytics.total_distance_m,
            elapsed_seconds=analytics.elapsed_seconds,
            moving_seconds=analytics.moving_seconds,
            avg_pace_sec_per_km=analytics.avg_pace_sec_per_km,
            elevation_gain_m=analytics.elevation_gain_m,
            point_count=len(run.points),
        )


class User(BaseModel):
    user_id: str
    display_name: str
    email: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    total_runs: int = 0
    total_distance_m: float = 0.0
