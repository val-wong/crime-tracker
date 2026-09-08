"""Chicago's official IUCR (Illinois Uniform Crime Reporting) reference
codes — a small, publisher-maintained lookup table, not an
internally-generated entity, hence the natural (not UUID) primary key.

See docs/sources/chicago.md §7: the crimes dataset's own `iucr` field
is best understood against this table, which the publisher itself
keeps versioned via an `active` flag — 10 of 434 codes were confirmed
inactive at investigation time. Deliberately NOT enforced as a foreign
key from `Offense.raw_offense_code`: a historical offense may
legitimately reference a code this table has since marked inactive
(or, if our local copy is stale, one not yet reflected here at all),
and a hard constraint would incorrectly block ingestion of otherwise
valid crime records over a reference-data staleness issue. This table
is for validation/enrichment at read or ingestion time, not integrity
enforcement.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import utcnow


class IucrCode(Base):
    __tablename__ = "iucr_codes"

    iucr: Mapped[str] = mapped_column(String(10), primary_key=True)
    primary_description: Mapped[str] = mapped_column(String(255), nullable=False)
    secondary_description: Mapped[str] = mapped_column(String(255), nullable=False)
    # "I" (Index, FBI-tracked), "N" (Non-Index), or "D" (the special
    # domestic-violence catch-all code) — see docs/sources/chicago.md §7.
    index_code: Mapped[str] = mapped_column(String(5), nullable=False)
    # Never discard a row just because active=False — see module
    # docstring. This flag is preserved for enrichment/alerting, not
    # used to filter out historically-referenced codes.
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)

    last_refreshed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<IucrCode {self.iucr!r} active={self.active}>"
