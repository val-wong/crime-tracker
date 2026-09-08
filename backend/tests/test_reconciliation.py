"""Reconciliation state-machine tests, run against synthetic fixture data only.

Covers every case in docs/ingestion-framework.md: new, unchanged,
changed, missing (complete run only), confirmed removal after N
consecutive complete misses, reappearance, and partial-run safety.
"""

from sqlalchemy import func, select

from app.adapters.fixture import FixtureSourceAdapter
from app.models.enums import RemovalConfidence
from app.models.raw_version import RawSourceRecordVersion
from app.models.source import Source
from app.models.source_record import SourceRecord
from app.services.ingestion import run_ingestion_cycle

REMOVAL_CONFIRMATION_RUNS = 2


def _make_source(db, key="fixture-test-source") -> Source:
    source = Source(source_key=key, name="Fixture Test Source")
    db.add(source)
    db.flush()
    return source


def _record(external_id, **overrides):
    base = {
        "external_record_id": external_id,
        "external_incident_id": f"INC-{external_id}",
        "category": "theft",
        "subcategory": "theft-bicycle",
        "offense_code": "2399",
        "occurred_at": "2026-01-01T12:00:00+00:00",
        "reported_at": "2026-01-01T13:00:00+00:00",
        "address_text": "100 BLK MAIN ST",
        "latitude": 39.7,
        "longitude": -104.9,
        "location_precision": "block",
        "neighborhood": "downtown",
        "district": "1",
        "victim_count": 1,
    }
    base.update(overrides)
    return base


def _source_record(db, source, external_id) -> SourceRecord:
    return db.execute(
        select(SourceRecord).where(
            SourceRecord.source_id == source.id, SourceRecord.external_record_id == external_id
        )
    ).scalar_one()


def _version_count(db, source_record_id) -> int:
    return db.execute(
        select(func.count())
        .select_from(RawSourceRecordVersion)
        .where(RawSourceRecordVersion.source_record_id == source_record_id)
    ).scalar_one()


def test_new_record_is_created_and_versioned(db):
    source = _make_source(db)
    adapter = FixtureSourceAdapter([_record("A-1")])

    result = run_ingestion_cycle(
        db,
        source=source,
        adapter=adapter,
        is_complete=True,
        removal_confirmation_runs=REMOVAL_CONFIRMATION_RUNS,
    )

    assert result.run.records_created == 1
    assert result.run.records_received == 1
    record = _source_record(db, source, "A-1")
    assert record.source_active is True
    assert record.first_seen_at == record.last_seen_at
    assert _version_count(db, record.id) == 1


def test_unchanged_record_creates_no_new_version(db):
    source = _make_source(db)
    adapter = FixtureSourceAdapter([_record("A-1")])

    run_ingestion_cycle(
        db, source=source, adapter=adapter, is_complete=True, removal_confirmation_runs=2
    )
    record_after_first = _source_record(db, source, "A-1")
    first_seen = record_after_first.first_seen_at
    last_seen_after_first = record_after_first.last_seen_at

    # Second, identical run.
    result2 = run_ingestion_cycle(
        db, source=source, adapter=adapter, is_complete=True, removal_confirmation_runs=2
    )

    record = _source_record(db, source, "A-1")
    assert result2.run.records_unchanged == 1
    assert result2.run.records_created == 0
    assert _version_count(db, record.id) == 1  # still exactly one version
    assert record.first_seen_at == first_seen  # unchanged
    assert record.last_seen_at >= last_seen_after_first  # advanced (or equal, if same instant)


def test_changed_record_creates_exactly_one_new_version(db):
    source = _make_source(db)
    adapter_v1 = FixtureSourceAdapter([_record("A-1", category="theft")])
    run_ingestion_cycle(
        db, source=source, adapter=adapter_v1, is_complete=True, removal_confirmation_runs=2
    )

    adapter_v2 = FixtureSourceAdapter([_record("A-1", category="burglary")])
    result2 = run_ingestion_cycle(
        db, source=source, adapter=adapter_v2, is_complete=True, removal_confirmation_runs=2
    )

    record = _source_record(db, source, "A-1")
    assert result2.run.records_changed == 1
    assert _version_count(db, record.id) == 2

    versions = (
        db.execute(
            select(RawSourceRecordVersion)
            .where(RawSourceRecordVersion.source_record_id == record.id)
            .order_by(RawSourceRecordVersion.observed_at)
        )
        .scalars()
        .all()
    )
    assert versions[0].raw_payload["category"] == "theft"
    assert versions[1].raw_payload["category"] == "burglary"
    assert record.current_fingerprint == versions[1].fingerprint


