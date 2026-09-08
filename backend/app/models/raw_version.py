"""Append-only raw-record version history.

**Append-only at the application level**: this table must only ever be
inserted into, never updated or deleted, by application code. A new
row is written only when a source record's fingerprint changes from
its previously stored version (see app/services/reconciliation.py) —
an unchanged record does not get a new version on every re-fetch, so
storage cost is bounded by how often a record actually changes, not by
how often we poll it. See docs/architecture.md "Raw versioning for
sources that mutate records without a per-row timestamp" and
docs/ingestion-framework.md for the full rationale.

No database-level trigger enforces this (per the Phase 3 instructions:
"append-only at the application level"); the repository layer
(app/repositories/raw_versions.py) intentionally exposes no
update/delete functions for this table.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import UUIDPKMixin, utcnow

if TYPE_CHECKING:
    from app.models.ingestion_run import IngestionRun
    from app.models.source_record import SourceRecord


class RawSourceRecordVersion(UUIDPKMixin, Base):
    __tablename__ = "raw_source_record_versions"

    source_record_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_records.id"), nullable=False, index=True
    )
    ingestion_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingestion_runs.id"), nullable=False, index=True
    )

    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    source_record: Mapped["SourceRecord"] = relationship(back_populates="raw_versions")
    ingestion_run: Mapped["IngestionRun"] = relationship(back_populates="raw_versions")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<RawSourceRecordVersion {self.id} fingerprint={self.fingerprint[:8]}>"
