"""
GPS run analytics engine.

Easy parts (implemented here):
  - Haversine distance between two GPS points
  - Total distance accumulation
  - Pace calculation (min/km)
  - Elevation gain with noise filtering
  - Per-km splits
  - Moving time vs elapsed time

Hard parts (commented, not implemented):
  - GPS accuracy smoothing / Kalman filtering
  - Barometric vs GPS elevation reconciliation
  - Segment / lap detection
  - Heart rate zone analysis
  - Route matching against known segments
  - Social graph feed and Kudos aggregation
  - Photo tagging with GPS EXIF correlation
  - Auto-pause tuning per activity type
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

from .models import GpsPoint, RunAnalytics, Split


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EARTH_RADIUS_M = 6_371_000.0          # metres
ELEVATION_NOISE_THRESHOLD_M = 2.0     # ignore elevation changes smaller than this
AUTO_PAUSE_THRESHOLD_SECONDS = 15.0   # gap longer than this counts as a pause
AUTO_PAUSE_MIN_SPEED_MS = 0.3         # below this m/s we consider the runner stopped


# ---------------------------------------------------------------------------
# Core geometry
# ---------------------------------------------------------------------------

def haversine(p1: GpsPoint, p2: GpsPoint) -> float:
    """Return the great-circle distance in metres between two GPS points."""
    lat1, lon1 = math.radians(p1.lat), math.radians(p1.lon)
    lat2, lon2 = math.radians(p2.lat), math.radians(p2.lon)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.asin(math.sqrt(a))
    return EARTH_RADIUS_M * c


def bearing(p1: GpsPoint, p2: GpsPoint) -> float:
    """Compass bearing in degrees (0–360) from p1 to p2."""
    lat1, lon1 = math.radians(p1.lat), math.radians(p1.lon)
    lat2, lon2 = math.radians(p2.lat), math.radians(p2.lon)
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


# ---------------------------------------------------------------------------
# Segment-level helpers
# ---------------------------------------------------------------------------

def segment_duration_seconds(p1: GpsPoint, p2: GpsPoint) -> float:
    """Wall-clock seconds between two GPS readings."""
    return abs((p2.timestamp - p1.timestamp).total_seconds())


def is_moving(p1: GpsPoint, p2: GpsPoint) -> bool:
    """
    Return True if the runner is moving between two consecutive points.

    A gap > AUTO_PAUSE_THRESHOLD_SECONDS combined with a speed below
    AUTO_PAUSE_MIN_SPEED_MS is treated as a deliberate pause (traffic light,
    water stop, etc.).  Short gaps at any speed are always 'moving'.
    """
    dt = segment_duration_seconds(p1, p2)
    if dt <= 0:
        return True  # same timestamp edge case – keep it

    dist = haversine(p1, p2)
    speed = dist / dt  # m/s

    if dt > AUTO_PAUSE_THRESHOLD_SECONDS and speed < AUTO_PAUSE_MIN_SPEED_MS:
        return False  # long pause with virtually no displacement

    return True


# ---------------------------------------------------------------------------
# Elevation helpers
# ---------------------------------------------------------------------------

def elevation_deltas(points: List[GpsPoint]) -> Tuple[float, float]:
    """
    Return (gain_m, loss_m) for a list of GPS points.

    Only counts changes larger than ELEVATION_NOISE_THRESHOLD_M to filter
    the ±3 m GPS altitude jitter that would otherwise accumulate into
    hundreds of phantom metres of gain on a flat course.

    NOTE (the hard part): GPS altitude error is typically ±10–15 m even with
    good signal, and much worse in urban canyons.  A barometric altimeter
    (as found in Garmin watches) is far more accurate for elevation but
    requires drift correction against GPS absolute altitude over time.
    A production implementation would fuse both sources with a complementary
    filter or Kalman smoother.
    """
    gain = 0.0
    loss = 0.0
    alts = [p.alt for p in points if p.alt is not None]
    if len(alts) < 2:
        return gain, loss

    for i in range(1, len(alts)):
        delta = alts[i] - alts[i - 1]
        if delta > ELEVATION_NOISE_THRESHOLD_M:
            gain += delta
        elif delta < -ELEVATION_NOISE_THRESHOLD_M:
            loss += abs(delta)

    return gain, loss


# ---------------------------------------------------------------------------
# Main analytics computation
# ---------------------------------------------------------------------------

def compute_analytics(points: List[GpsPoint]) -> RunAnalytics:
    """
    Compute all analytics for a finished (or in-progress) run.

    Returns a fully populated RunAnalytics object.
    """
    if len(points) < 2:
        return RunAnalytics()

    # Sort by timestamp to be safe (client might send out of order)
    sorted_pts = sorted(points, key=lambda p: p.timestamp)

    total_dist = 0.0
    moving_secs = 0.0

    # Elevation
    alts = [p.alt for p in sorted_pts if p.alt is not None]
    gain_m, loss_m = elevation_deltas(sorted_pts)
    max_alt = max(alts) if alts else None
    min_alt = min(alts) if alts else None

    # Per-km split accumulators
    splits: List[Split] = []
    split_dist = 0.0
    split_elapsed = 0.0
    split_moving = 0.0
    split_km = 1
    split_alts: List[float] = []
    KM = 1000.0

    for i in range(1, len(sorted_pts)):
        p1, p2 = sorted_pts[i - 1], sorted_pts[i]
        seg_dist = haversine(p1, p2)
        seg_secs = segment_duration_seconds(p1, p2)
        moving = is_moving(p1, p2)

        total_dist += seg_dist
        if moving:
            moving_secs += seg_secs

        # Collect altitude for current split
        if p2.alt is not None:
            split_alts.append(p2.alt)

        # Accumulate split bucket
        remaining = seg_dist
        remaining_secs = seg_secs
        remaining_moving = seg_secs if moving else 0.0

        while split_dist + remaining >= KM:
            # How much of this segment fills the current km bucket
            portion = (KM - split_dist) / seg_dist if seg_dist > 0 else 1.0
            fill_dist = KM - split_dist
            fill_elapsed = remaining_secs * portion
            fill_moving = remaining_moving * portion

            split_dist += fill_dist
            split_elapsed += fill_elapsed
            split_moving += fill_moving

            pace = (split_moving / split_dist * KM) if split_moving > 0 and split_dist > 0 else 0.0
            avg_alt = (sum(split_alts) / len(split_alts)) if split_alts else None

            splits.append(Split(
                km=split_km,
                distance_m=split_dist,
                elapsed_seconds=split_elapsed,
                moving_seconds=split_moving,
                pace_per_km=pace,
                avg_alt_m=avg_alt,
            ))

            # Start next km
            split_km += 1
            remaining -= fill_dist
            remaining_secs -= fill_elapsed
            remaining_moving -= fill_moving
            split_dist = 0.0
            split_elapsed = 0.0
            split_moving = 0.0
            split_alts = []

        split_dist += remaining
        split_elapsed += remaining_secs
        split_moving += remaining_moving

    # Partial last split
    if split_dist > 0:
        pace = (split_moving / split_dist * KM) if split_moving > 0 and split_dist > 0 else 0.0
        avg_alt = (sum(split_alts) / len(split_alts)) if split_alts else None
        splits.append(Split(
            km=split_km,
            distance_m=split_dist,
            elapsed_seconds=split_elapsed,
            moving_seconds=split_moving,
            pace_per_km=pace,
            avg_alt_m=avg_alt,
        ))

    elapsed_secs = (sorted_pts[-1].timestamp - sorted_pts[0].timestamp).total_seconds()
    avg_pace = (moving_secs / total_dist * KM) if total_dist > 0 and moving_secs > 0 else None

    return RunAnalytics(
        total_distance_m=total_dist,
        elapsed_seconds=elapsed_secs,
        moving_seconds=moving_secs,
        avg_pace_sec_per_km=avg_pace,
        elevation_gain_m=gain_m,
        elevation_loss_m=loss_m,
        max_alt_m=max_alt,
        min_alt_m=min_alt,
        splits=splits,
    )


# ---------------------------------------------------------------------------
# Convenience formatters (used by the API layer)
# ---------------------------------------------------------------------------

def format_pace(seconds_per_km: Optional[float]) -> str:
    """Return a human-readable pace string like '5:23 /km'."""
    if seconds_per_km is None or seconds_per_km <= 0:
        return "--:-- /km"
    mins = int(seconds_per_km // 60)
    secs = int(seconds_per_km % 60)
    return f"{mins}:{secs:02d} /km"


def format_duration(seconds: float) -> str:
    """Return 'h:mm:ss' or 'mm:ss' string."""
    total = int(seconds)
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_distance(metres: float) -> str:
    """Return '1.23 km' or '450 m'."""
    if metres >= 1000:
        return f"{metres / 1000:.2f} km"
    return f"{int(metres)} m"


# ---------------------------------------------------------------------------
# What makes GPS analytics HARD (not implemented — illustrative comments)
# ---------------------------------------------------------------------------
#
# 1. GPS SMOOTHING / KALMAN FILTER
#    Raw GPS traces have position jumps of 5–30 m especially at the start
#    of a run while the receiver gets a fix.  A Kalman filter (or simpler
#    Ramer–Douglas–Peucker simplification) is needed to produce smooth routes.
#    Without it you'll over-count distance by 3–8 %.
#
# 2. ELEVATION NOISE
#    Even with our 2 m threshold, GPS altitude noise on a flat course can
#    report 20–40 m of spurious gain per hour.  Garmin watches blend GPS
#    altitude with a barometric sensor; Strava post-processes elevation using
#    a DEM (digital elevation model) from SRTM/Mapbox and IGNORES the GPS
#    altitude entirely for elevation gain/loss.
#
# 3. SEGMENT DETECTION
#    Strava's "Segments" feature matches every run against millions of crowd-
#    sourced segment polylines using dynamic time warping (DTW) and spatial
#    indexing (R-tree / PostGIS).  Near-real-time at scale requires pre-built
#    spatial indexes and fanout workers per uploaded activity.
#
# 4. HEART RATE ZONES
#    Requires BLE or ANT+ sensor pairing, per-user max-HR calibration, and
#    time-in-zone histograms. Varies significantly by activity type.
#
# 5. SOCIAL FEED / KUDOS
#    Requires follow graph, activity fan-out on write (or pull-on-read with
#    caching), notification delivery, and spam/abuse detection.
#
# 6. PHOTO TAGGING
#    Correlate EXIF GPS/timestamp to route, handle timezone offsets, generate
#    thumbnails, CDN delivery, and privacy controls.
#
# 7. ROUTE MATCHING
#    "You ran this route before" detection requires approximate polyline
#    similarity with Fréchet distance, stored as a trajectory index.
#
# 8. BATTERY / SAMPLING RATE TRADE-OFFS
#    1 Hz GPS sampling (1 point/second) gives good accuracy but drains battery.
#    Adaptive sampling (slow down to 0.2 Hz when speed is constant) requires
#    dead-reckoning interpolation between sparse points.
