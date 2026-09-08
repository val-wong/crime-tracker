"""Mass-disappearance plausibility safeguard tests.

Covers docs/ingestion-framework.md "Complete vs. trusted for deletion
inference" against synthetic fixture data only — the same scenario
class as the real Chicago incident documented in
docs/sources/chicago.md §5, but never against real city data.

Where the task's own scenarios gave explicit record counts (e.g.
"1000 -> 990", "1000 -> 10", "500 -> 20 with minimum 100"), those exact
numbers are used here for traceability. Scenarios that didn't specify
exact counts use smaller proportional numbers to keep the suite fast —
each record round-trips through several DB statements, so 1000-record
runs are reserved for where the task explicitly asked for that scale.
"""

import pytest
from sqlalchemy import select

from app.adapters.fixture import FixtureSourceAdapter
from app.models.enums import PlausibilityBlockReason, PlausibilityStatus
from app.models.source import Source
from app.models.source_record import SourceRecord
from app.repositories import sources as sources_repo
from app.services.ingestion import run_ingestion_cycle


def _make_source(db, key="plausibility-test-source", **overrides) -> Source:
    source = Source(source_key=key, name="Plausibility Test Source", **overrides)
    db.add(source)
    db.flush()
    return source


def _records(n, prefix="R"):
    return [
        {
            "external_record_id": f"{prefix}-{i}",
            "external_incident_id": f"{prefix}-{i}",
            "category": "theft",
        }
        for i in range(n)
    ]


def _active_count(db, source) -> int:
    return len(
        db.execute(
            select(SourceRecord).where(
                SourceRecord.source_id == source.id, SourceRecord.source_active.is_(True)
            )
        )
        .scalars()
        .all()
    )


# ---------------------------------------------------------------------------
# Normal full reconciliation: a small, permitted drop
# ---------------------------------------------------------------------------


def test_normal_decline_within_threshold_produces_normal_missing_evidence(db):
    source = _make_source(db, max_record_count_drop_percent=5.0)

    run_ingestion_cycle(
        db, source=source, adapter=FixtureSourceAdapter(_records(1000)), is_complete=True
    )
    db.refresh(source)
    assert source.last_trusted_active_count == 1000

    # 990 of the original 1000 (a 1% drop -- well within the 5% threshold).
    result = run_ingestion_cycle(
        db, source=source, adapter=FixtureSourceAdapter(_records(990)), is_complete=True
    )

    assert result.run.plausibility_status == PlausibilityStatus.PLAUSIBLE
    assert result.run.blocking_reason is None
    assert result.run.deletion_evidence_allowed is True
    assert result.run.records_missing == 10
    db.refresh(source)
    assert source.last_trusted_active_count == 990  # baseline advances on a plausible run


# ---------------------------------------------------------------------------
# Suspicious collapse: 1000 -> 10
# ---------------------------------------------------------------------------


def test_suspicious_collapse_blocks_deletion_evidence(db):
    source = _make_source(db, max_record_count_drop_percent=10.0)

    run_ingestion_cycle(
        db, source=source, adapter=FixtureSourceAdapter(_records(1000)), is_complete=True
    )
    db.refresh(source)
    assert source.last_trusted_active_count == 1000

    result = run_ingestion_cycle(
        db, source=source, adapter=FixtureSourceAdapter(_records(10)), is_complete=True
    )

    assert result.run.is_complete is True  # the fetch itself "succeeded"
    assert result.run.plausibility_status == PlausibilityStatus.SUSPICIOUS
    assert result.run.blocking_reason == PlausibilityBlockReason.RECORD_COUNT_DROP_EXCEEDS_THRESHOLD
    assert result.run.deletion_evidence_allowed is False
    assert result.run.records_missing == 0  # no missing-evidence accumulated

    # None of the original 1000 records were marked removed or even dinged.
    active_records = (
        db.execute(
            select(SourceRecord).where(
                SourceRecord.source_id == source.id, SourceRecord.external_record_id.like("R-%")
            )
        )
        .scalars()
        .all()
    )
    assert len(active_records) == 1000
    assert all(r.source_active for r in active_records)
    assert all(r.consecutive_missing_runs == 0 for r in active_records)

    # Baseline did not move.
    db.refresh(source)
    assert source.last_trusted_active_count == 1000


# ---------------------------------------------------------------------------
# Recovery: 1000 -> suspicious 10 -> 1001
# ---------------------------------------------------------------------------


