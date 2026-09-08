"""Regression tests for real date-semantics bugs found during browser
QA (see docs/... "Default period anchoring" and "Explicit user-selected
future dates"):

1. The default summary period was anchored to `date.today()` instead of
   the source's own latest loaded occurrence date -- since a live
   source always lags "now", this silently counted zero-data days as
   part of the "current" period and made the previous-period comparison
   look artificially negative.
2. An explicit request whose end_date reached past the source's real
   coverage was not clamped -- it silently reported those unloaded days
   as zero incidents, which reads as "confirmed zero crime" rather than
   "not ingested yet".
3. Trend buckets never flagged a final bucket whose natural week/month
   span extends past the source's real coverage.

Uses the real Chicago-shaped fixture rows (never the live API) -- see
tests/fixtures/chicago_sample_rows.json, whose latest `date` field is
2023-09-06: verified directly against the fixture file, and years
behind "today" in any real test run, which is exactly what makes it a
solid fixture for these tests -- it proves the default period anchors
to the *data*, not the wall clock.
"""

import json
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.adapters.chicago import ChicagoSourceAdapter
from app.constants import CHICAGO_SOURCE_KEY
from app.db.session import get_db
from app.main import app
from app.models.source import Source
from app.services.ingestion import run_ingestion_cycle

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "chicago_sample_rows.json"
LATEST_FIXTURE_DATE = date(2023, 9, 6)


def _ingest_fixture_rows(db) -> Source:
    rows = json.loads(FIXTURE_PATH.read_text())
    source = Source(source_key=CHICAGO_SOURCE_KEY, name="Chicago Date-Semantics Test Fixture")
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


def test_default_period_anchors_to_latest_source_date_not_today(db):
    _ingest_fixture_rows(db)

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get("/api/summary")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    body = response.json()
    assert body["end_date"] == LATEST_FIXTURE_DATE.isoformat()
    assert body["start_date"] == (LATEST_FIXTURE_DATE - timedelta(days=30)).isoformat()
    # The whole point of this test: it must not be anchored to today's
    # real date (this test suite runs years after the fixture's data).
    assert body["end_date"] != date.today().isoformat()


def test_previous_period_is_equal_length_and_immediately_preceding(db):
    _ingest_fixture_rows(db)

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get("/api/summary")
    finally:
        app.dependency_overrides.pop(get_db, None)

    body = response.json()
    start = date.fromisoformat(body["start_date"])
    end = date.fromisoformat(body["end_date"])
    prev_start = date.fromisoformat(body["previous_period_start_date"])
    prev_end = date.fromisoformat(body["previous_period_end_date"])

    assert prev_end == start - timedelta(days=1)  # immediately preceding, no gap or overlap
    assert (prev_end - prev_start).days == (end - start).days  # equal length


def test_explicit_future_range_is_clamped_to_latest_available_date(db):
    _ingest_fixture_rows(db)

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        clamped = client.get(
            "/api/summary", params={"start_date": "2023-08-01", "end_date": "2023-12-31"}
        )
        unclamped = client.get(
            "/api/summary",
            params={"start_date": "2023-08-01", "end_date": LATEST_FIXTURE_DATE.isoformat()},
        )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert clamped.status_code == 200
    clamped_body = clamped.json()
    # The analyzed end_date is pulled back to the real data, not left as
    # the requested 2023-12-31 -- the response never implies those
    # unloaded days were checked and found empty.
    assert clamped_body["end_date"] == LATEST_FIXTURE_DATE.isoformat()
    assert clamped_body["end_date_clamped"] is True
    # Clamping must not change the actual count -- it's the same real
    # analyzed period either way, just requested differently.
    assert clamped_body["reported_incidents"] == unclamped.json()["reported_incidents"]


def test_explicit_range_within_coverage_is_not_clamped(db):
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
    assert body["end_date"] == "2001-01-11"  # echoed exactly, never silently altered
    assert body["end_date_clamped"] is False


def test_partial_final_trend_bucket_is_flagged(db):
    _ingest_fixture_rows(db)

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get("/api/summary")
    finally:
        app.dependency_overrides.pop(get_db, None)

    body = response.json()
    assert body["time_bucket"] == "week"  # the default 30-day span uses weekly buckets
    buckets = body["incidents_by_time"]
    assert len(buckets) > 0

    last = buckets[-1]
    last_start = date.fromisoformat(last["bucket_start"])
    natural_end = last_start + timedelta(days=6)  # a week bucket spans 7 days
    expected_partial = natural_end > LATEST_FIXTURE_DATE
    # True for this fixture's real latest date (2023-09-06 is a
    # Wednesday, so its week's natural end is the following Sunday) --
    # asserted explicitly so this test would fail loudly, not silently
    # pass vacuously, if that ever stopped being the case.
    assert expected_partial is True
    assert last["is_partial"] is expected_partial

    for bucket in buckets[:-1]:
        assert bucket["is_partial"] is False
