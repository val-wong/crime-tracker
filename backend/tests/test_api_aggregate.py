"""GET /api/incidents/aggregate, against real Chicago-shaped fixture
rows (never the live API) -- see docs/map-aggregation.md.
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.adapters.chicago import ChicagoSourceAdapter
from app.db.session import get_db
from app.main import app
from app.models.source import Source
from app.services.ingestion import run_ingestion_cycle
from app.services.rollup import refresh_rollup

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "chicago_sample_rows.json"


def _ingest_fixture_rows(db) -> Source:
    rows = json.loads(FIXTURE_PATH.read_text())
    source = Source(
        source_key="chicago-pd-open-data-agg-test", name="Chicago Aggregate Test Fixture"
    )
    db.add(source)
    db.flush()

    adapter = ChicagoSourceAdapter()
    adapter.fetch_current_records = lambda: iter(rows)
    try:
        run_ingestion_cycle(db, source=source, adapter=adapter, is_complete=False)
    finally:
        adapter.close()
    db.commit()
    return source


def test_aggregate_returns_cells_summing_to_geocoded_incident_count(db):
    _ingest_fixture_rows(db)
    refresh_rollup(db)  # zoom 8 -> grid 0.05 is served from the rollup; must be fresh

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get("/api/incidents/aggregate", params={"zoom": 8})
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    body = response.json()
    assert body["grid_degrees"] == 0.05  # zoom 8 -> widest bucket, see docs/map-aggregation.md
    assert len(body["cells"]) > 0
    total_from_cells = sum(cell["count"] for cell in body["cells"])
    # Aggregation counts INCIDENTS, not raw offense rows -- the fixture's
    # multi-victim homicide case (G023235, 2 rows sharing one
    # case_number) collapses to one incident, same as everywhere else
    # in this platform (see docs/sources/chicago.md §2). Rows without
    # coordinates (some THEFT fixture rows) are correctly excluded, not
    # double counted or dropped silently.
    geocoded_case_numbers = {
        r["case_number"]
        for r in json.loads(FIXTURE_PATH.read_text())
        if r.get("latitude") is not None and r.get("longitude") is not None
    }
    assert total_from_cells == len(geocoded_case_numbers)


def test_aggregate_grid_size_varies_by_zoom(db):
    _ingest_fixture_rows(db)

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        wide = client.get("/api/incidents/aggregate", params={"zoom": 5}).json()
        close = client.get("/api/incidents/aggregate", params={"zoom": 16}).json()
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert wide["grid_degrees"] > close["grid_degrees"]


def test_aggregate_respects_bbox_filter(db):
    _ingest_fixture_rows(db)

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get(
            "/api/incidents/aggregate",
            params={
                "zoom": 12,
                "min_lon": -80.0,
                "min_lat": 30.0,
                "max_lon": -79.0,
                "max_lat": 31.0,
            },
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert response.json()["cells"] == []


def test_aggregate_respects_category_filter(db):
    _ingest_fixture_rows(db)
    refresh_rollup(db)

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get("/api/incidents/aggregate", params={"zoom": 8, "category": "THEFT"})
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    body = response.json()
    total_from_cells = sum(cell["count"] for cell in body["cells"])
    # Only one THEFT fixture row has coordinates (id 13201227).
    assert total_from_cells == 1
