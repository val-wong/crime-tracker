"""Refresh logic for the `incident_grid_rollup` materialized view.

See docs/rollup-design.md "Refresh strategy". Never invoked by
application startup or implicitly by ingestion -- always an explicit
call, either via `python -m app.cli refresh-rollup` or from the ingest
CLI commands themselves after a successful run (see app/cli.py).
"""

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.mixins import utcnow
from app.models.enums import RollupRefreshStatus
from app.models.rollup_refresh_run import RollupRefreshRun

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RollupRefreshResult:
    run_id: uuid.UUID
    status: RollupRefreshStatus
    row_count: Optional[int]
    duration_seconds: float


def refresh_rollup(db: Session) -> RollupRefreshResult:
    """Refresh `incident_grid_rollup` via `REFRESH ... CONCURRENTLY`.

    Readers see the old (still consistent) data for the whole duration
    of the refresh, and continue to on failure -- CONCURRENTLY builds
    the new result set separately and only swaps it in atomically at
    the end, so a failed/interrupted refresh never leaves the view
    half-updated or unusable (see docs/rollup-design.md).

    Raises whatever the underlying `REFRESH` statement raises, after
    recording the failure -- callers must not treat a failed refresh as
    a non-event.
    """
    run = RollupRefreshRun(started_at=utcnow(), status=RollupRefreshStatus.RUNNING)
    db.add(run)
    db.flush()
    db.commit()

    start = time.monotonic()
    try:
        db.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY incident_grid_rollup"))
        db.commit()
    except Exception as exc:
        db.rollback()
        duration = time.monotonic() - start
        run.completed_at = utcnow()
        run.status = RollupRefreshStatus.FAILED
        run.duration_seconds = duration
        run.error_message = str(exc)
        db.add(run)
        db.commit()
        logger.error("Rollup refresh failed after %.2fs: %s", duration, exc)
        raise

    duration = time.monotonic() - start
    row_count = db.execute(text("SELECT count(*) FROM incident_grid_rollup")).scalar_one()

    run.completed_at = utcnow()
    run.status = RollupRefreshStatus.COMPLETED
    run.duration_seconds = duration
    run.row_count = row_count
    db.add(run)
    db.commit()

    logger.info("Rollup refresh completed in %.2fs: row_count=%d", duration, row_count)
    return RollupRefreshResult(
        run_id=run.id,
        status=run.status,
        row_count=row_count,
        duration_seconds=duration,
    )


def get_latest_refresh(db: Session) -> Optional[RollupRefreshRun]:
    return db.execute(
        select(RollupRefreshRun).order_by(RollupRefreshRun.started_at.desc()).limit(1)
    ).scalar_one_or_none()
