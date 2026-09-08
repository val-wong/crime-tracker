"""Bounding-box filtering on GET /api/incidents, against real
Chicago-shaped fixture rows (never the live API) -- see
tests/fixtures/chicago_sample_rows.json and docs/map-aggregation.md.
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.adapters.chicago import ChicagoSourceAdapter
from app.db.session import get_db
from app.main import app
from app.models.source import Source
from app.services.ingestion import run_ingestion_cycle

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "chicago_sample_rows.json"


def _ingest_fixture_rows(db) -> Source:
    rows = json.loads(FIXTURE_PATH.read_text())
    source = Source(source_key="chicago-pd-open-data-geo-test", name="Chicago Geo Test Fixture")
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


def test_bbox_filter_isolates_incidents_within_box(db):
    _ingest_fixture_rows(db)

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        # A tight box around fixture row id=634 (41.880224549, -87.688248952)
        # only -- no other fixture row's coordinates fall inside it.
        response = client.get(
            "/api/incidents",
            params={
                "min_lon": -87.70,
                "min_lat": 41.87,
                "max_lon": -87.68,
                "max_lat": 41.89,
                "limit": 100,
            },
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["external_incident_id"] == "G000705"


def test_bbox_excludes_out_of_box_incidents(db):
    _ingest_fixture_rows(db)

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        # A box over open water / no fixture coordinates.
        response = client.get(
            "/api/incidents",
            params={"min_lon": -80.0, "min_lat": 30.0, "max_lon": -79.0, "max_lat": 31.0},
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert response.json()["total"] == 0


def test_bbox_requires_all_four_params(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get("/api/incidents", params={"min_lon": -87.7, "min_lat": 41.8})
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 422


def test_bbox_rejects_inverted_box(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get(
            "/api/incidents",
            params={"min_lon": -87.5, "min_lat": 41.9, "max_lon": -87.9, "max_lat": 41.8},
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 422


def test_bbox_rejects_out_of_range_latitude(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get(
            "/api/incidents",
            params={"min_lon": -87.9, "min_lat": 200.0, "max_lon": -87.5, "max_lat": 41.9},
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 422
