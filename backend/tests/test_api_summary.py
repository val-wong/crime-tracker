"""GET /api/summary, against real Chicago-shaped fixture rows (never
the live API). Uses the real CHICAGO_SOURCE_KEY so the endpoint's
source lookup (see app/api/summary.py) finds this test's data.
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
    source = Source(source_key=CHICAGO_SOURCE_KEY, name="Chicago Summary Test Fixture")
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


def test_summary_counts_incidents_in_explicit_period(db):
    _ingest_fixture_rows(db)

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get(
            "/api/summary", params={"start_date": "2001-01-01", "end_date": "2001-01-11"}
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    body = response.json()
    # 14 of the 15 oldest homicide fixture rows occur in this window
    # (G000705..G021349); G023235's two rows occur on 2001-01-11 too.
    assert body["reported_incidents"] > 0
    assert body["start_date"] == "2001-01-01"
    assert body["end_date"] == "2001-01-11"
    assert body["most_common_category"] == "HOMICIDE"


def test_summary_percent_change_is_none_when_previous_period_is_empty(db):
    _ingest_fixture_rows(db)

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get(
            "/api/summary", params={"start_date": "2001-01-01", "end_date": "2001-01-11"}
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    body = response.json()
    # The comparable period immediately before 2001-01-01 has zero
    # fixture rows -- percent_change must be null, not a divide-by-zero
    # error or a misleading 0%/None conflation.
    assert body["previous_period_reported_incidents"] == 0
    assert body["percent_change"] is None


def test_summary_never_uses_risk_or_danger_language(db):
    _ingest_fixture_rows(db)

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get("/api/summary")
    finally:
        app.dependency_overrides.pop(get_db, None)

    body_text = json.dumps(response.json()).lower()
    for banned in ("danger", "risk", "unsafe", "safest", "probability"):
        assert banned not in body_text


def test_summary_category_breakdown_and_top_neighborhoods(db):
    _ingest_fixture_rows(db)

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get(
            "/api/summary", params={"start_date": "2001-01-01", "end_date": "2001-01-12"}
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    body = response.json()
    assert any(c["category"] == "HOMICIDE" for c in body["category_breakdown"])
    assert body["time_bucket"] == "week"  # an 11-day span uses weekly buckets
    assert len(body["top_neighborhoods"]) > 0


def test_summary_resolves_neighborhood_codes_to_official_names(db):
    _ingest_fixture_rows(db)

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get(
            "/api/summary", params={"start_date": "2001-01-01", "end_date": "2001-01-12"}
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    body = response.json()
    # Fixture row G000705 has community_area "28" -- Chicago's official
    # name for code 28 is "Near West Side" (see
    # app/chicago_community_areas.py). The numeric code is preserved
    # unchanged alongside the resolved name, for backward-compatible
    # filtering/querying.
    area_28 = next(n for n in body["top_neighborhoods"] if n["neighborhood"] == "28")
    assert area_28["name"] == "Near West Side"

    most_represented = body["most_represented_neighborhood"]
    assert most_represented is not None
    assert body["most_represented_neighborhood_name"] == (
        next(n["name"] for n in body["top_neighborhoods"] if n["neighborhood"] == most_represented)
    )