def test_complete_run_flags_missing_record_without_deleting_it(db):
    source = _make_source(db)
    run_ingestion_cycle(
        db,
        source=source,
        adapter=FixtureSourceAdapter([_record("A-1")]),
        is_complete=True,
        removal_confirmation_runs=2,
    )

    # A-1 is now absent from a complete fetch.
    result = run_ingestion_cycle(
        db,
        source=source,
        adapter=FixtureSourceAdapter([]),
        is_complete=True,
        removal_confirmation_runs=2,
    )

    record = _source_record(db, source, "A-1")
    assert result.run.records_missing == 1
    assert record.source_active is True  # not yet confirmed removed
    assert record.consecutive_missing_runs == 1
    assert record.removal_confidence == RemovalConfidence.PENDING


def test_partial_run_does_not_accumulate_missing_evidence(db):
    source = _make_source(db)
    run_ingestion_cycle(
        db,
        source=source,
        adapter=FixtureSourceAdapter([_record("A-1")]),
        is_complete=True,
        removal_confirmation_runs=2,
    )

    # A-1 absent, but this run is explicitly partial/failed.
    result = run_ingestion_cycle(
        db,
        source=source,
        adapter=FixtureSourceAdapter([]),
        is_complete=False,
        removal_confirmation_runs=2,
        error_message="simulated network failure mid-pagination",
    )

    record = _source_record(db, source, "A-1")
    assert result.run.records_missing == 0  # missing detection was skipped entirely
    assert record.source_active is True
    assert record.consecutive_missing_runs == 0
    assert record.removal_confidence == RemovalConfidence.NONE


def test_confirmed_removal_after_n_consecutive_complete_misses(db):
    source = _make_source(db)
    run_ingestion_cycle(
        db,
        source=source,
        adapter=FixtureSourceAdapter([_record("A-1")]),
        is_complete=True,
        removal_confirmation_runs=2,
    )

    # First complete miss: pending, still active.
    run_ingestion_cycle(
        db,
        source=source,
        adapter=FixtureSourceAdapter([]),
        is_complete=True,
        removal_confirmation_runs=2,
    )
    record = _source_record(db, source, "A-1")
    assert record.source_active is True
    assert record.removal_confidence == RemovalConfidence.PENDING

    # Second complete miss: reaches the configured threshold (2) -> confirmed removed.
    run_ingestion_cycle(
        db,
        source=source,
        adapter=FixtureSourceAdapter([]),
        is_complete=True,
        removal_confirmation_runs=2,
    )
    record = _source_record(db, source, "A-1")
    assert record.source_active is False
    assert record.removal_confidence == RemovalConfidence.CONFIRMED
    assert record.source_removed_at is not None


def test_a_partial_run_in_the_middle_does_not_count_toward_the_threshold(db):
    source = _make_source(db)
    run_ingestion_cycle(
        db,
        source=source,
        adapter=FixtureSourceAdapter([_record("A-1")]),
        is_complete=True,
        removal_confirmation_runs=2,
    )

    run_ingestion_cycle(
        db,
        source=source,
        adapter=FixtureSourceAdapter([]),
        is_complete=True,
        removal_confirmation_runs=2,
    )
    # A partial run in between must not advance the counter.
    run_ingestion_cycle(
        db,
        source=source,
        adapter=FixtureSourceAdapter([]),
        is_complete=False,
        removal_confirmation_runs=2,
        error_message="boom",
    )
    record = _source_record(db, source, "A-1")
    assert record.consecutive_missing_runs == 1  # still just the one complete miss
    assert record.source_active is True


