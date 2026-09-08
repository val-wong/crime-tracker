"""Persistence functions for SourceRecord rows."""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import RemovalConfidence
from app.models.source_record import SourceRecord


def get_by_external_id(
    db: Session, *, source_id: uuid.UUID, external_record_id: str
) -> Optional[SourceRecord]:
    return db.execute(
        select(SourceRecord).where(
            SourceRecord.source_id == source_id,
            SourceRecord.external_record_id == external_record_id,
        )
    ).scalar_one_or_none()


def create(
    db: Session,
    *,
    source_id: uuid.UUID,
    external_record_id: str,
    fingerprint: str,
    observed_at: datetime,
) -> SourceRecord:
    record = SourceRecord(
        source_id=source_id,
        external_record_id=external_record_id,
        source_active=True,
        first_seen_at=observed_at,
        last_seen_at=observed_at,
        current_fingerprint=fingerprint,
        removal_confidence=RemovalConfidence.NONE,
        consecutive_missing_runs=0,
    )
    db.add(record)
    db.flush()
    return record


def list_active_source_records(db: Session, *, source_id: uuid.UUID) -> list[SourceRecord]:
    return list(
        db.execute(
            select(SourceRecord).where(
                SourceRecord.source_id == source_id,
                SourceRecord.source_active.is_(True),
            )
        ).scalars()
    )
