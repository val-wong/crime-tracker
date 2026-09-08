"""Idempotency tests against the real ChicagoSourceAdapter, run through
the full ingestion pipeline against real Chicago-shaped rows (captured
once from the live API — see tests/fixtures/chicago_sample_rows.json).
Never calls Chicago's live API itself.
"""

import copy
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from app.adapters.chicago import ChicagoSourceAdapter
from app.models.incident import Incident
from app.models.offense import Offense
from app.models.raw_version import RawSourceRecordVersion
from app.models.source import Source
from app.models.source_record import SourceRecord
from app.services.ingestion import run_ingestion_cycle

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "chicago_sample_rows.json"


def _load_rows() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text())


def _make_source(db) -> Source:
    source = Source(source_key="chicago-idempotency-test", name="Chicago Idempotency Test")
    db.add(source)
    db.flush()
    return source


def _adapter_for(rows) -> ChicagoSourceAdapter:
    adapter = ChicagoSourceAdapter()
    adapter.fetch_current_records = lambda: iter(rows)
    return adapter


def _counts(db, source):
    n_source_records = db.execute(
        select(func.count()).select_from(SourceRecord).where(SourceRecord.source_id == source.id)
    ).scalar_one()
    n_incidents = db.execute(
        select(func.count()).select_from(Incident).where(Incident.source_id == source.id)
    ).scalar_one()
    n_offenses = db.execute(
        select(func.count())
        .select_from(Offense)
        .join(Incident, Offense.incident_id == Incident.id)
        .where(Incident.source_id == source.id)
    ).scalar_one()
    return n_source_records, n_incidents, n_offenses


def _version_count(db, source_record_id) -> int:
    return db.execute(
        select(func.count())
        .select_from(RawSourceRecordVersion)
        .where(RawSourceRecordVersion.source_record_id == source_record_id)
    ).scalar_one()


def test_first_run_creates_expected_records(db):
    rows = _load_rows()
    source = _make_source(db)
    with _adapter_for(rows) as adapter:
        result = run_ingestion_cycle(db, source=source, adapter=adapter, is_complete=False)

    n_source_records, n_incidents, n_offenses = _counts(db, source)
    assert result.run.records_created == len(rows)
    assert n_offenses == len(rows)  # one offense per raw row
    assert n_source_records == len(rows)
    # Fewer incidents than offenses: the multi-victim homicide case
    # (G023235, 2 rows) collapses to one incident.
    distinct_case_numbers = len({r["case_number"] for r in rows})
    assert n_incidents == distinct_case_numbers
    assert n_incidents < n_offenses


def test_identical_second_run_creates_no_duplicates_or_extra_versions(db):
    rows = _load_rows()
    source = _make_source(db)
    with _adapter_for(rows) as adapter:
        run_ingestion_cycle(db, source=source, adapter=adapter, is_complete=False)
    before = _counts(db, source)

    with _adapter_for(rows) as adapter:
        result = run_ingestion_cycle(db, source=source, adapter=adapter, is_complete=False)

    after = _counts(db, source)
    assert after == before  # no duplicate source records/incidents/offenses
    assert result.run.records_created == 0
    assert result.run.records_unchanged == len(rows)
    assert result.run.records_changed == 0

    # No unnecessary raw versions.
    record = db.execute(
        select(SourceRecord).where(
            SourceRecord.source_id == source.id, SourceRecord.external_record_id == rows[0]["id"]
        )
    ).scalar_one()
    assert _version_count(db, record.id) == 1


def test_changed_record_creates_exactly_one_new_version(db):
    rows = _load_rows()
    source = _make_source(db)
    with _adapter_for(rows) as adapter:
        run_ingestion_cycle(db, source=source, adapter=adapter, is_complete=False)

    changed_rows = copy.deepcopy(rows)
    changed_rows[0]["primary_type"] = "BATTERY"  # a genuine substantive change
    changed_rows[0]["description"] = "AGGRAVATED"
    with _adapter_for(changed_rows) as adapter:
        result = run_ingestion_cycle(db, source=source, adapter=adapter, is_complete=False)

    assert result.run.records_changed == 1
    assert result.run.records_unchanged == len(rows) - 1

    record = db.execute(
        select(SourceRecord).where(
            SourceRecord.source_id == source.id,
            SourceRecord.external_record_id == rows[0]["id"],
        )
    ).scalar_one()
    assert _version_count(db, record.id) == 2

    offense = db.execute(select(Offense).where(Offense.source_record_id == record.id)).scalar_one()
    assert offense.source_category == "BATTERY"


