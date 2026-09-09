"""Regression tests for a real map-quality bug found during manual QA:
at wide/medium aggregate zoom, some circles appeared offshore in Lake
Michigan, even though the underlying incidents were on/near land.

Root cause (see app/repositories/incidents.py's `aggregate_incidents`
and app/repositories/rollup.py's `query_rollup`): aggregate cells were
labeled with the *geometric* grid-cell center
(`floor(lon/grid)*grid + grid/2`), computed independent of where the
contributing incidents actually sit within that cell. Confirmed
directly against the real ~8.6M-row Chicago dataset: the 0.05°-grid
cell at (-87.525, 41.775) has 3,514 real incidents, every single one
at longitude <= -87.5414 (the land/lakefront side), yet its geometric
center (-87.525) sits *east* of all of them, out over open water.

Fix: label each cell with the **centroid of its actual contributing
incidents** instead -- computed as `avg(lon)`/`avg(lat)` in the same
grouped aggregation that already computes `count()` (raw path), and
precomputed the same way during rollup refresh (`centroid_lon`/
`centroid_lat` columns, migration 0010). A centroid can never fall
outside the convex hull of the points it averages, so it can never
drift into a part of a cell (open water) with zero contributing
incidents. Cell *membership* (the grouping key) and `incident_count`
are completely unchanged -- only the displayed lon/lat differs.

This file uses a small, hand-built synthetic dataset (not the shared
`chicago_sample_rows.json` fixture) so every expected coordinate is
exactly computable and shoreline-like clustering can be deliberately
constructed and verified against. Every point below is a (lon, lat)
pair, matching how coordinates are written everywhere else in this
codebase (e.g. `BoundingBox`, `GridCell`).
"""

import math

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.adapters.chicago import ChicagoSourceAdapter
from app.api import incidents as incidents_api
from app.db.session import get_db
from app.main import app
from app.models.incident import Incident
from app.models.source import Source
from app.repositories import incidents as incidents_repo
from app.repositories import rollup as rollup_repo
from app.services.ingestion import run_ingestion_cycle
from app.services.rollup import refresh_rollup

GRID = 0.05

# A cell whose bounds are [-87.60, -87.55) x [41.90, 41.95) -- its
# *geometric* center is (-87.575, 41.925). Every synthetic incident
# below is placed tightly against the cell's west/south corner (the
# "shoreline" side), deliberately far from that geometric center, the
# same shape of situation confirmed in the real dataset.
GEOMETRIC_CENTER = (-87.575, 41.925)

SHORELINE_POINTS = [
    (-87.599, 41.901),
    (-87.598, 41.902),
    (-87.597, 41.903),
    (-87.596, 41.904),
]
# Same cell, but a different month -- used to prove the rollup's
# cross-month weighted-average combination is exact.
SHORELINE_POINT_OTHER_MONTH = (-87.595, 41.905)
ALL_SHORELINE_POINTS = SHORELINE_POINTS + [SHORELINE_POINT_OTHER_MONTH]

# A point in a *different* cell entirely (its floor(lon/grid),
# floor(lat/grid) bucket differs) -- must never influence the
# shoreline cell's centroid or count.
OTHER_CELL_POINT = (-87.10, 41.20)


def _cell_key(lon: float, lat: float) -> tuple:
    return (math.floor(lon / GRID), math.floor(lat / GRID))


def _row(record_id, case_number, occurred_on, lon=None, lat=None, primary_type="THEFT"):
    timestamp = f"{occurred_on}T12:00:00.000"
    row = {
        "id": record_id,
        "case_number": case_number,
        "date": timestamp,
        "updated_on": timestamp,
        "iucr": "0000",
        "primary_type": primary_type,
        "community_area": "1",
    }
    if lon is not None and lat is not None:
        row["latitude"] = str(lat)
        row["longitude"] = str(lon)
    return row


