"""Aggregate rollup layer: correctness, refresh, and fallback behavior
(see docs/rollup-design.md). Uses real Chicago-shaped fixture rows,
never the live API.
"""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.adapters.chicago import ChicagoSourceAdapter
from app.api import incidents as incidents_api
from app.db.session import get_db
from app.main import app
from app.models.enums import RollupRefreshStatus
from app.models.rollup_refresh_run import RollupRefreshRun
from app.models.source import Source
from app.repositories import rollup as rollup_repo
from app.services.ingestion import run_ingestion_cycle
from app.services.rollup import get_latest_refresh, refresh_rollup

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "chicago_sample_rows.json"


def _ingest_fixture_rows(db, source_key: str) -> Source:
    rows = json.loads(FIXTURE_PATH.read_text())
    source = Source(source_key=source_key, name="Chicago Rollup Test Fixture")
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


def _raw_grid_count(db, *, grid_size: float, category) -> int:
    if category is None:
        sql = """
            SELECT count(*) FROM incidents i
            WHERE i.location IS NOT NULL AND i.occurred_at IS NOT NULL
        """
        return db.execute(text(sql)).scalar_one()
    sql = """
        SELECT count(*) FROM incidents i
        JOIN offenses o ON o.incident_id = i.id
        WHERE i.location IS NOT NULL AND i.occurred_at IS NOT NULL AND o.source_category = :cat
    """
    return db.execute(text(sql), {"cat": category}).scalar_one()


def test_rollup_refresh_creates_a_run_record(db):
    _ingest_fixture_rows(db, "chicago-rollup-refresh-test")
    result = refresh_rollup(db)

    assert result.status == RollupRefreshStatus.COMPLETED
    assert result.row_count is not None and result.row_count >= 0
    assert result.duration_seconds >= 0

    latest = get_latest_refresh(db)
    assert latest is not None
    assert latest.id == result.run_id
    assert latest.status == RollupRefreshStatus.COMPLETED
    assert latest.completed_at is not None


def test_rollup_correctness_no_category_matches_raw_distinct_count(db):
    """Category-agnostic rollup rows must equal a true distinct-incident
    count from the raw table -- no fan-out from the offenses join."""
    _ingest_fixture_rows(db, "chicago-rollup-correctness-test")
    refresh_rollup(db)

    for grid_size in rollup_repo.ROLLUP_GRID_SIZES:
        rollup_total = db.execute(
            text(
                "SELECT coalesce(sum(incident_count), 0) FROM incident_grid_rollup "
                "WHERE grid_size = :g AND category IS NULL"
            ),
            {"g": grid_size},
        ).scalar_one()
        raw_total = _raw_grid_count(db, grid_size=grid_size, category=None)
        assert rollup_total == raw_total, f"grid_size={grid_size}"


def test_rollup_correctness_category_row_matches_raw_offense_count(db):
    """Category-scoped rollup rows count incident/offense pairs (see
    docs/rollup-design.md's documented fan-out tradeoff), matching a
    direct join count against the raw tables."""
    _ingest_fixture_rows(db, "chicago-rollup-category-test")
    refresh_rollup(db)

    for grid_size in rollup_repo.ROLLUP_GRID_SIZES:
        rollup_total = db.execute(
            text(
                "SELECT coalesce(sum(incident_count), 0) FROM incident_grid_rollup "
                "WHERE grid_size = :g AND category = 'HOMICIDE'"
            ),
            {"g": grid_size},
        ).scalar_one()
        raw_total = _raw_grid_count(db, grid_size=grid_size, category="HOMICIDE")
        assert rollup_total == raw_total, f"grid_size={grid_size}"


def test_query_rollup_matches_raw_aggregate_for_eligible_request(db):
    """The rollup-backed query function must return the same cells/counts
    as the raw-table aggregate for a request both can serve exactly."""
    from app.repositories import incidents as incidents_repo

    _ingest_fixture_rows(db, "chicago-rollup-vs-raw-test")
    refresh_rollup(db)

    rollup_cells = rollup_repo.query_rollup(
        db, grid_degrees=0.05, start_date=None, end_date=None, category=None, bbox=None
    )
    raw_cells = incidents_repo.aggregate_incidents(db, grid_degrees=0.05)

    rollup_by_cell = {(c.lon, c.lat): c.count for c in rollup_cells}
    raw_by_cell = {(c.lon, c.lat): c.count for c in raw_cells}
    assert rollup_by_cell == raw_by_cell


def test_query_rollup_with_an_explicit_month_aligned_date_range(db):
    """Regression test (pre-staging cleanup): a bind parameter directly
    followed by a `::cast` in raw SQL text (`:start_date::date`) is a
    real SQLAlchemy `text()` parsing trap -- it silently fails to
    recognize the token as a bindparam, drops it from what's sent to
    the driver, and leaves literal `:start_date::date` in the SQL for
    Postgres to reject as a syntax error. This path was never actually
    exercised by any prior test (every existing rollup-path test used
    either no date filter or a *non*-month-aligned range that
    deliberately falls back to the raw query), so it went undetected
    until this phase -- meaning any real user filtering the map by an
    exact month at a wide/medium zoom would have hit a 500 error."""
    from datetime import date

    _ingest_fixture_rows(db, "chicago-rollup-explicit-date-range-test")
    refresh_rollup(db)

    cells = rollup_repo.query_rollup(
        db,
        grid_degrees=0.05,
        start_date=date(2001, 1, 1),
        end_date=date(2001, 1, 31),
        category=None,
        bbox=None,
    )
    assert sum(c.count for c in cells) > 0


