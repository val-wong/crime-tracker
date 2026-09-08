"""A real-world reported incident, normalized across sources.

Deliberately generic: fields that not every source provides are
nullable (see docs/architecture.md "Proposed Normalized Incident
Model"). Coordinates are never assumed to be exact — see
``location_precision`` and docs/sources/denver.md §3 for why.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from geoalchemy2 import Geography
from sqlalchemy import DateTime, Float, ForeignKey, String, UniqueConstraint
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPKMixin
from app.models.enums import LocationPrecision

if TYPE_CHECKING:
    from app.models.offense import Offense
    from app.models.source import Source


class Incident(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "incidents"
    __table_args__ = (
        UniqueConstraint("source_id", "external_incident_id", name="uq_incident_external_id"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False, index=True
    )
    # The source's own incident-level identifier (e.g. Denver's
    # INCIDENT_ID). If a future source has no incident-level concept
    # distinct from a single record, its adapter should synthesize one
    # (e.g. reuse the record's own external id) rather than leaving
    # this null, so every offense always has an incident to belong to.
    external_incident_id: Mapped[str] = mapped_column(String(255), nullable=False)

    occurred_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # The raw, unparsed source value for occurred_at, preserved
    # verbatim — see docs/sources/denver-ingestion-design.md §3 on why
    # we keep the raw string alongside our parsed interpretation.
    occurred_at_raw: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    reported_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reported_at_raw: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # e.g. "source-declared-utc-unverified-upstream" — see
    # docs/sources/denver-ingestion-design.md §3. Deliberately free text
    # metadata, not a boolean, since the honest answer is often nuanced.
    source_time_convention: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Canonical PostGIS geography point (WGS84 / SRID 4326), when
    # coordinates are available. Kept alongside latitude/longitude
    # (rather than instead of) so simple consumers don't need PostGIS
    # functions just to read a coordinate pair back out.
    location: Mapped[Optional[str]] = mapped_column(
        Geography(geometry_type="POINT", srid=4326), nullable=True
    )

    # Never call generalized coordinates "exact" — see
    # docs/sources/denver.md §3 for the confirmed evidence that a
    # source's published lat/lon can correspond to a block or
    # intersection rather than the true incident location.
    location_precision: Mapped[LocationPrecision] = mapped_column(
        SAEnum(
            LocationPrecision,
            name="location_precision",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=LocationPrecision.UNKNOWN,
    )

    address_text: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    neighborhood: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    district: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    beat: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    source: Mapped["Source"] = relationship(back_populates="incidents")
    offenses: Mapped[list["Offense"]] = relationship(
        back_populates="incident", cascade="all, delete-orphan"
    )

    @property
    def categories(self) -> list[str]:
        """Distinct offense source_categories on this incident — a
        light summary for the API (see app/schemas/incident.py), not
        the normalized taxonomy (doesn't exist yet). Callers listing
        many incidents should eager-load `offenses` (see
        app/repositories/incidents.py `list_incidents`) to avoid an
        N+1 query per incident.
        """
        seen: list[str] = []
        for offense in self.offenses:
            if offense.source_category and offense.source_category not in seen:
                seen.append(offense.source_category)
        return seen

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Incident {self.external_incident_id!r}>"