def _build_rows() -> list[dict]:
    rows = []
    for i, (lon, lat) in enumerate(SHORELINE_POINTS, start=1):
        rows.append(_row(f"7000{i}", f"SHORE-{i}", "2026-08-10", lon, lat))
    rows.append(
        _row(
            "70099",
            "SHORE-OTHER-MONTH",
            "2026-09-15",
            SHORELINE_POINT_OTHER_MONTH[0],
            SHORELINE_POINT_OTHER_MONTH[1],
        )
    )
    rows.append(
        _row("70100", "ELSEWHERE-1", "2026-08-10", OTHER_CELL_POINT[0], OTHER_CELL_POINT[1])
    )
    # No coordinates at all -- must be excluded, not counted anywhere.
    rows.append(_row("70200", "NO-LOCATION-1", "2026-08-10"))
    return rows


def _ingest(db, source_key: str) -> Source:
    source = Source(source_key=source_key, name="Shoreline Centroid Test Fixture")
    db.add(source)
    db.flush()

    adapter = ChicagoSourceAdapter()
    adapter.fetch_current_records = lambda: iter(_build_rows())
    try:
        run_ingestion_cycle(db, source=source, adapter=adapter, is_complete=False)
    finally:
        adapter.close()
    db.commit()
    return source


def _shoreline_cell(cells):
    target_key = _cell_key(*GEOMETRIC_CENTER)
    matches = [c for c in cells if _cell_key(c.lon, c.lat) == target_key]
    assert len(matches) == 1, f"expected exactly one shoreline cell, got {matches}"
    return matches[0]


def test_raw_path_centroid_stays_within_contributing_points_bounding_box(db):
    """The representative point must be derivable only from the records
    actually in that cell, and can never drift outside their spatial
    range purely from geometric cell-center placement."""
    _ingest(db, "shoreline-raw-test")

    cells = incidents_repo.aggregate_incidents(db, grid_degrees=GRID)
    cell = _shoreline_cell(cells)

    min_lon = min(p[0] for p in ALL_SHORELINE_POINTS)
    max_lon = max(p[0] for p in ALL_SHORELINE_POINTS)
    min_lat = min(p[1] for p in ALL_SHORELINE_POINTS)
    max_lat = max(p[1] for p in ALL_SHORELINE_POINTS)

    assert min_lon <= cell.lon <= max_lon
    assert min_lat <= cell.lat <= max_lat
    # The old geometric center is measurably outside that range --
    # this is the actual bug being fixed, made concrete.
    assert not (min_lon <= GEOMETRIC_CENTER[0] <= max_lon)
    assert not (min_lat <= GEOMETRIC_CENTER[1] <= max_lat)


def test_raw_path_centroid_matches_exact_average_of_contributing_points(db):
    _ingest(db, "shoreline-raw-exact-test")

    cells = incidents_repo.aggregate_incidents(db, grid_degrees=GRID)
    cell = _shoreline_cell(cells)

    expected_lon = sum(p[0] for p in ALL_SHORELINE_POINTS) / len(ALL_SHORELINE_POINTS)
    expected_lat = sum(p[1] for p in ALL_SHORELINE_POINTS) / len(ALL_SHORELINE_POINTS)

    assert cell.lon == pytest.approx(expected_lon, abs=1e-9)
    assert cell.lat == pytest.approx(expected_lat, abs=1e-9)
    assert cell.count == len(ALL_SHORELINE_POINTS)


