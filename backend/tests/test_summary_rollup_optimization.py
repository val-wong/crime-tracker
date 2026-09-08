"""Pre-staging cleanup: /api/summary's category breakdown was confirmed
via EXPLAIN ANALYZE to take ~24s at full Chicago scale (a full-table
`offenses ⋈ incidents` join, unavoidable via indexing alone since a
category like THEFT matches too large a fraction of the table). Fixed
by using the existing `incident_grid_rollup` when eligible -- see
app/services/summary.py::_category_breakdown and
docs/performance-validation.md.
"""

import json
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from app.adapters.chicago import ChicagoSourceAdapter
from app.db.session import get_db
from app.main import app
from app.models.source import Source
from app.repositories import rollup as rollup_repo
from app.repositories import summary as summary_repo
from app.services.ingestion import run_ingestion_cycle
from app.services.rollup import refresh_rollup

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "chicago_sample_rows.json"


def _ingest_fixture_rows(db, source_key: str) -> Source:
    rows = json.loads(FIXTURE_PATH.read_text())
    source = Source(source_key=source_key, name="Chicago Summary Rollup Test Fixture")
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


def test_summary_uses_rollup_for_month_aligned_range_without_neighborhood(db, monkeypatch):
    _ingest_fixture_rows(db, "chicago-summary-rollup-test")
    refresh_rollup(db)

    call_count = 0
    original = summary_repo.category_breakdown

    def counting_breakdown(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(summary_repo, "category_breakdown", counting_breakdown)

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get(
            "/api/summary", params={"start_date": "2001-01-01", "end_date": "2001-01-31"}
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    body = response.json()
    assert any(c["category"] == "HOMICIDE" for c in body["category_breakdown"])
    # Served from the rollup -- the raw (slow) path must not have run.
    assert call_count == 0


def test_summary_falls_back_to_raw_for_non_month_aligned_range(db, monkeypatch):
    _ingest_fixture_rows(db, "chicago-summary-rollup-fallback-test")
    refresh_rollup(db)

    call_count = 0
    original = summary_repo.category_breakdown

    def counting_breakdown(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(summary_repo, "category_breakdown", counting_breakdown)

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get(
            "/api/summary", params={"start_date": "2001-01-01", "end_date": "2001-01-11"}
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert call_count == 1  # raw path used, not the rollup


def test_summary_falls_back_to_raw_when_neighborhood_filter_present(db, monkeypatch):
    _ingest_fixture_rows(db, "chicago-summary-rollup-neighborhood-test")
    refresh_rollup(db)

    call_count = 0
    original = summary_repo.category_breakdown

    def counting_breakdown(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(summary_repo, "category_breakdown", counting_breakdown)

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get(
            "/api/summary",
            params={"start_date": "2001-01-01", "end_date": "2001-01-31", "neighborhood": "28"},
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert call_count == 1  # neighborhood filter -> rollup can't serve it, raw path used


def test_rollup_and_raw_category_breakdown_agree_on_month_aligned_range(db):
    """The rollup substitution must never change the actual numbers --
    only which code path produces them."""
    start_date = date(2001, 1, 1)
    end_date = date(2001, 1, 31)

    _ingest_fixture_rows(db, "chicago-summary-rollup-agreement-test")
    refresh_rollup(db)

    rollup_result = dict(
        rollup_repo.category_breakdown(db, start_date=start_date, end_date=end_date)
    )
    raw_result = {
        c.category: c.count
        for c in summary_repo.category_breakdown(
            db,
            source_id=None,
            start_date=start_date,
            end_date=end_date,
            neighborhood=None,
        )
    }
    assert rollup_result == raw_result
