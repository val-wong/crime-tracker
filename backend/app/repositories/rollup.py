"""Query functions for `incident_grid_rollup` (see docs/rollup-design.md).

Kept separate from app/repositories/incidents.py's raw-table
`aggregate_incidents` -- the API layer (app/api/incidents.py) decides
which one to call per request.
"""

from datetime import date, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.repositories.incidents import BoundingBox, GridCell

# The two grid sizes actually materialized in incident_grid_rollup --
# see docs/rollup-design.md "Which grid sizes are materialized".
ROLLUP_GRID_SIZES = (0.05, 0.02)


def is_month_aligned_range(start_date: Optional[date], end_date: Optional[date]) -> bool:
    """True when there's no date filter at all, or both bounds exactly
    cover whole calendar months (see docs/rollup-design.md "Date-range
    fallback rule") -- the only cases the rollup can answer exactly."""
    if start_date is None and end_date is None:
        return True
    if start_date is None or end_date is None:
        return False
    if start_date.day != 1:
        return False
    # end_date must be the last day of its month.
    next_month = end_date.replace(day=28) + timedelta(days=4)
    last_day_of_month = next_month - timedelta(days=next_month.day)
    return end_date == last_day_of_month


def query_rollup(
    db: Session,
    *,
    grid_degrees: float,
    start_date: Optional[date],
    end_date: Optional[date],
    category: Optional[str],
    bbox: Optional[BoundingBox],
    max_cells: int = 5000,
) -> list[GridCell]:
    """Sum `incident_count` from the rollup for the given filters.

    Callers must have already confirmed eligibility (grid size is one
    of ROLLUP_GRID_SIZES, no neighborhood filter, and the date range is
    month-aligned per `is_month_aligned_range`) -- this function trusts
    that and does not re-check it, to keep it a pure query function.
    """
    where_clauses = ["grid_size = :grid_size"]
    params: dict = {"grid_size": grid_degrees, "max_cells": max_cells}

    if category is not None:
        where_clauses.append("category = :category")
        params["category"] = category
    else:
        where_clauses.append("category IS NULL")

    if start_date is not None:
        # A bind parameter immediately followed by a `::cast` (no
        # space) is a real, confirmed SQLAlchemy `text()` parsing trap:
        # it fails to recognize `:start_date` as a bindparam token at
        # all, silently drops it from the params it sends to the
        # driver, and leaves the literal `:start_date::date` in the
        # SQL for Postgres to choke on. No cast is needed here anyway
        # -- `start_date` is already a real `datetime.date`, which
        # `date_trunc` accepts directly.
        where_clauses.append("month_bucket >= date_trunc('month', :start_date)")
        params["start_date"] = start_date
    if end_date is not None:
        where_clauses.append("month_bucket <= date_trunc('month', :end_date)")
        params["end_date"] = end_date

    if bbox is not None:
        where_clauses.append(
            "cell_lon >= :min_lon AND cell_lon <= :max_lon "
            "AND cell_lat >= :min_lat AND cell_lat <= :max_lat"
        )
        params["min_lon"] = bbox.min_lon
        params["max_lon"] = bbox.max_lon
        params["min_lat"] = bbox.min_lat
        params["max_lat"] = bbox.max_lat

    sql = f"""
        SELECT cell_lon, cell_lat, sum(incident_count)::bigint AS count
        FROM incident_grid_rollup
        WHERE {" AND ".join(where_clauses)}
        GROUP BY cell_lon, cell_lat
        ORDER BY count DESC
        LIMIT :max_cells
    """
    rows = db.execute(text(sql), params).all()
    return [GridCell(lon=row.cell_lon, lat=row.cell_lat, count=row.count) for row in rows]


def category_breakdown(
    db: Session,
    *,
    start_date: Optional[date],
    end_date: Optional[date],
    limit: int = 20,
) -> list[tuple[Optional[str], int]]:
    """Sum `incident_count` by category across all cells and both
    materialized grid tiers -- either tier alone already covers every
    incident exactly once, so summing across both would double-count;
    a single fixed tier is used (0.05, arbitrarily -- both cover the
    same underlying data). Category rows count incident-offense pairs,
    identical to `app.repositories.summary.category_breakdown`'s own
    documented convention (see docs/rollup-design.md) -- this is a pure
    substitution, not a semantic change.

    Callers must have already confirmed eligibility (no category
    filter -- this function has no way to scope to one category --, no
    neighborhood filter, month-aligned date range per
    `is_month_aligned_range`; see app/services/summary.py's
    `_category_breakdown` for the gating logic).

    Real evidence for this fix (see docs/performance-validation.md):
    the raw-table equivalent (`offenses ⋈ incidents`) took ~24s at full
    Chicago scale because counting by category requires touching every
    offense row for a non-selective category -- Postgres correctly
    avoids using `ix_offenses_source_category` for a category matching
    >20% of the table, since a sequential scan is genuinely cheaper
    there than an index scan. The rollup avoids the problem entirely by
    already being pre-aggregated to ~1M rows. Measured: 76ms warm
    (~300x faster) against the identical real date range.
    """
    grid_size = ROLLUP_GRID_SIZES[0]
    where_clauses = ["grid_size = :grid_size", "category IS NOT NULL"]
    params: dict = {"grid_size": grid_size, "limit": limit}

    if start_date is not None:
        # See the matching comment in `query_rollup` above -- no `::`
        # cast needed or wanted here (it silently breaks SQLAlchemy's
        # `text()` bindparam parsing).
        where_clauses.append("month_bucket >= date_trunc('month', :start_date)")
        params["start_date"] = start_date
    if end_date is not None:
        where_clauses.append("month_bucket <= date_trunc('month', :end_date)")
        params["end_date"] = end_date

    sql = f"""
        SELECT category, sum(incident_count)::bigint AS count
        FROM incident_grid_rollup
        WHERE {" AND ".join(where_clauses)}
        GROUP BY category
        ORDER BY count DESC
        LIMIT :limit
    """
    rows = db.execute(text(sql), params).all()
    return [(row.category, row.count) for row in rows]
