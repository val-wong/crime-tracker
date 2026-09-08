"""Production-readiness hardening: checkpointed transactions for
reconciliation (see docs/reconciliation-transactions.md).

Covers:
- checkpoints actually commit partial progress (not held in one
  multi-hour transaction),
- a mid-run failure leaves already-checkpointed batches durable,
- a failed run never applies missing-record/deletion evidence,
- a failed run never moves the source's trusted baseline,
- a subsequent full re-run after a failure is safe (idempotent).

Uses the synthetic FixtureSourceAdapter, never the live Chicago API.
"""

from collections.abc import Iterator, Mapping
from typing import Any

import pytest
from sqlalchemy import select

from app.adapters.fixture import FixtureSourceAdapter
from app.models.enums import IngestionRunStatus, RemovalConfidence
from app.models.ingestion_run import IngestionRun
from app.models.source import Source
from app.models.source_record import SourceRecord
from app.services.ingestion import run_ingestion_cycle


class _FailAfterNAdapter(FixtureSourceAdapter):
    """Yields its records normally, then raises partway through -- used
    to simulate a real-world crash/network failure mid-reconciliation
    without needing to actually kill the process."""

    def __init__(self, records, *, fail_after: int):
        super().__init__(records)
        self._fail_after = fail_after

    def fetch_current_records(self) -> Iterator[Mapping[str, Any]]:
        def _gen():
            for i, record in enumerate(self._records):
                if i == self._fail_after:
                    raise RuntimeError("simulated mid-run failure")
                yield record
            if self._fail_after >= len(self._records):
                # Fail after yielding everything -- simulates the fetch
                # completing but something downstream (e.g. the final
                # plausibility/missing-evidence step) erroring out.
                raise RuntimeError("simulated mid-run failure")

        return _gen()


def _record(n: int, *, category: str = "theft") -> dict:
    return {
        "external_record_id": f"FX-{n}",
        "external_incident_id": f"FX-INC-{n}",
        "category": category,
        "subcategory": "generic",
        "offense_code": "1000",
        "occurred_at": "2026-01-01T12:00:00+00:00",
        "reported_at": "2026-01-01T13:00:00+00:00",
        "address_text": f"{n} BLK MAIN ST",
        "latitude": 39.7,
        "longitude": -104.9,
        "location_precision": "block",
        "neighborhood": "downtown",
        "district": "1",
        "victim_count": None,
    }


def _make_source(db) -> Source:
    source = Source(source_key="fixture-checkpoint-test", name="Fixture Checkpoint Test")
    db.add(source)
    db.flush()
    return source


def test_checkpointing_commits_progress_within_a_run(db):
    """With checkpoint_every_n_batches=1 and batch_size=2, a 6-record
    fetch should checkpoint 3 times. This doesn't observe intermediate
    commits directly (the whole run completes in this test), but
    confirms checkpointed runs still produce exactly the same, correct
    end result as an uncheckpointed one."""
    source = _make_source(db)
    records = [_record(i) for i in range(6)]
    adapter = FixtureSourceAdapter(records)

    result = run_ingestion_cycle(
        db,
        source=source,
        adapter=adapter,
        is_complete=True,
        schema_valid=True,
        reported_total_count=6,
        batch_size=2,
        checkpoint_every_n_batches=1,
    )

    assert result.run.records_created == 6
    assert result.run.status == IngestionRunStatus.COMPLETED
    n_records = (
        db.execute(select(SourceRecord).where(SourceRecord.source_id == source.id)).scalars().all()
    )
    assert len(n_records) == 6


def test_mid_run_failure_preserves_already_checkpointed_batches(db):
    """Records from batches processed before the failure must remain
    committed, even though the overall run failed."""
    source = _make_source(db)
    records = [_record(i) for i in range(6)]
    # batch_size=2 -> 3 batches of 2; fail while building batch 3 (index 4)
    # so batches 1-2 (records 0-3) should already be checkpointed.
    adapter = _FailAfterNAdapter(records, fail_after=4)

    with pytest.raises(RuntimeError, match="simulated mid-run failure"):
        run_ingestion_cycle(
            db,
            source=source,
            adapter=adapter,
            is_complete=True,
            schema_valid=True,
            reported_total_count=6,
            batch_size=2,
            checkpoint_every_n_batches=1,
        )

    committed_records = (
        db.execute(select(SourceRecord).where(SourceRecord.source_id == source.id)).scalars().all()
    )
    committed_ids = {r.external_record_id for r in committed_records}
    assert committed_ids == {"FX-0", "FX-1", "FX-2", "FX-3"}


