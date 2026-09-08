"""Persistence functions for IngestionRun rows."""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.mixins import utcnow
from app.models.enums import IngestionRunStatus
from app.models.ingestion_run import IngestionRun


def start_run(
    db: Session, *, source_id: uuid.UUID, started_at: Optional[datetime] = None
) -> IngestionRun:
    run = IngestionRun(
        source_id=source_id,
        started_at=started_at or utcnow(),
        status=IngestionRunStatus.RUNNING,
        is_complete=False,
    )
    db.add(run)
    db.flush()
    return run


def finish_run(
    db: Session,
    run: IngestionRun,
    *,
    is_complete: bool,
    status: Optional[IngestionRunStatus] = None,
    error_message: Optional[str] = None,
) -> IngestionRun:
    """Finalize a run.

    ``is_complete`` is the field reconciliation logic trusts for
    deletion evidence; pass True only when the source's *entire*
    current dataset was fetched successfully. If ``status`` is not
    given, it's derived: COMPLETED when is_complete, else FAILED.
    """
    run.completed_at = utcnow()
    run.is_complete = is_complete
    run.error_message = error_message
    if status is not None:
        run.status = status
    else:
        run.status = IngestionRunStatus.COMPLETED if is_complete else IngestionRunStatus.FAILED
    db.flush()
    return run


def get_latest_completed(db: Session, *, source_id: uuid.UUID) -> Optional[IngestionRun]:
    """Most recent run that finished successfully (regardless of
    whether it was a full or bounded fetch) -- used by the public
    dataset-status endpoint to report "last updated" freshness.
    """
    return db.execute(
        select(IngestionRun)
        .where(
            IngestionRun.source_id == source_id, IngestionRun.status == IngestionRunStatus.COMPLETED
        )
        .order_by(IngestionRun.completed_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def get_latest_complete_full_run(db: Session, *, source_id: uuid.UUID) -> Optional[IngestionRun]:
    """Most recent run that fetched the source's *entire* dataset
    successfully (``is_complete``) -- existence of one is what "dataset
    population is complete" means (see docs/ingestion-framework.md).
    Distinct from ``get_latest_completed``: a small bounded smoke/dev
    ingest can be `status=COMPLETED` without ever being `is_complete`.
    """
    return db.execute(
        select(IngestionRun)
        .where(IngestionRun.source_id == source_id, IngestionRun.is_complete.is_(True))
        .order_by(IngestionRun.completed_at.desc())
        .limit(1)
    ).scalar_one_or_none()
