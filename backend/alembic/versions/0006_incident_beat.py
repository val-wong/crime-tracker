"""Add Incident.beat.

Chicago's `beat` field (a finer-grained police-assignment area than
`district`) was already fetched, canonicalized, and fingerprinted by
the adapter but never normalized into a column -- the incident-detail
panel needs it (see docs/sources/chicago.md and the map/dashboard
phase's incident-detail requirements). `ADD COLUMN ... NULL` is a fast
metadata-only change in Postgres (no table rewrite), safe to run
without CONCURRENTLY even against a table receiving concurrent writes.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-06

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("incidents", sa.Column("beat", sa.String(length=100), nullable=True))


def downgrade() -> None:
    op.drop_column("incidents", "beat")
