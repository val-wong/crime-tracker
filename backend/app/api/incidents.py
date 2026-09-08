"""Incidents API.

Reads from the database (see app/repositories/incidents.py). On a
clean database this returns an empty collection. Synthetic/fixture
data is never seeded into this endpoint's database in normal
operation — it only appears in tests (see app/adapters/fixture.py) or
after a deliberate Chicago ingestion command (see
docs/operations/chicago-ingestion.md).

Two endpoints exist for two different map zoom regimes -- see
docs/map-aggregation.md for the full reasoning:

- `GET /api/incidents` -- individual incidents, for close zoom and
  incident-detail lookups. Always paginated, capped at `MAX_LIMIT`.
- `GET /api/incidents/aggregate` -- grid-cell counts, for wide/medium
  zoom. Never returns raw incident rows.

Filtering: date range, category, neighborhood, district, and a map
bounding box (`min_lon`/`min_lat`/`max_lon`/`max_lat`), backed by the
existing PostGIS GIST index on `incidents.location`.
"""

import time
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.repositories import incidents as incidents_repo
from app.repositories import rollup as rollup_repo
from app.repositories.incidents import BoundingBox
from app.schemas.aggregate import GridCellResponse, IncidentAggregateResponse
from app.schemas.incident import Incident, IncidentListResponse

router = APIRouter(prefix="/api/incidents", tags=["incidents"])

MAX_LIMIT = 500

# Real EXPLAIN ANALYZE evidence against the full ~8.6M-row Chicago
# dataset (see docs/map-aggregation.md "Known limitation: wide-zoom
# aggregate performance"): a citywide aggregate query touches nearly
# every row regardless of grid size or bounding box, taking 10-20s --
# an index cannot fix a near-100%-selectivity scan, and a proper fix
# (a periodically-refreshed rollup table) is a bigger change deferred
# to a future phase. This in-process cache is the pragmatic, in-scope
# mitigation: the underlying data only changes via periodic ingestion
# runs (hours/days apart), so a short TTL makes every viewer after the
# first, for a given filter/grid combination, instant -- without
# claiming this fixes the underlying query cost.
_AGGREGATE_CACHE_TTL_SECONDS = 120
_aggregate_cache: dict[tuple, tuple[float, IncidentAggregateResponse]] = {}


def _aggregate_cache_get(key: tuple) -> Optional[IncidentAggregateResponse]:
    entry = _aggregate_cache.get(key)
    if entry is None:
        return None
    cached_at, response = entry
    if time.monotonic() - cached_at > _AGGREGATE_CACHE_TTL_SECONDS:
        _aggregate_cache.pop(key, None)
        return None
    return response


def _aggregate_cache_set(key: tuple, response: IncidentAggregateResponse) -> None:
    _aggregate_cache[key] = (time.monotonic(), response)


# Real evidence (see docs/map-aggregation.md "Known limitation:
# wide-zoom aggregate performance") showed the slow-query problem isn't
# limited to an unbounded/citywide request -- any bounding box covering
# a large-enough fraction of the dataset is slow, and ordinary map
# panning changes the bbox on every move, which would defeat a cache
# keyed on the exact bbox (every pan = a cache miss = another
# multi-second query). Snapping the bbox actually used for both the
# cache key AND the real query to a coarse grid means nearby pans reuse
# the same cached (slightly larger-than-requested) result -- a
# reasonable approximation at the zoom levels wide enough for this to
# matter; a few extra cells just outside the exact viewport are not
# perceptible at that scale.
_BBOX_SNAP_DEGREES = 0.1