def test_recovery_after_suspicious_run_compares_against_old_trusted_baseline(db):
    source = _make_source(db, max_record_count_drop_percent=10.0)

    run_ingestion_cycle(
        db, source=source, adapter=FixtureSourceAdapter(_records(1000)), is_complete=True
    )
    suspicious_result = run_ingestion_cycle(
        db, source=source, adapter=FixtureSourceAdapter(_records(10)), is_complete=True
    )
    assert suspicious_result.run.deletion_evidence_allowed is False

    # Recovery: back up to slightly above the original 1000.
    recovery_result = run_ingestion_cycle(
        db, source=source, adapter=FixtureSourceAdapter(_records(1001)), is_complete=True
    )

    # Compared against the OLD trusted baseline (1000), not the
    # suspicious run's 10.
    assert recovery_result.run.baseline_count_at_run == 1000
    assert recovery_result.run.plausibility_status == PlausibilityStatus.PLAUSIBLE
    assert recovery_result.run.deletion_evidence_allowed is True
    # No false mass removal: none of the original records were confirmed
    # removed by the intervening suspicious run, and the recovery run's
    # missing count should be 0 (all 1000 records are present in the 1001).
    assert recovery_result.run.records_missing == 0

    db.refresh(source)
    assert source.last_trusted_active_count == 1001


# ---------------------------------------------------------------------------
# Legitimate moderate decline within a generous threshold: 1000 -> 950
# ---------------------------------------------------------------------------


def test_legitimate_moderate_decline_allowed_when_threshold_permits(db):
    source = _make_source(db, max_record_count_drop_percent=10.0)

    run_ingestion_cycle(
        db, source=source, adapter=FixtureSourceAdapter(_records(1000)), is_complete=True
    )
    result = run_ingestion_cycle(
        db, source=source, adapter=FixtureSourceAdapter(_records(950)), is_complete=True
    )

    assert result.run.plausibility_status == PlausibilityStatus.PLAUSIBLE
    assert result.run.deletion_evidence_allowed is True
    assert result.run.records_missing == 50
    db.refresh(source)
    assert source.last_trusted_active_count == 950


# ---------------------------------------------------------------------------
# Absolute-floor violation: 500 -> 20, minimum 100
# ---------------------------------------------------------------------------


def test_absolute_floor_violation_blocks_regardless_of_drop_percent(db):
    # No drop-percent threshold configured at all -- the floor alone
    # must still block this.
    source = _make_source(db, min_expected_records=100)

    run_ingestion_cycle(
        db, source=source, adapter=FixtureSourceAdapter(_records(500)), is_complete=True
    )
    result = run_ingestion_cycle(
        db, source=source, adapter=FixtureSourceAdapter(_records(20)), is_complete=True
    )

    assert result.run.plausibility_status == PlausibilityStatus.SUSPICIOUS
    assert result.run.blocking_reason == PlausibilityBlockReason.RECORD_COUNT_BELOW_ABSOLUTE_FLOOR
    assert result.run.deletion_evidence_allowed is False
    assert result.run.records_missing == 0
    db.refresh(source)
    assert source.last_trusted_active_count == 500


def test_absolute_floor_blocks_even_on_the_very_first_run(db):
    """The floor doesn't need a prior baseline to be meaningful."""
    source = _make_source(db, min_expected_records=100)

    result = run_ingestion_cycle(
        db, source=source, adapter=FixtureSourceAdapter(_records(20)), is_complete=True
    )

    assert result.run.deletion_evidence_allowed is False
    assert result.run.blocking_reason == PlausibilityBlockReason.RECORD_COUNT_BELOW_ABSOLUTE_FLOOR
    db.refresh(source)
    assert source.last_trusted_active_count is None  # never established


# ---------------------------------------------------------------------------
# Partial run: existing safety must still hold, now expressed via the new fields
# ---------------------------------------------------------------------------


def test_partial_run_never_evaluates_plausibility_or_allows_deletion_evidence(db):
    source = _make_source(db, max_record_count_drop_percent=5.0)

    run_ingestion_cycle(
        db, source=source, adapter=FixtureSourceAdapter(_records(20)), is_complete=True
    )
    db.refresh(source)
    assert source.last_trusted_active_count == 20

    # A partial run that only saw 2 of the 20 records -- would look like
    # a catastrophic drop, but partial runs are blocked long before
    # plausibility is even considered.
    result = run_ingestion_cycle(
        db,
        source=source,
        adapter=FixtureSourceAdapter(_records(2)),
        is_complete=False,
        error_message="simulated network failure mid-pagination",
    )

    assert result.run.plausibility_status == PlausibilityStatus.NOT_EVALUATED
    assert result.run.deletion_evidence_allowed is False
    assert result.run.records_missing == 0
    # Still surfaces what the current baseline was, for observability.
    assert result.run.baseline_count_at_run == 20

    db.refresh(source)
    assert source.last_trusted_active_count == 20  # unchanged


# ---------------------------------------------------------------------------
# Manual override
# ---------------------------------------------------------------------------


