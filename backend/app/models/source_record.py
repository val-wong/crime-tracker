"""A source's own record, tracked generically across any city/source.

``external_record_id`` is the source's own identifier for one record
(e.g. Denver's ``OFFENSE_ID``) — deliberately named generically since
future sources will not all use Denver's naming or granularity. See
docs/sources/denver-ingestion-design.md §1 for why this is empirically
(not contractually) unique per source.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPKMixin
from app.models.enums import RemovalConfidence

if TYPE_CHECKING:
    from app.models.offense import Offense
    from app.models.raw_version import RawSourceRecordVersion
    from app.models.source import Source


class SourceRecord(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "source_records"
    __table_args__ = (
        UniqueConstraint("source_id", "external_record_id", name="uq_source_record_external_id"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False, index=True
    )
    external_record_id: Mapped[str] = mapped_column(String(255), nullable=False)

    source_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Historical marker of the most recent time this record was
    # confirmed removed. Deliberately NOT cleared on reactivation — see
    # "Reappearance" in docs/ingestion-framework.md: source_active is
    # the authoritative "is it live right now" flag; this field is a
    # breadcrumb of the last removal event, preserved for provenance.
    source_removed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    removal_confidence: Mapped[RemovalConfidence] = mapped_column(
        SAEnum(
            RemovalConfidence,
            name="removal_confidence",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=RemovalConfidence.NONE,
    )
    # Underlying evidence counter driving removal_confidence transitions.
    # Reset to 0 any time the record is observed in a fetch (complete or
    # partial run); only incremented on a *complete* run that misses it.
    consecutive_missing_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    current_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    source: Mapped["Source"] = relationship(back_populates="source_records")
    raw_versions: Mapped[list["RawSourceRecordVersion"]] = relationship(
        back_populates="source_record",
        cascade="all, delete-orphan",
        order_by="RawSourceRecordVersion.observed_at",
    )
    offenses: Mapped[list["Offense"]] = relationship(back_populates="source_record")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<SourceRecord {self.external_record_id!r} active={self.source_active}>"