def test_mid_run_failure_marks_run_failed(db):
    source = _make_source(db)
    records = [_record(i) for i in range(6)]
    adapter = _FailAfterNAdapter(records, fail_after=4)

    with pytest.raises(RuntimeError):
        run_ingestion_cycle(
            db,
            source=source,
            adapter=adapter,
            is_complete=True,
            schema_valid=True,
            reported_total_count=6,
            batch_size=2,
            checkpoint_every_n_batches=1,
        )

    runs = (
        db.execute(select(IngestionRun).where(IngestionRun.source_id == source.id)).scalars().all()
    )
    assert len(runs) == 1
    assert runs[0].status == IngestionRunStatus.FAILED
    assert runs[0].is_complete is False
    assert "simulated mid-run failure" in runs[0].error_message


def test_failed_run_never_applies_missing_record_evidence(db):
    """The core safety property: a run that fails partway through must
    never mark previously-active records as missing/removed, even
    though it was passed is_complete=True and would otherwise have been
    eligible to do so."""
    source = _make_source(db)

    # First, a real successful complete run establishes a trusted
    # baseline with 3 active records.
    baseline_records = [_record(i) for i in range(3)]
    run_ingestion_cycle(
        db,
        source=source,
        adapter=FixtureSourceAdapter(baseline_records),
        is_complete=True,
        schema_valid=True,
        reported_total_count=3,
    )
    baseline_trusted_count = source.last_trusted_active_count
    baseline_trusted_run_id = source.last_trusted_run_id
    assert baseline_trusted_count == 3

    # Second run: only re-sends record 0 (so records 1 and 2 would look
    # "missing" if missing-evidence were incorrectly applied), then
    # fails immediately after its one batch.
    adapter = _FailAfterNAdapter([_record(0)], fail_after=1)
    with pytest.raises(RuntimeError):
        run_ingestion_cycle(
            db,
            source=source,
            adapter=adapter,
            is_complete=True,
            schema_valid=True,
            reported_total_count=1,
            batch_size=1,
            checkpoint_every_n_batches=1,
        )

    # Records 1 and 2 must be untouched: still active, zero missing runs.
    still_active = (
        db.execute(
            select(SourceRecord).where(
                SourceRecord.source_id == source.id,
                SourceRecord.external_record_id.in_(["FX-1", "FX-2"]),
            )
        )
        .scalars()
        .all()
    )
    assert len(still_active) == 2
    for record in still_active:
        assert record.source_active is True
        assert record.consecutive_missing_runs == 0
        assert record.removal_confidence == RemovalConfidence.NONE

    # The source's trusted baseline must not have moved.
    db.refresh(source)
    assert source.last_trusted_active_count == baseline_trusted_count
    assert source.last_trusted_run_id == baseline_trusted_run_id


def test_rerun_after_failure_is_safe_and_completes_correctly(db):
    """After a failed run, simply re-running the full fetch from
    scratch (the documented recovery strategy -- see
    docs/reconciliation-transactions.md) must produce a correct,
    complete end state with no duplicates."""
    source = _make_source(db)
    records = [_record(i) for i in range(6)]

    with pytest.raises(RuntimeError):
        run_ingestion_cycle(
            db,
            source=source,
            adapter=_FailAfterNAdapter(records, fail_after=4),
            is_complete=True,
            schema_valid=True,
            reported_total_count=6,
            batch_size=2,
            checkpoint_every_n_batches=1,
        )

    # Re-run the same fetch from scratch -- no --limit/cursor tricks,
    # just the same full record set again.
    result = run_ingestion_cycle(
        db,
        source=source,
        adapter=FixtureSourceAdapter(records),
        is_complete=True,
        schema_valid=True,
        reported_total_count=6,
        batch_size=2,
        checkpoint_every_n_batches=1,
    )

    assert result.run.status == IngestionRunStatus.COMPLETED
    assert result.run.records_received == 6
    # 4 records already existed (created by the failed run's checkpointed
    # batches) -- only 2 are genuinely new to this second attempt.
    assert result.run.records_created == 2
    assert result.run.records_unchanged == 4

    all_records = (
        db.execute(select(SourceRecord).where(SourceRecord.source_id == source.id)).scalars().all()
    )
    assert len(all_records) == 6  # no duplicates
    assert all(r.source_active for r in all_records)