def test_manual_override_promotes_suspicious_run_to_new_baseline(db):
    source = _make_source(db, max_record_count_drop_percent=5.0)

    run_ingestion_cycle(
        db, source=source, adapter=FixtureSourceAdapter(_records(20)), is_complete=True
    )
    suspicious = run_ingestion_cycle(
        db, source=source, adapter=FixtureSourceAdapter(_records(2)), is_complete=True
    )
    assert suspicious.run.deletion_evidence_allowed is False
    db.refresh(source)
    assert source.last_trusted_active_count == 20  # not yet moved

    # Operator investigates, confirms this is a legitimate deletion, and
    # deliberately promotes the run.
    sources_repo.promote_run_to_trusted_baseline(db, source=source, run=suspicious.run)

    db.refresh(source)
    assert source.last_trusted_active_count == 2
    assert source.last_trusted_run_id == suspicious.run.id
    assert suspicious.run.plausibility_status == PlausibilityStatus.OVERRIDDEN_TRUSTED
    assert suspicious.run.promoted_to_baseline_at is not None

    # Future reconciliation now compares against the new (lower) baseline.
    next_result = run_ingestion_cycle(
        db, source=source, adapter=FixtureSourceAdapter(_records(2)), is_complete=True
    )
    assert next_result.run.baseline_count_at_run == 2
    assert next_result.run.plausibility_status == PlausibilityStatus.PLAUSIBLE


def test_promote_refuses_to_cross_sources(db):
    source_a = _make_source(db, key="override-source-a")
    source_b = _make_source(db, key="override-source-b")

    result = run_ingestion_cycle(
        db, source=source_a, adapter=FixtureSourceAdapter(_records(5)), is_complete=True
    )

    with pytest.raises(ValueError):
        sources_repo.promote_run_to_trusted_baseline(db, source=source_b, run=result.run)


def test_manual_deletion_hold_blocks_regardless_of_counts(db):
    source = _make_source(db, manual_deletion_hold=True)

    run_ingestion_cycle(
        db, source=source, adapter=FixtureSourceAdapter(_records(20)), is_complete=True
    )
    # Identical count -- would trivially pass any count-based check --
    # but the manual hold must still block it.
    result = run_ingestion_cycle(
        db, source=source, adapter=FixtureSourceAdapter(_records(20)), is_complete=True
    )

    assert result.run.plausibility_status == PlausibilityStatus.SUSPICIOUS
    assert result.run.blocking_reason == PlausibilityBlockReason.MANUAL_SAFETY_HOLD
    assert result.run.deletion_evidence_allowed is False


def test_disabling_protection_bypasses_checks_entirely(db):
    source = _make_source(
        db, min_expected_records=1000, mass_disappearance_protection_enabled=False
    )

    run_ingestion_cycle(
        db, source=source, adapter=FixtureSourceAdapter(_records(20)), is_complete=True
    )
    result = run_ingestion_cycle(
        db, source=source, adapter=FixtureSourceAdapter(_records(1)), is_complete=True
    )

    assert result.run.plausibility_status == PlausibilityStatus.PLAUSIBLE
    assert result.run.deletion_evidence_allowed is True


# ---------------------------------------------------------------------------
# Consecutive suspicious runs must not gradually establish a lower baseline
# ---------------------------------------------------------------------------


def test_consecutive_suspicious_runs_do_not_drift_the_baseline(db):
    source = _make_source(db, max_record_count_drop_percent=5.0)

    run_ingestion_cycle(
        db, source=source, adapter=FixtureSourceAdapter(_records(20)), is_complete=True
    )

    for suspicious_count in (2, 3, 1):
        result = run_ingestion_cycle(
            db,
            source=source,
            adapter=FixtureSourceAdapter(_records(suspicious_count)),
            is_complete=True,
        )
        assert result.run.deletion_evidence_allowed is False
        assert (
            result.run.baseline_count_at_run == 20
        )  # always compared against the ORIGINAL baseline
        db.refresh(source)
        assert source.last_trusted_active_count == 20  # never drifts


# ---------------------------------------------------------------------------
# Source isolation
# ---------------------------------------------------------------------------


def test_one_sources_suspicious_collapse_does_not_affect_another_source(db):
    source_a = _make_source(db, key="isolation-source-a", max_record_count_drop_percent=5.0)
    source_b = _make_source(db, key="isolation-source-b", max_record_count_drop_percent=5.0)

    run_ingestion_cycle(
        db,
        source=source_a,
        adapter=FixtureSourceAdapter(_records(20, prefix="A")),
        is_complete=True,
    )
    run_ingestion_cycle(
        db,
        source=source_b,
        adapter=FixtureSourceAdapter(_records(20, prefix="B")),
        is_complete=True,
    )

    # Source A collapses catastrophically.
    result_a = run_ingestion_cycle(
        db, source=source_a, adapter=FixtureSourceAdapter(_records(1, prefix="A")), is_complete=True
    )
    assert result_a.run.deletion_evidence_allowed is False

    # Source B has an entirely normal run in the meantime -- must be
    # completely unaffected by A's suspicious state.
    result_b = run_ingestion_cycle(
        db,
        source=source_b,
        adapter=FixtureSourceAdapter(_records(19, prefix="B")),
        is_complete=True,
    )
    assert result_b.run.plausibility_status == PlausibilityStatus.PLAUSIBLE
    assert result_b.run.deletion_evidence_allowed is True
    assert result_b.run.records_missing == 1

    db.refresh(source_a)
    db.refresh(source_b)
    assert source_a.last_trusted_active_count == 20  # unaffected by its own suspicious run
    assert source_b.last_trusted_active_count == 19  # advanced normally
