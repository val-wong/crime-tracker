"""Fix ix_incidents_occurred_at to match the query's actual sort order.

Real bug found via performance investigation against the complete
~8.63M-row Chicago dataset (see docs/map-aggregation.md and
docs/sources/chicago-ingestion-design.md "Performance"): the default,
unfiltered `GET /api/incidents` request took ~2 seconds -- traced via
EXPLAIN ANALYZE to a full parallel sequential scan + sort, even though
migration 0005 already added a plain `(occurred_at)` btree index.

Root cause: `app/repositories/incidents.py`'s `list_incidents` orders
by `Incident.occurred_at.desc().nulls_last()` -- i.e.
`ORDER BY occurred_at DESC NULLS LAST`. A plain ascending btree index's
two natural traversal orders are `ASC NULLS LAST` (forward) and
`DESC NULLS FIRST` (backward) -- `DESC NULLS LAST` matches neither, so
Postgres cannot use the index for this sort and falls back to scanning
and sorting the whole table. Confirmed directly: rebuilding the index
with the exact matching sort order changes the query from a ~1.2s
parallel seq scan + sort to a sub-10ms backward index scan.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-07

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_incidents_occurred_at_desc_nulls_last "
            "ON incidents (occurred_at DESC NULLS LAST)"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_incidents_occurred_at")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_incidents_occurred_at "
            "ON incidents (occurred_at)"
        )
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_incidents_occurred_at_desc_nulls_last")
