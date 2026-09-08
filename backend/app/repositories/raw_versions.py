"""Persistence functions for RawSourceRecordVersion rows.

Intentionally exposes only ``create`` — no update or delete function.
This table is append-only at the application level (see
app/models/raw_version.py); the absence of update/delete helpers here
is the enforcement mechanism.
"""

import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.raw_version import RawSourceRecordVersion


def create(
    db: Session,
    *,
    source_record_id: uuid.UUID,
    ingestion_run_id: uuid.UUID,
    fingerprint: str,
    raw_payload: Mapping[str, Any],
    observed_at: datetime,
) -> RawSourceRecordVersion:
    version = RawSourceRecordVersion(
        source_record_id=source_record_id,
        ingestion_run_id=ingestion_run_id,
        fingerprint=fingerprint,
        raw_payload=dict(raw_payload),
        observed_at=observed_at,
    )
    db.add(version)
    db.flush()
    return version
