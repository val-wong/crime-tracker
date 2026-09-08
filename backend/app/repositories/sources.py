"""Persistence functions for Source rows.

Plain functions over a passed-in Session — no repository classes.
Routes/services should go through these rather than querying the ORM
directly (see docs/ingestion-framework.md).
"""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.mixins import utcnow
from app.models.enums import PlausibilityStatus
from app.models.ingestion_run import IngestionRun
from app.models.source import Source


def get_by_key(db: Session, source_key: str) -> Optional[Source]:
    return db.execute(select(Source).where(Source.source_key == source_key)).scalar_one_or_none()


def create(
    db: Session,
    *,
    source_key: str,
    name: str,
    publisher: Optional[str] = None,
    source_url: Optional[str] = None,
    timezone_convention: Optional[str] = None,
    enabled: bool = True,
) -> Source:
    source = Source(
        source_key=source_key,
        name=name,
        publisher=publisher,
        source_url=source_url,
        timezone_convention=timezone_convention,
        enabled=enabled,
    )
    db.add(source)
    db.flush()
    return source


def get_or_create(
    db: Session,
    *,
    source_key: str,
    name: str,
    **kwargs,
) -> Source:
    existing = get_by_key(db, source_key)
    if existing is not None:
        return existing
    return create(db, source_key=source_key, name=name, **kwargs)


def promote_run_to_trusted_baseline(db: Session, *, source: Source, run: IngestionRun) -> Source:
    """Explicit, deliberate operator override.

    Promotes ``run`` (even one that was flagged suspicious by the
    automatic plausibility check) to be the source's new trusted
    reconciliation baseline. This must only ever be called as a
    deliberate, out-of-band operator action after investigating a run
    — e.g. confirming a large drop was a legitimate deletion at the
    source, not a publisher-side truncation bug like the one
    documented in docs/sources/chicago.md §5. It is never called
    automatically by reconciliation logic itself.

    This affects *future* reconciliation only: the next full run will
    compare against this (possibly much lower) baseline. It does not
    retroactively reprocess ``run`` itself — that run's own
    missing-evidence accumulation, if any was skipped, is not replayed.
    See docs/ingestion-framework.md "Manual override".
    """
    if run.source_id != source.id:
        raise ValueError("run does not belong to source; refusing to promote across sources")

    run.plausibility_status = PlausibilityStatus.OVERRIDDEN_TRUSTED
    run.blocking_reason = None
    run.deletion_evidence_allowed = True
    run.promoted_to_baseline_at = utcnow()

    source.last_trusted_active_count = run.records_received
    source.last_trusted_run_id = run.id
    db.flush()
    return source


def set_manual_deletion_hold(db: Session, *, source: Source, enabled: bool) -> Source:
    """Proactively hold (or release) deletion evidence for a source,
    independent of the automatic count-based checks. See
    ``Source.manual_deletion_hold``.
    """
    source.manual_deletion_hold = enabled
    db.flush()
    return source
