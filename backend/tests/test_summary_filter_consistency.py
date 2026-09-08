"""Regression tests for a real filter-consistency bug found during
manual browser QA: `/api/summary` applied the committed
category/neighborhood filters inconsistently across its different
sub-calculations.

Reproduction that surfaced it: `category=NARCOTICS&neighborhood=40`
(Washington Park) with an explicit date range. `reported_incidents`
(and the map) correctly showed a small filtered count, but
`most_common_category` said BATTERY, `most_represented_neighborhood`
said Humboldt Park (23), and the category breakdown listed categories
other than NARCOTICS with counts far exceeding the filtered total.

Root cause (see app/services/summary.py and
app/repositories/summary.py):

1. `_category_breakdown` never threaded `category` through to either
   the raw query or the rollup-eligibility check at all --
   `summary_repo.category_breakdown` hardcoded `category=None`, so the
   breakdown (and therefore `most_common_category`, which is just
   `category_breakdown[0]`) always reflected every category in range,
   never just the selected one.
2. `top_neighborhoods_by_incident_count` never accepted or applied a
   `neighborhood` argument at all -- `get_summary` never passed one,
   and the repository function hardcoded `neighborhood=None` -- so
   `top_neighborhoods` (and `most_represented_neighborhood`) always
   ranked across every neighborhood citywide, never just the selected
   one.

This file ingests a small, self-contained synthetic dataset (not the
shared `chicago_sample_rows.json` fixture, so exact expected counts
stay hand-verifiable) covering both community areas named in the
reported bug (40 = Washington Park, 23 = Humboldt Park) and both
categories (NARCOTICS, BATTERY), plus a third category (CRIMINAL
DAMAGE) and an out-of-range row, across every filter combination in
the task.
"""

from datetime import date
from typing import Optional

from fastapi.testclient import TestClient

from app.adapters.chicago import ChicagoSourceAdapter
from app.constants import CHICAGO_SOURCE_KEY
from app.db.session import get_db
from app.main import app
from app.models.source import Source
from app.services.ingestion import run_ingestion_cycle

START_DATE = "2026-08-01"
END_DATE = "2026-08-29"

WASHINGTON_PARK = "40"
HUMBOLDT_PARK = "23"


def _row(
    record_id: str, case_number: str, occurred_on: str, primary_type: str, community_area: str
):
    timestamp = f"{occurred_on}T12:00:00.000"
    return {
        "id": record_id,
        "case_number": case_number,
        "date": timestamp,
        "updated_on": timestamp,
        "iucr": "0000",
        "primary_type": primary_type,
        "community_area": community_area,
    }


def _build_rows() -> list[dict]:
    rows = []
    # Washington Park (40), in range: 4 NARCOTICS, 2 BATTERY, 1 CRIMINAL
    # DAMAGE -- 7 incidents total, matching the "4" from the reported
    # bug for the NARCOTICS-only slice.
    for i, day in enumerate(["02", "09", "16", "23"], start=1):
        rows.append(_row(f"9010{i}", f"W-NARC-{i}", f"2026-08-{day}", "NARCOTICS", WASHINGTON_PARK))
    for i, day in enumerate(["05", "12"], start=1):
        rows.append(_row(f"9020{i}", f"W-BATT-{i}", f"2026-08-{day}", "BATTERY", WASHINGTON_PARK))
    rows.append(_row("90301", "W-DMG-1", "2026-08-19", "CRIMINAL DAMAGE", WASHINGTON_PARK))

    # Humboldt Park (23), in range: 5 NARCOTICS (deliberately *more*
    # than Washington Park's 4, so the pre-fix bug's citywide ranking
    # would have picked this neighborhood even when neighborhood=40 was
    # explicitly selected) + 1 BATTERY -- 6 incidents total.
    for i, day in enumerate(["03", "08", "13", "18", "24"], start=1):
        rows.append(_row(f"9040{i}", f"H-NARC-{i}", f"2026-08-{day}", "NARCOTICS", HUMBOLDT_PARK))
    rows.append(_row("90501", "H-BATT-1", "2026-08-27", "BATTERY", HUMBOLDT_PARK))

    # Out of range entirely (before START_DATE) -- must never be
    # counted by any filter combination below.
    rows.append(_row("90601", "OUT-OF-RANGE", "2026-07-01", "NARCOTICS", WASHINGTON_PARK))

    # A third category/neighborhood, dated exactly END_DATE, purely to
    # anchor this source's latest loaded occurrence date at END_DATE --
    # without it, the latest row above (2026-08-27) would make the
    # backend clamp end_date down from 2026-08-29 (see
    # app/services/summary.py::get_summary's "Explicit future dates"
    # handling), which is real, correct, pre-existing behavior but not
    # what this file is testing. Distinct enough (THEFT, Rogers Park)
    # to never affect any other assertion below except the two
    # citywide/no-filter totals, which account for it explicitly.
    rows.append(_row("90701", "ANCHOR-LATEST-DATE", END_DATE, "THEFT", "1"))

    return rows


