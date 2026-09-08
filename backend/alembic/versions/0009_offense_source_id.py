"""Denormalize Offense.source_id from its incident.

Real bug found and fixed during production-readiness hardening's
browser QA: `GET /api/status`'s `offense_count` (via
`count_offenses`) previously joined `offenses` to `incidents` just to
filter by source -- confirmed via EXPLAIN ANALYZE to take ~17 seconds
at full Chicago scale (a parallel sequential scan of both
multi-million-row tables feeding a hash join), the single largest
contributor to a ~30s total `/api/status` response. An offense always
belongs to exactly one incident, which always belongs to exactly one
source, so this is safe to denormalize directly onto `offenses` --
turning that query into a single indexed count.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-07

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("offenses", sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.execute(
        "UPDATE offenses SET source_id = incidents.source_id "
        "FROM incidents WHERE offenses.incident_id = incidents.id"
    )
    op.alter_column("offenses", "source_id", nullable=False)
    op.create_foreign_key(
        "fk_offenses_source_id_sources", "offenses", "sources", ["source_id"], ["id"]
    )
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_offenses_source_id ON offenses (source_id)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_offenses_source_id")
    op.drop_constraint("fk_offenses_source_id_sources", "offenses", type_="foreignkey")
    op.drop_column("offenses", "source_id")
