"""Mass-disappearance plausibility safeguard.

Adds per-source reconciliation-safety configuration and a trusted
reconciliation baseline pointer to ``sources``, and adds plausibility
tracking fields to ``ingestion_runs``. See
docs/ingestion-framework.md "Complete vs. trusted for deletion
inference" and docs/sources/chicago.md §5 for why.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-06

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- sources: reconciliation safety configuration + trusted baseline ---
    op.add_column("sources", sa.Column("min_expected_records", sa.Integer(), nullable=True))
    op.add_column("sources", sa.Column("max_record_count_drop_percent", sa.Float(), nullable=True))
    op.add_column(
        "sources",
        sa.Column(
            "mass_disappearance_protection_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.add_column(
        "sources",
        sa.Column("manual_deletion_hold", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("sources", sa.Column("removal_confirmation_runs", sa.Integer(), nullable=True))
    op.add_column("sources", sa.Column("last_trusted_active_count", sa.Integer(), nullable=True))
    op.add_column(
        "sources",
        sa.Column("last_trusted_run_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_sources_last_trusted_run_id",
        "sources",
        "ingestion_runs",
        ["last_trusted_run_id"],
        ["id"],
    )

    # --- ingestion_runs: plausibility tracking ---
    # Unlike op.create_table (used in 0001), op.add_column does NOT
    # auto-create a Postgres ENUM type for us — it must be created
    # explicitly first, with create_type=False on the column's type so
    # add_column doesn't then try (and fail) to create it a second time.
    plausibility_status_enum = postgresql.ENUM(
        "not_evaluated",
        "plausible",
        "suspicious",
        "overridden_trusted",
        name="plausibility_status",
    )
    plausibility_status_enum.create(op.get_bind(), checkfirst=True)

    plausibility_block_reason_enum = postgresql.ENUM(
        "schema_validation_failed",
        "manual_safety_hold",
        "source_count_mismatch",
        "record_count_below_absolute_floor",
        "record_count_drop_exceeds_threshold",
        name="plausibility_block_reason",
    )
    plausibility_block_reason_enum.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "ingestion_runs",
        sa.Column(
            "plausibility_status",
            postgresql.ENUM(name="plausibility_status", create_type=False),
            nullable=False,
            server_default="not_evaluated",
        ),
    )
    op.add_column(
        "ingestion_runs",
        sa.Column(
            "blocking_reason",
            postgresql.ENUM(name="plausibility_block_reason", create_type=False),
            nullable=True,
        ),
    )
    op.add_column(
        "ingestion_runs",
        sa.Column(
            "deletion_evidence_allowed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column("ingestion_runs", sa.Column("baseline_count_at_run", sa.Integer(), nullable=True))
    op.add_column(
        "ingestion_runs", sa.Column("percent_change_from_baseline", sa.Float(), nullable=True)
    )
    op.add_column(
        "ingestion_runs",
        sa.Column("promoted_to_baseline_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ingestion_runs", "promoted_to_baseline_at")
    op.drop_column("ingestion_runs", "percent_change_from_baseline")
    op.drop_column("ingestion_runs", "baseline_count_at_run")
    op.drop_column("ingestion_runs", "deletion_evidence_allowed")
    op.drop_column("ingestion_runs", "blocking_reason")
    op.drop_column("ingestion_runs", "plausibility_status")

    op.drop_constraint("fk_sources_last_trusted_run_id", "sources", type_="foreignkey")
    op.drop_column("sources", "last_trusted_run_id")
    op.drop_column("sources", "last_trusted_active_count")
    op.drop_column("sources", "removal_confirmation_runs")
    op.drop_column("sources", "manual_deletion_hold")
    op.drop_column("sources", "mass_disappearance_protection_enabled")
    op.drop_column("sources", "max_record_count_drop_percent")
    op.drop_column("sources", "min_expected_records")

    op.execute("DROP TYPE IF EXISTS plausibility_block_reason")
    op.execute("DROP TYPE IF EXISTS plausibility_status")