def test_count_and_cell_membership_are_unchanged_by_the_coordinate_fix(db):
    """The fix changes only the displayed lon/lat -- grouping and counts
    must be identical to what the plain geometric-bucket grouping key
    would produce on its own."""
    _ingest(db, "shoreline-membership-test")

    cells = incidents_repo.aggregate_incidents(db, grid_degrees=GRID)

    # Exactly 2 real cells: the shoreline cluster and the other-cell
    # point -- nothing else (the no-location row is correctly excluded
    # from every cell, not silently counted anywhere).
    assert len(cells) == 2
    total = sum(c.count for c in cells)
    assert total == len(ALL_SHORELINE_POINTS) + 1  # + the other-cell point

    other_cell = next(c for c in cells if _cell_key(c.lon, c.lat) == _cell_key(*OTHER_CELL_POINT))
    assert other_cell.count == 1
    assert other_cell.lon == pytest.approx(OTHER_CELL_POINT[0], abs=1e-9)
    assert other_cell.lat == pytest.approx(OTHER_CELL_POINT[1], abs=1e-9)


def test_raw_incident_coordinates_are_never_modified(db):
    """The fix must never touch stored incident coordinates -- only how
    an aggregate cell's representative point is *displayed*."""
    _ingest(db, "shoreline-raw-coords-test")

    stored = {
        row.external_incident_id: (row.longitude, row.latitude)
        for row in db.execute(select(Incident)).scalars()
    }
    for i, (lon, lat) in enumerate(SHORELINE_POINTS, start=1):
        assert stored[f"SHORE-{i}"] == (lon, lat)
    assert stored["SHORE-OTHER-MONTH"] == SHORELINE_POINT_OTHER_MONTH
    assert stored["ELSEWHERE-1"] == OTHER_CELL_POINT
    assert stored["NO-LOCATION-1"] == (None, None)


def test_rollup_and_raw_numeric_agreement_including_centroid(db):
    """The rollup-backed path must agree with the raw path not just on
    counts (already covered elsewhere in tests/test_rollup.py) but on
    the representative coordinate too -- and specifically prove the
    cross-month weighted combination (see app/repositories/rollup.py's
    `query_rollup` docstring) produces the exact same centroid as
    averaging every contributing point directly, for a cell whose
    incidents span two different months."""
    _ingest(db, "shoreline-rollup-agreement-test")
    refresh_rollup(db)

    raw_cells = incidents_repo.aggregate_incidents(db, grid_degrees=GRID)
    rollup_cells = rollup_repo.query_rollup(
        db, grid_degrees=GRID, start_date=None, end_date=None, category=None, bbox=None
    )

    raw_cell = _shoreline_cell(raw_cells)
    rollup_cell = _shoreline_cell(rollup_cells)

    assert rollup_cell.count == raw_cell.count
    assert rollup_cell.lon == pytest.approx(raw_cell.lon, abs=1e-6)
    assert rollup_cell.lat == pytest.approx(raw_cell.lat, abs=1e-6)


def test_shoreline_cell_survives_the_full_http_api(db):
    """End-to-end: the same fix is actually wired into the real
    endpoint both paths serve, not just the repository functions in
    isolation."""
    _ingest(db, "shoreline-api-test")
    refresh_rollup(db)

    # The endpoint's in-process response cache (see app/api/incidents.py)
    # is keyed by filters, not by database/transaction -- it must be
    # cleared before and after, or this test's default-filter response
    # would leak into (or be polluted by) any other test hitting the
    # same zoom/filter combination in the same pytest process, exactly
    # like tests/test_rollup.py's equivalent HTTP-level tests already do.
    incidents_api._aggregate_cache.clear()
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get("/api/incidents/aggregate", params={"zoom": 8})
    finally:
        app.dependency_overrides.pop(get_db, None)
        incidents_api._aggregate_cache.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "rollup"

    target_key = _cell_key(*GEOMETRIC_CENTER)
    matches = [c for c in body["cells"] if _cell_key(c["lon"], c["lat"]) == target_key]
    assert len(matches) == 1
    cell = matches[0]
    min_lon = min(p[0] for p in ALL_SHORELINE_POINTS)
    max_lon = max(p[0] for p in ALL_SHORELINE_POINTS)
    assert min_lon <= cell["lon"] <= max_lon
    assert cell["count"] == len(ALL_SHORELINE_POINTS)
