"""API tests against real Chicago-shaped rows run through the full
ingestion pipeline (ChicagoSourceAdapter -> run_ingestion_cycle ->
Postgres), never against Chicago's live API. Proves the pipeline and
the API's filters/pagination work together end-to-end, per item 17's
"API against ingested fixture Chicago-style records" requirement.
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
    source = Source(source_key="chicago-pd-open-data-test", name="Chicago Test Fixture")
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


def test_api_returns_ingested_chicago_incidents(db):
    _ingest_fixture_rows(db)

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get("/api/incidents", params={"limit": 100})
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    body = response.json()
    assert body["total"] > 0
    assert len(body["items"]) == body["total"]  # fixture is small; fits one page

    # The known multi-victim homicide case (see docs/sources/chicago.md
    # §1-2) should appear as ONE incident, not two.
    homicide_incidents = [i for i in body["items"] if i["external_incident_id"] == "G023235"]
    assert len(homicide_incidents) == 1
    assert "HOMICIDE" in homicide_incidents[0]["categories"]

    # Location precision is always BLOCK for Chicago, never EXACT.
    assert all(item["location_precision"] in ("block", "unknown") for item in body["items"])
    assert all(item["location_precision"] != "exact" for item in body["items"])


def test_api_filters_by_category(db):
    _ingest_fixture_rows(db)

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get("/api/incidents", params={"category": "THEFT", "limit": 100})
    finally:
        app.dependency_overrides.pop(get_db, None)

    body = response.json()
    assert body["total"] > 0
    assert all("THEFT" in item["categories"] for item in body["items"])


def test_api_filters_by_neighborhood(db):
    _ingest_fixture_rows(db)

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        # community_area "28" is on the first fixture row (see
        # tests/fixtures/chicago_sample_rows.json, id 634).
        response = client.get("/api/incidents", params={"neighborhood": "28", "limit": 100})
    finally:
        app.dependency_overrides.pop(get_db, None)

    body = response.json()
    assert body["total"] >= 1
    assert all(item["neighborhood"] == "28" for item in body["items"])


def test_api_pagination_limit_and_offset(db):
    _ingest_fixture_rows(db)

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        page1 = client.get("/api/incidents", params={"limit": 2, "offset": 0}).json()
        page2 = client.get("/api/incidents", params={"limit": 2, "offset": 2}).json()
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert len(page1["items"]) == 2
    assert len(page2["items"]) == 2
    ids_page1 = {item["id"] for item in page1["items"]}
    ids_page2 = {item["id"] for item in page2["items"]}
    assert ids_page1.isdisjoint(ids_page2)