def _ingest(db) -> Source:
    source = Source(source_key=CHICAGO_SOURCE_KEY, name="Filter Consistency Test Fixture")
    db.add(source)
    db.flush()

    adapter = ChicagoSourceAdapter()
    adapter.fetch_current_records = lambda: iter(_build_rows())
    try:
        run_ingestion_cycle(db, source=source, adapter=adapter, is_complete=False)
    finally:
        adapter.close()
    db.commit()
    return source


def _get_summary(db, **params) -> dict:
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get("/api/summary", params=params)
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert response.status_code == 200
    return response.json()


def _assert_no_metric_contradicts_filters(
    body: dict, *, category: Optional[str], neighborhood: Optional[str]
):
    """Generic invariants that must hold no matter which filters were
    applied -- the actual assertions from task 8."""
    if category is not None:
        # Every breakdown row must be for the selected category -- not
        # "contains at least" but "contains only", since this dataset
        # has no multi-offense incidents to legitimately produce a
        # second row.
        for row in body["category_breakdown"]:
            assert row["category"] == category, body["category_breakdown"]
        if body["category_breakdown"]:
            assert body["most_common_category"] == category
        # Breakdown counts can never exceed the correctly filtered
        # population -- this is exactly the "52, 26, ... exceeding 4"
        # symptom from the bug report.
        for row in body["category_breakdown"]:
            assert row["count"] <= body["reported_incidents"]

    if neighborhood is not None:
        for row in body["top_neighborhoods"]:
            assert row["neighborhood"] == neighborhood, body["top_neighborhoods"]
        if body["top_neighborhoods"]:
            assert body["most_represented_neighborhood"] == neighborhood


def test_category_and_neighborhood_together_matches_the_reported_bug(db):
    """The exact combination from the bug report: category=NARCOTICS,
    neighborhood=40, explicit date range."""
    _ingest(db)
    body = _get_summary(
        db,
        start_date=START_DATE,
        end_date=END_DATE,
        category="NARCOTICS",
        neighborhood=WASHINGTON_PARK,
    )

    assert body["reported_incidents"] == 4
    assert body["category_breakdown"] == [{"category": "NARCOTICS", "count": 4}]
    assert body["most_common_category"] == "NARCOTICS"
    assert body["top_neighborhoods"] == [
        {"neighborhood": "40", "name": "Washington Park", "count": 4}
    ]
    assert body["most_represented_neighborhood"] == "40"
    assert body["most_represented_neighborhood_name"] == "Washington Park"
    # The exact contradictions the bug report called out, made explicit:
    assert body["most_common_category"] != "BATTERY"
    assert body["most_represented_neighborhood"] != HUMBOLDT_PARK
    assert sum(t["count"] for t in body["incidents_by_time"]) == 4

    _assert_no_metric_contradicts_filters(body, category="NARCOTICS", neighborhood=WASHINGTON_PARK)


def test_all_filters_together_with_explicit_dates(db):
    _ingest(db)
    body = _get_summary(
        db,
        start_date=START_DATE,
        end_date=END_DATE,
        category="NARCOTICS",
        neighborhood=WASHINGTON_PARK,
    )
    assert body["reported_incidents"] == 4
    _assert_no_metric_contradicts_filters(body, category="NARCOTICS", neighborhood=WASHINGTON_PARK)


def test_category_only(db):
    _ingest(db)
    body = _get_summary(db, start_date=START_DATE, end_date=END_DATE, category="NARCOTICS")

    # 4 in Washington Park + 5 in Humboldt Park, citywide.
    assert body["reported_incidents"] == 9
    assert body["category_breakdown"] == [{"category": "NARCOTICS", "count": 9}]
    assert body["most_common_category"] == "NARCOTICS"
    # No neighborhood filter -- Humboldt Park (5) legitimately outranks
    # Washington Park (4) for NARCOTICS specifically. This is a correct
    # global answer, not the bug (the bug was ranking citywide *despite*
    # an explicit neighborhood filter -- see the combined test above).
    assert body["most_represented_neighborhood"] == HUMBOLDT_PARK

    _assert_no_metric_contradicts_filters(body, category="NARCOTICS", neighborhood=None)


def test_neighborhood_only(db):
    _ingest(db)
    body = _get_summary(db, start_date=START_DATE, end_date=END_DATE, neighborhood=WASHINGTON_PARK)

    assert body["reported_incidents"] == 7  # 4 NARCOTICS + 2 BATTERY + 1 CRIMINAL DAMAGE
    breakdown = {row["category"]: row["count"] for row in body["category_breakdown"]}
    assert breakdown == {"NARCOTICS": 4, "BATTERY": 2, "CRIMINAL DAMAGE": 1}
    assert sum(breakdown.values()) == body["reported_incidents"]
    assert body["most_common_category"] == "NARCOTICS"
    assert body["most_represented_neighborhood"] == WASHINGTON_PARK

    _assert_no_metric_contradicts_filters(body, category=None, neighborhood=WASHINGTON_PARK)


