"""Initial schema: sources, ingestion_runs, source_records,
raw_source_record_versions, incidents, offenses.

Revision ID: 0001
Revises:
Create Date: 2026-09-06

"""

import geoalchemy2
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    op.create_table(
        "sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("source_key", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("publisher", sa.String(length=255), nullable=True),
        sa.Column("source_url", sa.String(length=2048), nullable=True),
        sa.Column("timezone_convention", sa.String(length=100), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_key", name="uq_sources_source_key"),
    )

    op.create_table(
        "ingestion_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sources.id"), nullable=False
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum("running", "completed", "partial", "failed", name="ingestion_run_status"),
            nullable=False,
        ),
        sa.Column("records_received", sa.Integer(), nullable=False),
        sa.Column("records_created", sa.Integer(), nullable=False),
        sa.Column("records_changed", sa.Integer(), nullable=False),
        sa.Column("records_unchanged", sa.Integer(), nullable=False),
        sa.Column("records_missing", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("is_complete", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ingestion_runs_source_id", "ingestion_runs", ["source_id"])

    op.create_table(
        "source_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sources.id"), nullable=False
        ),
        sa.Column("external_record_id", sa.String(length=255), nullable=False),
        sa.Column("source_active", sa.Boolean(), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "removal_confidence",
            sa.Enum("none", "pending", "likely", "confirmed", name="removal_confidence"),
            nullable=False,
        ),
        sa.Column("consecutive_missing_runs", sa.Integer(), nullable=False),
        sa.Column("current_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_id", "external_record_id", name="uq_source_record_external_id"),
    )
    op.create_index("ix_source_records_source_id", "source_records", ["source_id"])

    op.create_table(
        "raw_source_record_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_record_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source_records.id"),
            nullable=False,
        ),
        sa.Column(
            "ingestion_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ingestion_runs.id"),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_raw_source_record_versions_source_record_id",
        "raw_source_record_versions",
        ["source_record_id"],
    )
    op.create_index(
        "ix_raw_source_record_versions_ingestion_run_id",
        "raw_source_record_versions",
        ["ingestion_run_id"],
    )

    op.create_table(
        "incidents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sources.id"), nullable=False
        ),
        sa.Column("external_incident_id", sa.String(length=255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("occurred_at_raw", sa.String(length=255), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reported_at_raw", sa.String(length=255), nullable=True),
        sa.Column("source_time_convention", sa.String(length=100), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column(
            "location",
            geoalchemy2.Geography(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=True,
        ),
        sa.Column(
            "location_precision",
            sa.Enum(
                "exact",
                "approximate",
                "block",
                "intersection",
                "suppressed",
                "unknown",
                name="location_precision",
            ),
            nullable=False,
        ),
        sa.Column("address_text", sa.String(length=500), nullable=True),
        sa.Column("neighborhood", sa.String(length=255), nullable=True),
        sa.Column("district", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source_id", "external_incident_id", name="uq_incident_external_id"),
    )
    op.create_index("ix_incidents_source_id", "incidents", ["source_id"])
    # GiST index for spatial queries (radius search, containment, etc.)
    # — created explicitly here since spatial_index=False above avoids
    # relying on GeoAlchemy2's automatic (and less predictable) index
    # creation ordering in a hand-written migration.
    op.execute("CREATE INDEX ix_incidents_location ON incidents USING GIST (location)")

    op.create_table(
        "offenses",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "incident_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("incidents.id"),
            nullable=False,
        ),
        sa.Column(
            "source_record_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("source_records.id"),
            nullable=True,
        ),
        sa.Column("external_offense_id", sa.String(length=255), nullable=False),
        sa.Column("raw_offense_code", sa.String(length=100), nullable=True),
        sa.Column("source_category", sa.String(length=255), nullable=True),
        sa.Column("source_subcategory", sa.String(length=255), nullable=True),
        sa.Column("normalized_category", sa.String(length=100), nullable=True),
        sa.Column("normalized_subcategory", sa.String(length=100), nullable=True),
        sa.Column("victim_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_offenses_incident_id", "offenses", ["incident_id"])
    op.create_index("ix_offenses_source_record_id", "offenses", ["source_record_id"])


def downgrade() -> None:
    op.drop_table("offenses")
    op.execute("DROP INDEX IF EXISTS ix_incidents_location")
    op.drop_table("incidents")
    op.drop_table("raw_source_record_versions")
    op.drop_table("source_records")
    op.drop_table("ingestion_runs")
    op.drop_table("sources")

    op.execute("DROP TYPE IF EXISTS location_precision")
    op.execute("DROP TYPE IF EXISTS removal_confidence")
    op.execute("DROP TYPE IF EXISTS ingestion_run_status")
