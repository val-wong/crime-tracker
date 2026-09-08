"""GET /api/categories -- the complete, filter-independent set of
valid category values (see app/api/categories.py). Contrast with
/api/summary's category_breakdown, which is scoped to whatever
date/neighborhood is currently selected.
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


def _ingest_fixture_rows(db) -> Source:
    rows = json.loads(FIXTURE_PATH.read_text())
    source = Source(source_key=CHICAGO_SOURCE_KEY, name="Chicago Categories Test Fixture")
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


def test_categories_returns_the_complete_alphabetized_set(db):
    _ingest_fixture_rows(db)

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get("/api/categories")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    body = response.json()
    # The fixture's only two primary_type values -- confirmed
    # alphabetized (HOMICIDE before THEFT), not ordered by frequency.
    assert body["categories"] == ["HOMICIDE", "THEFT"]


def test_categories_is_empty_and_well_formed_when_no_source_ingested_yet(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get("/api/categories")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert response.json() == {"categories": []}


def test_categories_is_unaffected_by_date_or_neighborhood_scoping(db):
    # /api/categories takes no query params at all -- unlike
    # /api/summary, there's no date range or neighborhood filter that
    # could narrow the result, which is the whole point: a stable,
    # complete option list for the filter UI regardless of what's
    # currently selected elsewhere on the dashboard.
    _ingest_fixture_rows(db)

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get(
            "/api/categories",
            params={"start_date": "2001-01-01", "end_date": "2001-01-02", "neighborhood": "25"},
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.json()["categories"] == ["HOMICIDE", "THEFT"]
