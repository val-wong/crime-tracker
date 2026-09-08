"""A single offense within an incident.

One incident may have multiple offenses (confirmed for Denver: 94.2%
of incidents have exactly one, up to 8 observed on one incident — see
docs/sources/denver-ingestion-design.md §2). Each offense traces back
to the source record/version that produced it via ``source_record_id``.

The normalized crime taxonomy is not built yet (see
docs/product.md/architecture.md) — ``normalized_category`` and
``normalized_subcategory`` stay nullable until that phase.
"""

import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.incident import Incident
    from app.models.source_record import SourceRecord


class Offense(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "offenses"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=False, index=True
    )
    # Denormalized from incidents.source_id (an offense always belongs
    # to exactly one incident, which always belongs to exactly one
    # source, so this is never ambiguous). Added during production-
    # readiness hardening after a real, measured problem: counting
    # offenses for a source previously required joining to `incidents`
    # for the filter, which meant scanning both multi-million-row
    # tables and took ~17s at full Chicago scale (confirmed via
    # EXPLAIN ANALYZE -- see docs/performance-validation.md). Storing
    # it directly turns that into a single indexed count.
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False, index=True
    )
    # Nullable: an offense's provenance is normally a source record, but
    # keeping the FK nullable avoids ever needing to fabricate a source
    # record for an offense we can't otherwise cleanly attribute.
    source_record_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("source_records.id"), nullable=True, index=True
    )

    external_offense_id: Mapped[str] = mapped_column(String(255), nullable=False)
    raw_offense_code: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # A separate, coarser classification some sources carry alongside
    # their own local code (e.g. Chicago's `fbi_code`) — see
    # docs/sources/chicago.md §7 for why this isn't folded into
    # `raw_offense_code`. Not part of the (not-yet-built) normalized
    # taxonomy — still a source-provided value, preserved as-is.
    fbi_code: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    source_category: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source_subcategory: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    normalized_category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    normalized_subcategory: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    victim_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    incident: Mapped["Incident"] = relationship(back_populates="offenses")
    source_record: Mapped[Optional["SourceRecord"]] = relationship(back_populates="offenses")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Offense {self.external_offense_id!r}>"
