"""Add Source.incremental_watermark.

Persists the high-water mark for incremental (`updated_on`-filtered)
fetching, per source. See docs/sources/chicago-ingestion-design.md
"Incremental mode": only ever advanced after a successful *trusted*
run (see app/cli.py), never after a partial or suspicious one.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-07

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sources", sa.Column("incremental_watermark", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("sources", "incremental_watermark")
