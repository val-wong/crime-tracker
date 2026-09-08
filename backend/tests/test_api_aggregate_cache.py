"""The aggregate endpoint's short-lived in-process response cache (see
app/api/incidents.py "Real EXPLAIN ANALYZE evidence" -- a citywide
aggregate query is expensive at full data scale; this cache is the
pragmatic V1 mitigation). Uses fixture data, never the live API.
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.adapters.chicago import ChicagoSourceAdapter
from app.api import incidents as incidents_api
from app.db.session import get_db
from app.main import app
from app.models.source import Source
from app.services.ingestion import run_ingestion_cycle

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "chicago_sample_rows.json"


def _ingest_fixture_rows(db) -> Source:
    rows = json.loads(FIXTURE_PATH.read_text())
    source = Source(source_key="chicago-pd-open-data-cache-test", name="Chicago Cache Test Fixture")
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


def test_aggregate_cache_returns_same_response_without_requerying(db, monkeypatch):
    _ingest_fixture_rows(db)
    incidents_api._aggregate_cache.clear()

    call_count = 0
    original = incidents_api.incidents_repo.aggregate_incidents

    def counting_aggregate(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(incidents_api.incidents_repo, "aggregate_incidents", counting_aggregate)

    # A non-month-aligned date range forces the raw-table path (see
    # docs/rollup-design.md "Date-range fallback rule") rather than the
    # rollup, so this test exercises the cache in front of the code
    # path it actually protects.
    params = {"zoom": 8, "start_date": "2001-01-01", "end_date": "2001-01-11"}
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        first = client.get("/api/incidents/aggregate", params=params)
        second = client.get("/api/incidents/aggregate", params=params)
    finally:
        app.dependency_overrides.pop(get_db, None)
        incidents_api._aggregate_cache.clear()

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json()
    assert call_count == 1  # second request served from cache, no repository call


def test_aggregate_cache_is_scoped_by_filters(db, monkeypatch):
    _ingest_fixture_rows(db)
    incidents_api._aggregate_cache.clear()

    call_count = 0
    original = incidents_api.incidents_repo.aggregate_incidents

    def counting_aggregate(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(incidents_api.incidents_repo, "aggregate_incidents", counting_aggregate)

    base_params = {"zoom": 8, "start_date": "2001-01-01", "end_date": "2001-01-11"}
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        client.get("/api/incidents/aggregate", params=base_params)
        client.get("/api/incidents/aggregate", params={**base_params, "category": "THEFT"})
    finally:
        app.dependency_overrides.pop(get_db, None)
        incidents_api._aggregate_cache.clear()

    assert call_count == 2  # different filters -> different cache key -> real query each time
