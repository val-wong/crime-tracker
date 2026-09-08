"""Mass-disappearance plausibility safeguard.

A *complete* run (the fetch itself succeeded end-to-end — see
``IngestionRun.is_complete``) is not automatically trustworthy as
evidence that unseen records were deleted by the source. A real,
publicly-documented incident (a publisher-side pipeline bug that
truncated an entire dataset to a handful of rows while still returning
a successful API response — see docs/sources/chicago.md §5) showed
this gap concretely: a technically successful fetch can still reflect
a corrupted or truncated snapshot on the *source's* side, upstream of
anything our own fetch logic can detect.

This module answers a question distinct from "did the fetch succeed":
is the resulting snapshot *plausible* enough, compared to the source's
own prior trusted history, to trust for deletion inference? See
docs/ingestion-framework.md "Complete vs. trusted for deletion
inference" for the full explanation.

Nothing here is specific to any one source — thresholds are read from
the ``Source`` row passed in, never hard-coded (see
docs/sources/chicago-ingestion-design.md §5 for why Chicago's actual
threshold needs real tuning, not a value invented here).
"""

from dataclasses import dataclass
from typing import Optional

from app.models.enums import PlausibilityBlockReason, PlausibilityStatus
from app.models.source import Source


@dataclass(frozen=True)
class PlausibilityResult:
    status: PlausibilityStatus
    blocking_reason: Optional[PlausibilityBlockReason]
    deletion_evidence_allowed: bool
    baseline_count_at_run: Optional[int]
    percent_change_from_baseline: Optional[float]


def evaluate_plausibility(
    source: Source,
    *,
    records_received: int,
    schema_valid: bool = True,
    reported_total_count: Optional[int] = None,
) -> PlausibilityResult:
    """Evaluate whether a complete run's snapshot should be trusted.

    Only meaningful to call for a run whose fetch itself was complete
    — see ``reconcile_source_records``, which is the only caller and
    only invokes this when ``is_complete=True``. Checks are evaluated
    in order of "how fundamental the problem is": a schema failure or
    a proactive manual hold block regardless of counts; a raw
    fetch/count mismatch is checked before comparing against history;
    an absolute floor is checked before any baseline-relative check
    (it doesn't need a baseline to be meaningful); only then is the
    relative drop against the trusted baseline considered.
    """
    baseline = source.last_trusted_active_count
    pct_change = _percent_change(baseline, records_received)

    if not schema_valid:
        return PlausibilityResult(
            status=PlausibilityStatus.SUSPICIOUS,
            blocking_reason=PlausibilityBlockReason.SCHEMA_VALIDATION_FAILED,
            deletion_evidence_allowed=False,
            baseline_count_at_run=baseline,
            percent_change_from_baseline=pct_change,
        )

    if source.manual_deletion_hold:
        return PlausibilityResult(
            status=PlausibilityStatus.SUSPICIOUS,
            blocking_reason=PlausibilityBlockReason.MANUAL_SAFETY_HOLD,
            deletion_evidence_allowed=False,
            baseline_count_at_run=baseline,
            percent_change_from_baseline=pct_change,
        )

    if not source.mass_disappearance_protection_enabled:
        return PlausibilityResult(
            status=PlausibilityStatus.PLAUSIBLE,
            blocking_reason=None,
            deletion_evidence_allowed=True,
            baseline_count_at_run=baseline,
            percent_change_from_baseline=pct_change,
        )

    if reported_total_count is not None and reported_total_count != records_received:
        return PlausibilityResult(
            status=PlausibilityStatus.SUSPICIOUS,
            blocking_reason=PlausibilityBlockReason.SOURCE_COUNT_MISMATCH,
            deletion_evidence_allowed=False,
            baseline_count_at_run=baseline,
            percent_change_from_baseline=pct_change,
        )

    if source.min_expected_records is not None and records_received < source.min_expected_records:
        return PlausibilityResult(
            status=PlausibilityStatus.SUSPICIOUS,
            blocking_reason=PlausibilityBlockReason.RECORD_COUNT_BELOW_ABSOLUTE_FLOOR,
            deletion_evidence_allowed=False,
            baseline_count_at_run=baseline,
            percent_change_from_baseline=pct_change,
        )

    if not baseline:
        # No prior trusted baseline (first-ever run for this source, or
        # baseline explicitly unset). Nothing implausible about a first
        # observation — it becomes the baseline going forward.
        return PlausibilityResult(
            status=PlausibilityStatus.PLAUSIBLE,
            blocking_reason=None,
            deletion_evidence_allowed=True,
            baseline_count_at_run=baseline,
            percent_change_from_baseline=None,
        )

    if (
        source.max_record_count_drop_percent is not None
        and pct_change is not None
        and pct_change < 0
        and abs(pct_change) > source.max_record_count_drop_percent
    ):
        return PlausibilityResult(
            status=PlausibilityStatus.SUSPICIOUS,
            blocking_reason=PlausibilityBlockReason.RECORD_COUNT_DROP_EXCEEDS_THRESHOLD,
            deletion_evidence_allowed=False,
            baseline_count_at_run=baseline,
            percent_change_from_baseline=pct_change,
        )

    return PlausibilityResult(
        status=PlausibilityStatus.PLAUSIBLE,
        blocking_reason=None,
        deletion_evidence_allowed=True,
        baseline_count_at_run=baseline,
        percent_change_from_baseline=pct_change,
    )


def _percent_change(baseline: Optional[int], current: int) -> Optional[float]:
    """(current - baseline) / baseline * 100 — negative means a drop."""
    if not baseline:
        return None
    return ((current - baseline) / baseline) * 100.0