def test_reappearance_reactivates_and_preserves_removal_history(db):
    source = _make_source(db)
    run_ingestion_cycle(
        db,
        source=source,
        adapter=FixtureSourceAdapter([_record("A-1")]),
        is_complete=True,
        removal_confirmation_runs=2,
    )
    run_ingestion_cycle(
        db,
        source=source,
        adapter=FixtureSourceAdapter([]),
        is_complete=True,
        removal_confirmation_runs=2,
    )
    run_ingestion_cycle(
        db,
        source=source,
        adapter=FixtureSourceAdapter([]),
        is_complete=True,
        removal_confirmation_runs=2,
    )

    record = _source_record(db, source, "A-1")
    assert record.source_active is False
    removed_at_before_reappearance = record.source_removed_at
    assert removed_at_before_reappearance is not None

    # A-1 reappears.
    result = run_ingestion_cycle(
        db,
        source=source,
        adapter=FixtureSourceAdapter([_record("A-1")]),
        is_complete=True,
        removal_confirmation_runs=2,
    )

    record = _source_record(db, source, "A-1")
    assert record.source_active is True
    assert record.consecutive_missing_runs == 0
    assert record.removal_confidence == RemovalConfidence.NONE
    # Historical removal marker preserved, not cleared:
    assert record.source_removed_at == removed_at_before_reappearance
    # Same fingerprint as before removal -> no new version needed.
    assert result.outcomes[0].kind == "unchanged"
    assert result.outcomes[0].reappeared is True


def test_reappearance_with_changed_data_creates_new_version(db):
    source = _make_source(db)
    run_ingestion_cycle(
        db,
        source=source,
        adapter=FixtureSourceAdapter([_record("A-1", category="theft")]),
        is_complete=True,
        removal_confirmation_runs=2,
    )
    run_ingestion_cycle(
        db,
        source=source,
        adapter=FixtureSourceAdapter([]),
        is_complete=True,
        removal_confirmation_runs=2,
    )
    run_ingestion_cycle(
        db,
        source=source,
        adapter=FixtureSourceAdapter([]),
        is_complete=True,
        removal_confirmation_runs=2,
    )

    result = run_ingestion_cycle(
        db,
        source=source,
        adapter=FixtureSourceAdapter([_record("A-1", category="burglary")]),
        is_complete=True,
        removal_confirmation_runs=2,
    )

    record = _source_record(db, source, "A-1")
    assert record.source_active is True
    assert result.outcomes[0].kind == "changed"
    assert result.outcomes[0].reappeared is True
    assert _version_count(db, record.id) == 2


def test_raw_payload_is_preserved_verbatim(db):
    source = _make_source(db)
    raw = _record("A-1", category="theft", victim_count=3)
    run_ingestion_cycle(
        db,
        source=source,
        adapter=FixtureSourceAdapter([raw]),
        is_complete=True,
        removal_confirmation_runs=2,
    )

    record = _source_record(db, source, "A-1")
    version = db.execute(
        select(RawSourceRecordVersion).where(RawSourceRecordVersion.source_record_id == record.id)
    ).scalar_one()
    assert version.raw_payload["category"] == "theft"
    assert version.raw_payload["victim_count"] == 3
    assert version.raw_payload["external_record_id"] == "A-1"


def test_first_seen_and_last_seen_tracked_correctly(db):
    source = _make_source(db)
    run_ingestion_cycle(
        db,
        source=source,
        adapter=FixtureSourceAdapter([_record("A-1")]),
        is_complete=True,
        removal_confirmation_runs=2,
    )
    record = _source_record(db, source, "A-1")
    first_seen = record.first_seen_at
    assert record.last_seen_at == first_seen

    run_ingestion_cycle(
        db,
        source=source,
        adapter=FixtureSourceAdapter([_record("A-1")]),
        is_complete=True,
        removal_confirmation_runs=2,
    )
    record = _source_record(db, source, "A-1")
    assert record.first_seen_at == first_seen  # never changes after creation
    assert record.last_seen_at >= first_seen


def test_end_to_end_synthetic_workflow_creates_incident_and_offense(db):
    source = _make_source(db)
    adapter = FixtureSourceAdapter([_record("A-1")])
    result = run_ingestion_cycle(
        db, source=source, adapter=adapter, is_complete=True, removal_confirmation_runs=2
    )

    assert result.run.records_created == 1
    from app.models.incident import Incident
    from app.models.offense import Offense

    incident = db.execute(
        select(Incident).where(Incident.external_incident_id == "INC-A-1")
    ).scalar_one()
    offense = db.execute(select(Offense).where(Offense.incident_id == incident.id)).scalar_one()
    assert offense.source_category == "theft"
    assert offense.external_offense_id == "A-1"
