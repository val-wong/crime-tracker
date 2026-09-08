"""One attempt to refresh `incident_grid_rollup` (see
app/services/rollup.py, docs/rollup-design.md "Refresh strategy").

Mirrors `IngestionRun`'s role for the rollup: a durable, inspectable
record of "when did this last succeed / what happened when it didn't"
-- exactly the "clear status/logging" this phase's refresh strategy
requires, beyond just a log line that scrolls away.
"""

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPKMixin
from app.models.enums import RollupRefreshStatus


class RollupRefreshRun(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "rollup_refresh_runs"

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[RollupRefreshStatus] = mapped_column(
        SAEnum(
            RollupRefreshStatus,
            name="rollup_refresh_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=RollupRefreshStatus.RUNNING,
    )

    row_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration_seconds: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<RollupRefreshRun {self.id} status={self.status}>"
