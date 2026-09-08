"""Every attempted synchronization against a source.

A run's ``is_complete`` flag is the single source of truth for whether
its absence-of-a-record signal can be trusted as removal evidence.
``status`` is a richer, human-facing summary; ``is_complete`` is the
one field reconciliation logic actually branches on — see
docs/ingestion-framework.md.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, Text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPKMixin
from app.models.enums import IngestionRunStatus, PlausibilityBlockReason, PlausibilityStatus

if TYPE_CHECKING:
    from app.models.raw_version import RawSourceRecordVersion
    from app.models.source import Source


class IngestionRun(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "ingestion_runs"

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False, index=True
    )

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[IngestionRunStatus] = mapped_column(
        SAEnum(
            IngestionRunStatus,
            name="ingestion_run_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=IngestionRunStatus.RUNNING,
    )

    records_received: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_changed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_unchanged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_missing: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # True only when this run fetched the *entire* current source
    # dataset successfully end-to-end. Never set True for a run that
    # errored partway through — a partial run must never be treated as
    # evidence that an unseen record was deleted by the source.
    is_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # --- Mass-disappearance plausibility safeguard ---
    # `is_complete` above answers "did OUR fetch succeed end-to-end".
    # These answer a distinct question this platform learned it needs
    # to ask separately: "even though the fetch succeeded, is this
    # snapshot plausible enough to trust as deletion evidence and as
    # the next reconciliation baseline". See
    # docs/ingestion-framework.md "Complete vs. trusted for deletion
    # inference" and docs/sources/chicago.md §5 for the real incident
    # (a publisher-side pipeline bug truncated an entire dataset to a
    # handful of rows while still returning a successful response)
    # that motivated adding this as its own dimension rather than
    # folding it into `is_complete`.
    plausibility_status: Mapped[PlausibilityStatus] = mapped_column(
        SAEnum(
            PlausibilityStatus,
            name="plausibility_status",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=PlausibilityStatus.NOT_EVALUATED,
    )
    blocking_reason: Mapped[Optional[PlausibilityBlockReason]] = mapped_column(
        SAEnum(
            PlausibilityBlockReason,
            name="plausibility_block_reason",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=True,
    )
    # The field reconciliation logic actually branches on for the
    # "missing record" step — mirrors how `is_complete`, not `status`,
    # is what the state machine trusts. True only when is_complete AND
    # the plausibility checks below all passed (or were manually
    # overridden).
    deletion_evidence_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Diagnostic/observability snapshot: what this run was compared
    # against, and by how much it differed. Populated whenever a
    # trusted baseline exists, regardless of whether this particular
    # run passed or failed its plausibility check.
    baseline_count_at_run: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    percent_change_from_baseline: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Set whenever this run becomes (automatically or via manual
    # override) the source's trusted baseline. Left null otherwise —
    # lets you audit which runs have ever served as the baseline over
    # time, not just whichever one is current.
    promoted_to_baseline_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Explicit `foreign_keys` needed here: Source also has a
    # `last_trusted_run_id` FK pointing back at this table (the
    # reconciliation-baseline pointer), so there are now two FK paths
    # between these two tables and SQLAlchemy can't infer which one
    # this relationship means without being told.
    source: Mapped["Source"] = relationship(
        back_populates="ingestion_runs", foreign_keys=[source_id]
    )
    raw_versions: Mapped[list["RawSourceRecordVersion"]] = relationship(
        back_populates="ingestion_run"
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<IngestionRun {self.id} status={self.status}>"