def test_updated_on_only_bulk_touch_creates_no_new_version(db):
    """A confirmed real-world pattern (docs/sources/chicago.md §3-4):
    many rows can have their `updated_on` bumped by a bulk/administrative
    touch with no substantive field actually changing. Since
    `updated_on` is deliberately excluded from the fingerprint (see
    ChicagoSourceAdapter.FINGERPRINT_FIELDS), this must not create a
    new raw version.
    """
    rows = _load_rows()
    source = _make_source(db)
    with _adapter_for(rows) as adapter:
        run_ingestion_cycle(db, source=source, adapter=adapter, is_complete=False)

    touched_rows = copy.deepcopy(rows)
    for row in touched_rows:
        row["updated_on"] = "2030-01-01T00:00:00.000"  # only this changes

    with _adapter_for(touched_rows) as adapter:
        result = run_ingestion_cycle(db, source=source, adapter=adapter, is_complete=False)

    assert result.run.records_changed == 0
    assert result.run.records_unchanged == len(rows)

    record = db.execute(
        select(SourceRecord).where(
            SourceRecord.source_id == source.id, SourceRecord.external_record_id == rows[0]["id"]
        )
    ).scalar_one()
    assert _version_count(db, record.id) == 1  # still just the original version


def test_multi_offense_incident_maps_correctly(db):
    rows = [r for r in _load_rows() if r["case_number"] == "G023235"]
    assert len(rows) == 2
    source = _make_source(db)
    with _adapter_for(rows) as adapter:
        run_ingestion_cycle(db, source=source, adapter=adapter, is_complete=False)

    incident = db.execute(
        select(Incident).where(Incident.external_incident_id == "G023235")
    ).scalar_one()
    offenses = db.execute(select(Offense).where(Offense.incident_id == incident.id)).scalars().all()
    assert len(offenses) == 2
    assert {o.external_offense_id for o in offenses} == {"650", "651"}
    assert all(o.victim_count == 1 for o in offenses)


def test_homicide_victim_rows_do_not_inflate_incident_count(db):
    rows = _load_rows()
    homicide_rows = [r for r in rows if r["case_number"] == "G023235"]
    assert len(homicide_rows) == 2
    source = _make_source(db)
    with _adapter_for(rows) as adapter:
        run_ingestion_cycle(db, source=source, adapter=adapter, is_complete=False)

    n_incidents_for_case = db.execute(
        select(func.count())
        .select_from(Incident)
        .where(Incident.source_id == source.id, Incident.external_incident_id == "G023235")
    ).scalar_one()
    assert n_incidents_for_case == 1  # not 2


def test_geography_round_trips_through_postgis(db):
    """Spatial round-trip with real Chicago coordinates -- confirms the
    PostGIS geography column stores what the adapter parsed, and that
    it's tagged BLOCK (never EXACT) per docs/sources/chicago.md §8.
    """
    rows = _load_rows()
    row = rows[0]
    source = _make_source(db)
    with _adapter_for([row]) as adapter:
        run_ingestion_cycle(db, source=source, adapter=adapter, is_complete=False)

    incident = db.execute(
        select(Incident).where(Incident.external_incident_id == row["case_number"])
    ).scalar_one()

    lon, lat = db.execute(
        text(
            "SELECT ST_X(location::geometry), ST_Y(location::geometry) "
            "FROM incidents WHERE id = :id"
        ),
        {"id": incident.id},
    ).one()
    assert lon == pytest.approx(float(row["longitude"]), abs=1e-6)
    assert lat == pytest.approx(float(row["latitude"]), abs=1e-6)
    assert incident.location_precision.value == "block"