def test_month_aligned_range_detection():
    from datetime import date

    assert rollup_repo.is_month_aligned_range(None, None) is True
    assert rollup_repo.is_month_aligned_range(date(2020, 1, 1), date(2020, 1, 31)) is True
    assert rollup_repo.is_month_aligned_range(date(2020, 2, 1), date(2020, 2, 29)) is True  # leap
    assert rollup_repo.is_month_aligned_range(date(2021, 1, 1), date(2021, 2, 28)) is True
    assert rollup_repo.is_month_aligned_range(date(2020, 1, 5), date(2020, 1, 31)) is False
    assert rollup_repo.is_month_aligned_range(date(2020, 1, 1), date(2020, 1, 15)) is False
    assert rollup_repo.is_month_aligned_range(date(2020, 1, 1), None) is False


def test_api_uses_rollup_for_eligible_request(db):
    _ingest_fixture_rows(db, "chicago-rollup-api-test")
    refresh_rollup(db)
    incidents_api._aggregate_cache.clear()

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get("/api/incidents/aggregate", params={"zoom": 8})
    finally:
        app.dependency_overrides.pop(get_db, None)
        incidents_api._aggregate_cache.clear()

    assert response.status_code == 200
    assert response.json()["source"] == "rollup"


def test_api_uses_rollup_with_an_explicit_month_aligned_date_range(db):
    """Regression test for the same bug as
    test_query_rollup_with_an_explicit_month_aligned_date_range, at the
    full HTTP level -- confirms a real user filtering the map by an
    exact month at a wide zoom gets a 200, not a 500."""
    _ingest_fixture_rows(db, "chicago-rollup-api-date-range-test")
    refresh_rollup(db)
    incidents_api._aggregate_cache.clear()

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get(
            "/api/incidents/aggregate",
            params={"zoom": 8, "start_date": "2001-01-01", "end_date": "2001-01-31"},
        )
    finally:
        app.dependency_overrides.pop(get_db, None)
        incidents_api._aggregate_cache.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "rollup"
    assert sum(c["count"] for c in body["cells"]) > 0


def test_api_falls_back_to_raw_for_neighborhood_filter(db):
    _ingest_fixture_rows(db, "chicago-rollup-fallback-neighborhood-test")
    refresh_rollup(db)
    incidents_api._aggregate_cache.clear()

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get("/api/incidents/aggregate", params={"zoom": 8, "neighborhood": "28"})
    finally:
        app.dependency_overrides.pop(get_db, None)
        incidents_api._aggregate_cache.clear()

    assert response.status_code == 200
    assert response.json()["source"] == "raw"


def test_api_falls_back_to_raw_for_non_month_aligned_dates(db):
    _ingest_fixture_rows(db, "chicago-rollup-fallback-date-test")
    refresh_rollup(db)
    incidents_api._aggregate_cache.clear()

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get(
            "/api/incidents/aggregate",
            params={"zoom": 8, "start_date": "2020-01-05", "end_date": "2020-01-20"},
        )
    finally:
        app.dependency_overrides.pop(get_db, None)
        incidents_api._aggregate_cache.clear()

    assert response.status_code == 200
    assert response.json()["source"] == "raw"


def test_api_falls_back_to_raw_for_fine_grid_zoom(db):
    _ingest_fixture_rows(db, "chicago-rollup-fallback-zoom-test")
    refresh_rollup(db)
    incidents_api._aggregate_cache.clear()

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get("/api/incidents/aggregate", params={"zoom": 14})
    finally:
        app.dependency_overrides.pop(get_db, None)
        incidents_api._aggregate_cache.clear()

    assert response.status_code == 200
    assert response.json()["source"] == "raw"


def test_failed_refresh_does_not_destroy_existing_rollup_data(db, monkeypatch):
    """A failed REFRESH must leave the previously-good rollup data
    intact and record the failure -- never silently succeed, never wipe
    the view (see docs/rollup-design.md "Refresh strategy")."""
    _ingest_fixture_rows(db, "chicago-rollup-failure-test")
    refresh_rollup(db)  # establish a real, good snapshot first
    before_count = db.execute(text("SELECT count(*) FROM incident_grid_rollup")).scalar_one()
    assert before_count > 0

    original_execute = db.execute

    def failing_execute(statement, *args, **kwargs):
        if "REFRESH MATERIALIZED VIEW" in str(statement):
            raise RuntimeError("simulated refresh failure")
        return original_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db, "execute", failing_execute)

    with pytest.raises(RuntimeError, match="simulated refresh failure"):
        refresh_rollup(db)

    monkeypatch.undo()  # restore real execute to inspect state afterward

    after_count = db.execute(text("SELECT count(*) FROM incident_grid_rollup")).scalar_one()
    assert after_count == before_count  # untouched by the failed refresh

    latest = get_latest_refresh(db)
    assert latest.status == RollupRefreshStatus.FAILED
    assert "simulated refresh failure" in latest.error_message


def test_rollup_refresh_runs_table_records_history(db):
    _ingest_fixture_rows(db, "chicago-rollup-history-test")
    before = db.execute(text("SELECT count(*) FROM rollup_refresh_runs")).scalar_one()
    refresh_rollup(db)
    refresh_rollup(db)
    after = db.execute(text("SELECT count(*) FROM rollup_refresh_runs")).scalar_one()
    assert after == before + 2

    all_runs = (
        db.execute(select(RollupRefreshRun).order_by(RollupRefreshRun.started_at.desc()).limit(2))
        .scalars()
        .all()
    )
    assert all(r.status == RollupRefreshStatus.COMPLETED for r in all_runs)
