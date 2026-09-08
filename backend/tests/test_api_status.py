"""GET /api/status, against real Chicago-shaped fixture rows (never the
live API). Uses the real CHICAGO_SOURCE_KEY so the endpoint's source
lookup (see app/api/status.py) finds this test's data.
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.adapters.chicago import ChicagoSourceAdapter
from app.constants import CHICAGO_SOURCE_KEY
from app.db.session import get_db
from app.main import app
from app.models.source import Source
from app.services.ingestion import run_ingestion_cycle

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "chicago_sample_rows.json"


def test_status_before_any_source_exists_returns_well_formed_empty_state(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get("/api/status")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    body = response.json()
    assert body["city"] == "Chicago"
    assert body["incident_count"] == 0
    assert body["is_population_complete"] is False
    assert "cityofchicago.org" in body["attribution"]


def test_status_reflects_bounded_ingest_as_incomplete(db):
    rows = json.loads(FIXTURE_PATH.read_text())
    source = Source(source_key=CHICAGO_SOURCE_KEY, name="Chicago Status Test Fixture")
    db.add(source)
    db.flush()
    adapter = ChicagoSourceAdapter()
    adapter.fetch_current_records = lambda: iter(rows)
    try:
        run_ingestion_cycle(db, source=source, adapter=adapter, is_complete=False)
    finally:
        adapter.close()
    db.commit()

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get("/api/status")
    finally:
        app.dependency_overrides.pop(get_db, None)

    body = response.json()
    assert body["is_population_complete"] is False  # bounded run, never is_complete
    assert body["incident_count"] > 0
    assert body["offense_count"] == len(rows)
    assert body["earliest_occurred_date"] == "2001-01-01"
    assert body["latest_occurred_date"] == "2023-09-06"


def test_status_reflects_a_full_run_as_population_complete(db):
    rows = json.loads(FIXTURE_PATH.read_text())
    source = Source(source_key=CHICAGO_SOURCE_KEY, name="Chicago Status Test Fixture Full")
    db.add(source)
    db.flush()
    adapter = ChicagoSourceAdapter()
    adapter.fetch_current_records = lambda: iter(rows)
    try:
        # A "full" run in this test's fixture-scale world: schema_valid
        # and a reported_total_count matching what was fetched, so the
        # plausibility check passes and is_complete really does mean
        # "the whole current dataset was fetched" -- same semantics as
        # the real Chicago CLI path (see app/cli.py).
        run_ingestion_cycle(
            db,
            source=source,
            adapter=adapter,
            is_complete=True,
            schema_valid=True,
            reported_total_count=len(rows),
        )
    finally:
        adapter.close()
    db.commit()

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get("/api/status")
    finally:
        app.dependency_overrides.pop(get_db, None)

    body = response.json()
    assert body["is_population_complete"] is True
    assert body["latest_successful_ingestion_at"] is not None
