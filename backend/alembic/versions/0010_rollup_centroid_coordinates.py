"""Add centroid_lon/centroid_lat to incident_grid_rollup.

Fixes a real, confirmed map-quality bug: aggregate circles at wide/
medium zoom were labeled with the *geometric* grid-cell center
(`floor(lon/grid)*grid + grid/2`), computed independent of where the
contributing incidents actually sit within that cell. Along Chicago's
shoreline, a cell's geometric center can fall in Lake Michigan even
when every single contributing incident is on land -- confirmed
directly against the real dataset: the 0.05°-grid cell centered at
(-87.525, 41.775) has 3,514 real incidents, every one at longitude
<= -87.5414 (west/land side), yet its geometric center (-87.525) sits
*east* of all of them, out over open water.

Fix: precompute the centroid (`avg`) of each cell's actual contributing
incident coordinates *during rollup refresh*, not at request time --
this is the same aggregation pass already computing `incident_count`
(one extra `avg()` per group, not an additional scan or join), so the
refresh-time cost is expected to be immaterial. `cell_lon`/`cell_lat`
keep their existing meaning and role (grid-bucket identity, bbox
filtering in app/repositories/rollup.py) completely unchanged --
`centroid_lon`/`centroid_lat` are purely additive columns. See
app/repositories/incidents.py's `aggregate_incidents` for the matching
raw-path fix, and docs/map-aggregation.md for context.

Materialized views cannot be altered to add a column in place (no
`ALTER MATERIALIZED VIEW ... ADD COLUMN`) -- the view's shape is fully
determined by the query that defines it, so adding a column requires
dropping and recreating it, which means a full rebuild scanning
`incidents`/`offenses` again (the same cost class as migration 0008's
original build). Real measured cost on the complete ~8.63M-row/
8.53M-with-location dataset: `REFRESH MATERIALIZED VIEW CONCURRENTLY`
on the *unchanged* view took 313s immediately before this migration
was written, so a comparable duration is expected here (see
docs/performance-validation.md's rollup section for the pattern of
recording real measured migration durations honestly, and the
production-readiness lesson that a migration rewriting a large
materialized relation should be followed by `VACUUM ANALYZE`).
Storage impact: two extra `double precision` columns across ~1.04M
rollup rows is on the order of 16 MB before page overhead -- negligible
against the existing ~191 MB view.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-08

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

_GRID_CTE = """
with grid_source as (
    select
        0.05::double precision as grid_size,
        (floor(st_x(i.location::geometry) / 0.05) * 0.05 + 0.025)::double precision as cell_lon,
        (floor(st_y(i.location::geometry) / 0.05) * 0.05 + 0.025)::double precision as cell_lat,
        st_x(i.location::geometry)::double precision as point_lon,
        st_y(i.location::geometry)::double precision as point_lat,
        date_trunc('month', i.occurred_at)::date as month_bucket,
        i.id as incident_id
    from incidents i
    where i.location is not null and i.occurred_at is not null
    union all
    select
        0.02::double precision,
        (floor(st_x(i.location::geometry) / 0.02) * 0.02 + 0.01)::double precision,
        (floor(st_y(i.location::geometry) / 0.02) * 0.02 + 0.01)::double precision,
        st_x(i.location::geometry)::double precision,
        st_y(i.location::geometry)::double precision,
        date_trunc('month', i.occurred_at)::date,
        i.id
    from incidents i
    where i.location is not null and i.occurred_at is not null
)
"""

_ORIGINAL_GRID_CTE = """
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
       count(*)::bigint as incident_count,
       avg(point_lon)::double precision as centroid_lon,
       avg(point_lat)::double precision as centroid_lat
from grid_source
group by grid_size, cell_lon, cell_lat, month_bucket

union all

select g.grid_size, g.cell_lon, g.cell_lat, g.month_bucket, o.source_category as category,
       count(*)::bigint as incident_count,
       avg(g.point_lon)::double precision as centroid_lon,
       avg(g.point_lat)::double precision as centroid_lat
from grid_source g
join offenses o on o.incident_id = g.incident_id
where o.source_category is not null
group by g.grid_size, g.cell_lon, g.cell_lat, g.month_bucket, o.source_category
with no data;
"""

_ORIGINAL_VIEW_SQL = f"""
create materialized view incident_grid_rollup as
{_ORIGINAL_GRID_CTE}
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


def _recreate_view(create_sql: str) -> None:
    op.execute("drop materialized view if exists incident_grid_rollup")
    op.execute(create_sql)
    # Same index definitions as migration 0008 -- dropping the view
    # drops them, so they must be recreated identically either way.
    op.execute(
        "create unique index ix_incident_grid_rollup_uniq on incident_grid_rollup "
        "(grid_size, cell_lon, cell_lat, month_bucket, category) nulls not distinct"
    )
    op.execute(
        "create index ix_incident_grid_rollup_lookup on incident_grid_rollup "
        "(grid_size, category, month_bucket)"
    )
    # Non-concurrent, matching 0008 -- there is no prior data in the
    # just-recreated view to keep serving during this refresh.
    op.execute("refresh materialized view incident_grid_rollup")


def upgrade() -> None:
    _recreate_view(_CREATE_VIEW_SQL)


def downgrade() -> None:
    _recreate_view(_ORIGINAL_VIEW_SQL)
