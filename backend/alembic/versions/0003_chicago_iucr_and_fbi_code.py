"""Chicago IUCR reference table and Offense.fbi_code.

Adds a small, publisher-maintained lookup table for Chicago's IUCR
offense codes (source-agnostic in shape, but populated only for
Chicago in this phase), and a column to preserve each offense's own
`fbi_code` value. See docs/sources/chicago.md §7.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-07

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "iucr_codes",
        sa.Column("iucr", sa.String(length=10), primary_key=True),
        sa.Column("primary_description", sa.String(length=255), nullable=False),
        sa.Column("secondary_description", sa.String(length=255), nullable=False),
        sa.Column("index_code", sa.String(length=5), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.add_column("offenses", sa.Column("fbi_code", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("offenses", "fbi_code")
    op.drop_table("iucr_codes")
