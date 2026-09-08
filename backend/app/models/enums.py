"""Enum types shared across models.

Plain ``str`` enums so values serialize naturally to JSON and map
cleanly onto PostgreSQL native ``ENUM`` types via SQLAlchemy.
"""

import enum


class IngestionRunStatus(str, enum.Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class RemovalConfidence(str, enum.Enum):
    """Evidence level that a source record has actually been removed.

    ``NONE`` means the record was seen in the most recent complete
    reconciliation run (or has never been missing). Confidence only
    advances on *complete* runs — see docs/ingestion-framework.md.
    """

    NONE = "none"
    PENDING = "pending"
    LIKELY = "likely"
    CONFIRMED = "confirmed"


class PlausibilityStatus(str, enum.Enum):
    """Whether a *complete* run's snapshot is trustworthy enough to use
    as deletion evidence and as the next reconciliation baseline.

    This is deliberately separate from ``IngestionRunStatus``/
    ``IngestionRun.is_complete``: a run can be ``complete`` (the fetch
    itself succeeded end-to-end) while still being ``SUSPICIOUS`` here
    (the snapshot it fetched looks implausible compared to prior
    trusted history) — see docs/ingestion-framework.md "Complete vs.
    trusted for deletion inference" and
    docs/sources/chicago.md §5 for the real incident that motivated
    this distinction.
    """

    NOT_EVALUATED = "not_evaluated"  # partial/failed run; plausibility isn't meaningful to check
    PLAUSIBLE = "plausible"
    SUSPICIOUS = "suspicious"
    OVERRIDDEN_TRUSTED = "overridden_trusted"  # manually promoted after being suspicious


class PlausibilityBlockReason(str, enum.Enum):
    """Why a run's snapshot was not trusted for deletion inference.

    Persisted as structured state (not just an error string) so
    diagnosis and any future alerting can branch on it directly.
    """

    SCHEMA_VALIDATION_FAILED = "schema_validation_failed"
    MANUAL_SAFETY_HOLD = "manual_safety_hold"
    SOURCE_COUNT_MISMATCH = "source_count_mismatch"
    RECORD_COUNT_BELOW_ABSOLUTE_FLOOR = "record_count_below_absolute_floor"
    RECORD_COUNT_DROP_EXCEEDS_THRESHOLD = "record_count_drop_exceeds_threshold"


class RollupRefreshStatus(str, enum.Enum):
    """Status of one `incident_grid_rollup` refresh attempt -- see
    app/services/rollup.py and docs/rollup-design.md "Refresh
    strategy". Mirrors IngestionRunStatus's shape for consistency, but
    is its own enum since a rollup refresh has no "partial" concept
    (REFRESH MATERIALIZED VIEW CONCURRENTLY is atomic: it either fully
    succeeds or leaves the prior data untouched)."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class LocationPrecision(str, enum.Enum):
    """How precisely an incident's stored coordinates should be trusted.

    Never assume a source's published lat/lon is the exact incident
    location — see docs/sources/denver.md §3. This is deliberately
    generic (not Denver-specific) so any future source adapter can
    report what it actually knows.
    """

    EXACT = "exact"
    APPROXIMATE = "approximate"
    BLOCK = "block"
    INTERSECTION = "intersection"
    SUPPRESSED = "suppressed"
    UNKNOWN = "unknown"
