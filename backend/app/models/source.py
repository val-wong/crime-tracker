"""An external public-data source (e.g. one city's open-data catalog).

Deliberately contains no source-specific behavior — that belongs in a
``SourceAdapter`` (see app/adapters/base.py). This row just identifies
the source and records a few facts about it discovered during source
research (see docs/sources/denver.md for the Denver example).
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPKMixin

if TYPE_CHECKING:
    from app.models.incident import Incident
    from app.models.ingestion_run import IngestionRun
    from app.models.source_record import SourceRecord


class Source(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "sources"

    source_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    publisher: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)

    # e.g. "source-declared-utc-unverified-upstream" — see
    # docs/sources/denver-ingestion-design.md §3 for why this is
    # recorded as free-text metadata rather than a boolean.
    timezone_convention: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # --- Reconciliation safety / mass-disappearance plausibility config ---
    # All of these are per-source and generic — nothing here is
    # hard-coded to any one source's numbers (see
    # docs/sources/chicago-ingestion-design.md §5 for why a real
    # source's actual thresholds need real tuning, not a baked-in
    # default). None means "this specific check is disabled" for that
    # source.
    min_expected_records: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    max_record_count_drop_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    mass_disappearance_protection_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    # Proactive operator-set hold: when true, no run for this source is
    # ever trusted for deletion evidence, regardless of what the count
    # checks say, until an operator clears it. See
    # app/repositories/sources.py `set_manual_deletion_hold`.
    manual_deletion_hold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Per-source override of Settings.removal_confirmation_runs; None
    # falls back to the global default.
    removal_confirmation_runs: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # --- Trusted reconciliation baseline ---
    # Updated only when a *complete* run passes plausibility, or when
    # an operator explicitly promotes a run via
    # `sources.promote_run_to_trusted_baseline` — never merely because
    # a run was attempted. See docs/ingestion-framework.md.
    last_trusted_active_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    last_trusted_run_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ingestion_runs.id"), nullable=True
    )

    # High-water mark for incremental fetching (e.g. Chicago's
    # `updated_on > watermark` — see
    # docs/sources/chicago-ingestion-design.md "Incremental mode").
    # Persisted only after a successful *trusted* run — see
    # app/cli.py — so a failed or suspicious run never advances it and
    # a subsequent incremental fetch can't silently skip records.
    incremental_watermark: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Explicit `foreign_keys` needed: `last_trusted_run_id` above is a
    # second FK path to `ingestion_runs`, so SQLAlchemy can't infer
    # which one this relationship (the one-to-many "all runs for this
    # source") means without being told.
    ingestion_runs: Mapped[list["IngestionRun"]] = relationship(
        back_populates="source",
        cascade="all, delete-orphan",
        foreign_keys="IngestionRun.source_id",
    )
    source_records: Mapped[list["SourceRecord"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    incidents: Mapped[list["Incident"]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Source {self.source_key!r}>"
