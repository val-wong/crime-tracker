"""Add incident_grid_rollup materialized view + rollup_refresh_runs log.

See docs/rollup-design.md for the full design rationale. Summary:

- Grain: (grid_size, cell_lon, cell_lat, month_bucket, category) where
  `category` is NULL for a category-agnostic row (true distinct
  incident count, no fan-out) and non-NULL for a per-category row
  (counts incident/offense pairs -- matches the existing convention in
  app/repositories/summary.py's `category_breakdown`, which also
  counts offenses rather than distinct incidents). Only two grid sizes
  are materialized (0.05, 0.02 -- the two widest/slowest zoom tiers
  confirmed by EXPLAIN ANALYZE in the map/dashboard phase); finer zoom
  levels keep using the raw-table path, which is fast enough there.
- Neighborhood is deliberately NOT a rollup dimension (see
  docs/rollup-design.md "Why neighborhood is excluded") -- a
  neighborhood-filtered aggregate request always falls back to the raw
  table, which is already fast for that filter.
- A plain table (not the view) tracks refresh history/status, mirroring
  the existing `ingestion_runs` pattern.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-07

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_GRID_CTE = """
with grid_source as (
    select
        0.05::double precision as grid_size,
        (floor(st_x(i.location::geometry) / 0.05) * 0.05 + 0.025)::double precision as cell_lon,
        (floor(st_y(i.location::geometry) / 0.05) * 0.05 + 0.025)::double precision as cell_lat,
        date_trunc('month', i.occurred_at)::date as month_bucket,
        i.id as incident_id
    from incidents i
    where i.location is not null and i.occurred_at is not null
    union all
    select
        0.02::double precision,
        (floor(st_x(i.location::geometry) / 0.02) * 0.02 + 0.01)::double precision,
        (floor(st_y(i.location::geometry) / 0.02) * 0.02 + 0.01)::double precision,
        date_trunc('month', i.occurred_at)::date,
        i.id
    from incidents i
    where i.location is not null and i.occurred_at is not null
)
"""

_CREATE_VIEW_SQL = f"""
create materialized view incident_grid_rollup as
{_GRID_CTE}
select grid_size, cell_lon, cell_lat, month_bucket, null::varchar(255) as category,
       count(*)::bigint as incident_count
from grid_source
group by grid_size, cell_lon, cell_lat, month_bucket

union all

select g.grid_size, g.cell_lon, g.cell_lat, g.month_bucket, o.source_category as category,
       count(*)::bigint as incident_count
from grid_source g
join offenses o on o.incident_id = g.incident_id
where o.source_category is not null
group by g.grid_size, g.cell_lon, g.cell_lat, g.month_bucket, o.source_category
with no data;
"""


def upgrade() -> None:
    op.execute(_CREATE_VIEW_SQL)
    # A unique index is required for REFRESH MATERIALIZED VIEW
    # CONCURRENTLY (see app/services/rollup.py) -- NULLS NOT DISTINCT
    # (Postgres 15+; this project runs 16) treats the category-agnostic
    # rows' NULL category as one distinguishable group per cell/month,
    # rather than each being "distinct" from every other NULL.
    op.execute(
        "create unique index ix_incident_grid_rollup_uniq on incident_grid_rollup "
        "(grid_size, cell_lon, cell_lat, month_bucket, category) nulls not distinct"
    )
    # Supports filtering by category (or its absence) and month range
    # without a full scan, even though the view is expected to be small.
    op.execute(
        "create index ix_incident_grid_rollup_lookup on incident_grid_rollup "
        "(grid_size, category, month_bucket)"
    )
    # First population -- REFRESH (not CONCURRENTLY, since no prior
    # data exists yet to keep serving) via the same path the CLI uses.
    op.execute("refresh materialized view incident_grid_rollup")

    op.create_table(
        "rollup_refresh_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum("running", "completed", "failed", name="rollup_refresh_status"),
            nullable=False,
        ),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Float(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_rollup_refresh_runs_started_at", "rollup_refresh_runs", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_rollup_refresh_runs_started_at", table_name="rollup_refresh_runs")
    op.drop_table("rollup_refresh_runs")
    op.execute("drop type rollup_refresh_status")
    op.execute("drop materialized view if exists incident_grid_rollup")
