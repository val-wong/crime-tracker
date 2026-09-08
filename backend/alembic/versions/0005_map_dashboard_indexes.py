"""Add indexes needed by the map/dashboard's query patterns.

Justified by real EXPLAIN ANALYZE measurements at the current 500K-row
scale (see docs/operations/chicago-ingestion.md "Performance"): a
one-year `occurred_at` range filter, an `offenses.source_category`
equality filter, and an `incidents.neighborhood` equality filter each
cost 40-150ms via sequential scan at 500K rows -- at the full ~8.6M-row
scale (~17x) that projects into low-seconds territory per request,
too slow for an interactive map issuing several filtered queries per
viewport change. `incidents.location` already has a GIST index
(migration 0001) and was confirmed fast via bounding-box/radius
queries; no change needed there.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-06

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # CONCURRENTLY so this never takes a blocking lock against writes
    # from an in-progress ingestion run (a full Chicago reconciliation
    # can run for hours) -- each statement must run outside a
    # transaction block, hence autocommit_block(). IF NOT EXISTS makes
    # this safe to re-run if a prior attempt was interrupted partway
    # through (confirmed necessary in practice: a real full-reconcile
    # run's single long-lived transaction -- see
    # docs/operations/chicago-ingestion.md "Concurrent schema changes
    # during a full reconciliation" -- forced exactly this retry once
    # during this phase's own validation).
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_incidents_occurred_at "
            "ON incidents (occurred_at)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_incidents_neighborhood "
            "ON incidents (neighborhood)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_offenses_source_category "
            "ON offenses (source_category)"
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_offenses_source_category")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_incidents_neighborhood")
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_incidents_occurred_at")