def test_date_only(db):
    _ingest(db)
    body = _get_summary(db, start_date=START_DATE, end_date=END_DATE)

    # 7 (Washington Park) + 6 (Humboldt Park) + 1 (the Rogers Park
    # anchor row, dated END_DATE and therefore in range).
    assert body["reported_incidents"] == 14
    breakdown = {row["category"]: row["count"] for row in body["category_breakdown"]}
    assert breakdown == {"NARCOTICS": 9, "BATTERY": 3, "CRIMINAL DAMAGE": 1, "THEFT": 1}
    assert sum(breakdown.values()) == body["reported_incidents"]
    # Washington Park (7) outranks Humboldt Park (6) once *all*
    # categories are counted, even though Humboldt Park had more
    # NARCOTICS specifically -- see test_category_only above.
    assert body["most_represented_neighborhood"] == WASHINGTON_PARK

    _assert_no_metric_contradicts_filters(body, category=None, neighborhood=None)


def test_no_filters_uses_the_default_period_but_stays_internally_consistent(db):
    """No query params at all -- the backend's own default period
    (anchored to the latest loaded occurrence date, END_DATE here)
    covers exactly this dataset's in-range rows, so the numbers match
    the explicit-date-range test above."""
    _ingest(db)
    body = _get_summary(db)

    assert body["reported_incidents"] == 14
    breakdown = {row["category"]: row["count"] for row in body["category_breakdown"]}
    assert sum(breakdown.values()) == body["reported_incidents"]

    _assert_no_metric_contradicts_filters(body, category=None, neighborhood=None)


def test_category_and_neighborhood_without_explicit_dates(db):
    """Same combined filter as the bug report, but relying on the
    default period instead of an explicit range."""
    _ingest(db)
    body = _get_summary(db, category="NARCOTICS", neighborhood=WASHINGTON_PARK)

    assert body["reported_incidents"] == 4
    assert body["most_common_category"] == "NARCOTICS"
    assert body["most_represented_neighborhood"] == WASHINGTON_PARK

    _assert_no_metric_contradicts_filters(body, category="NARCOTICS", neighborhood=WASHINGTON_PARK)


def test_distinct_incident_counting_preserved_for_multi_offense_homicide_rows(db):
    """Regression guard for task 6: this fix must not change
    `reported_incidents`' *distinct-incident* counting convention for
    the documented multi-victim-homicide pattern (two source rows,
    same case_number -- see docs/sources/chicago.md §2), even though
    `category_breakdown` legitimately counts *offenses* and so shows 2
    for the same case."""
    source = Source(source_key=CHICAGO_SOURCE_KEY, name="Multi-Offense Homicide Test")
    db.add(source)
    db.flush()

    rows = [
        _row("70001", "HOMICIDE-CASE-1", "2026-08-10", "HOMICIDE", WASHINGTON_PARK),
        # Same case_number, second victim -- one Incident, two Offenses
        # (see app/adapters/chicago.py::normalize_incident_offense).
        {**_row("70002", "HOMICIDE-CASE-1", "2026-08-10", "HOMICIDE", WASHINGTON_PARK)},
    ]
    adapter = ChicagoSourceAdapter()
    adapter.fetch_current_records = lambda: iter(rows)
    try:
        run_ingestion_cycle(db, source=source, adapter=adapter, is_complete=False)
    finally:
        adapter.close()
    db.commit()

    body = _get_summary(
        db,
        start_date=START_DATE,
        end_date=END_DATE,
        category="HOMICIDE",
        neighborhood=WASHINGTON_PARK,
    )

    # One incident (two victims), not two.
    assert body["reported_incidents"] == 1
    # The offense-level breakdown legitimately counts both offense rows.
    assert body["category_breakdown"] == [{"category": "HOMICIDE", "count": 2}]
    assert body["most_common_category"] == "HOMICIDE"
    assert body["most_represented_neighborhood"] == WASHINGTON_PARK


def test_reported_incidents_matches_the_bug_reports_map_count(db):
    """The bug report specifically noted the map and reported_incidents
    already agreed (both said 4) -- this pins that down explicitly so a
    future change can't silently break that agreement while fixing
    something else."""
    _ingest(db)
    body = _get_summary(
        db,
        start_date=START_DATE,
        end_date=END_DATE,
        category="NARCOTICS",
        neighborhood=WASHINGTON_PARK,
    )
    assert body["reported_incidents"] == 4
    assert body["start_date"] == date(2026, 8, 1).isoformat()
    assert body["end_date"] == date(2026, 8, 29).isoformat()