def _snap_bbox(bbox: Optional[BoundingBox]) -> Optional[BoundingBox]:
    if bbox is None:
        return None
    step = _BBOX_SNAP_DEGREES
    return BoundingBox(
        min_lon=(bbox.min_lon // step) * step,
        min_lat=(bbox.min_lat // step) * step,
        max_lon=((bbox.max_lon // step) + 1) * step,
        max_lat=((bbox.max_lat // step) + 1) * step,
    )


# See docs/map-aggregation.md "Zoom -> grid size mapping". Ordered from
# widest to narrowest; the first threshold the requested zoom is <= to
# wins.
_ZOOM_GRID_BREAKPOINTS: list[tuple[int, float]] = [
    (10, 0.05),
    (12, 0.02),
    (14, 0.005),
]
_DEFAULT_GRID_DEGREES = 0.001  # zoom >= 15


def _grid_degrees_for_zoom(zoom: int) -> float:
    for max_zoom, grid_degrees in _ZOOM_GRID_BREAKPOINTS:
        if zoom <= max_zoom:
            return grid_degrees
    return _DEFAULT_GRID_DEGREES


def _bbox_from_query(
    min_lon: Optional[float],
    min_lat: Optional[float],
    max_lon: Optional[float],
    max_lat: Optional[float],
) -> Optional[BoundingBox]:
    values = (min_lon, min_lat, max_lon, max_lat)
    if all(v is None for v in values):
        return None
    if any(v is None for v in values):
        raise HTTPException(
            status_code=422,
            detail="min_lon, min_lat, max_lon, and max_lat must all be provided together",
        )
    if min_lon >= max_lon or min_lat >= max_lat:
        raise HTTPException(status_code=422, detail="bounding box min must be less than max")
    return BoundingBox(min_lon=min_lon, min_lat=min_lat, max_lon=max_lon, max_lat=max_lat)


@router.get("", response_model=IncidentListResponse)
def list_incidents(
    db: Session = Depends(get_db),
    start_date: Optional[date] = Query(None, description="Filter: occurred_at >= start_date"),
    end_date: Optional[date] = Query(None, description="Filter: occurred_at <= end_date"),
    category: Optional[str] = Query(
        None, description="Filter: offense source_category (e.g. THEFT)"
    ),
    neighborhood: Optional[str] = Query(None, description="Filter: neighborhood"),
    district: Optional[str] = Query(None, description="Filter: district"),
    min_lon: Optional[float] = Query(None, ge=-180, le=180, description="Bounding box west edge"),
    min_lat: Optional[float] = Query(None, ge=-90, le=90, description="Bounding box south edge"),
    max_lon: Optional[float] = Query(None, ge=-180, le=180, description="Bounding box east edge"),
    max_lat: Optional[float] = Query(None, ge=-90, le=90, description="Bounding box north edge"),
    limit: int = Query(100, ge=1, le=MAX_LIMIT, description="Page size"),
    offset: int = Query(0, ge=0, description="Page offset"),
) -> IncidentListResponse:
    bbox = _bbox_from_query(min_lon, min_lat, max_lon, max_lat)
    filters = dict(
        start_date=start_date,
        end_date=end_date,
        category=category,
        neighborhood=neighborhood,
        district=district,
        bbox=bbox,
    )
    total = incidents_repo.count_incidents_capped(db, **filters)
    rows = incidents_repo.list_incidents(db, **filters, limit=limit, offset=offset)
    return IncidentListResponse(
        items=[Incident.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/aggregate", response_model=IncidentAggregateResponse)
def aggregate_incidents(
    db: Session = Depends(get_db),
    zoom: int = Query(10, ge=0, le=22, description="Map zoom level; determines grid cell size"),
    start_date: Optional[date] = Query(None),
    end_date: Optional[date] = Query(None),
    category: Optional[str] = Query(None),
    neighborhood: Optional[str] = Query(None),
    min_lon: Optional[float] = Query(None, ge=-180, le=180),
    min_lat: Optional[float] = Query(None, ge=-90, le=90),
    max_lon: Optional[float] = Query(None, ge=-180, le=180),
    max_lat: Optional[float] = Query(None, ge=-90, le=90),
) -> IncidentAggregateResponse:
    raw_bbox = _bbox_from_query(min_lon, min_lat, max_lon, max_lat)
    grid_degrees = _grid_degrees_for_zoom(zoom)

    # Use the precomputed rollup (see docs/rollup-design.md) whenever
    # it can answer the request exactly: no neighborhood filter (not a
    # rollup dimension), a materialized grid size, and a month-aligned
    # (or absent) date range. Anything else falls back to the raw-table
    # path unchanged from the map/dashboard phase.
    use_rollup = (
        neighborhood is None
        and grid_degrees in rollup_repo.ROLLUP_GRID_SIZES
        and rollup_repo.is_month_aligned_range(start_date, end_date)
    )

    # Bbox snapping (see _snap_bbox) trades a slightly larger-than-
    # requested area for a much higher cache-hit rate -- a reasonable
    # trade at the wide zoom levels the rollup serves, where the number
    # of resulting grid cells stays small regardless of area. It is
    # deliberately NOT applied to the raw/close-zoom path: confirmed
    # directly (EXPLAIN ANALYZE) that snapping a genuinely small
    # close-zoom viewport up to a 0.1 degree box can pull in millions of
    # candidate rows in a dense area, turning a normally sub-second
    # close-zoom query into a 30+ second one grouping by a fine grid
    # over that whole widened area. Close zoom uses the caller's exact
    # bounding box instead.
    bbox = _snap_bbox(raw_bbox) if use_rollup else raw_bbox

    cache_key = (
        grid_degrees,
        start_date,
        end_date,
        category,
        neighborhood,
        bbox and (bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat),
    )
    cached = _aggregate_cache_get(cache_key)
    if cached is not None:
        return cached

    if use_rollup:
        cells = rollup_repo.query_rollup(
            db,
            grid_degrees=grid_degrees,
            start_date=start_date,
            end_date=end_date,
            category=category,
            bbox=bbox,
        )
        source = "rollup"
    else:
        cells = incidents_repo.aggregate_incidents(
            db,
            grid_degrees=grid_degrees,
            start_date=start_date,
            end_date=end_date,
            category=category,
            neighborhood=neighborhood,
            bbox=bbox,
        )
        source = "raw"

    response = IncidentAggregateResponse(
        cells=[GridCellResponse(lon=c.lon, lat=c.lat, count=c.count) for c in cells],
        grid_degrees=grid_degrees,
        source=source,
    )
    _aggregate_cache_set(cache_key, response)
    return response
