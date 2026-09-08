"""Aggregate queries for the summary/trend endpoint (see
app/api/summary.py). Operates on normalized incidents/offenses only --
never raw source records -- per docs/architecture.md "Trend/pattern
analysis... runs against the normalized data, not the raw snapshots."

Every count here is a **reported-incident count**, not a risk or
danger measure -- see docs/product.md "Safety & Ethics Constraints"
and the naming used throughout this module (`category_breakdown`, not
"risk_by_category"; `top_neighborhoods_by_incident_count`, not
"most dangerous neighborhoods").
"""

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Literal, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.incident import Incident
from app.models.offense import Offense
from app.repositories.incidents import apply_incident_filters

TimeBucket = Literal["week", "month"]


@dataclass(frozen=True)
class CategoryCount:
    category: Optional[str]
    count: int


@dataclass(frozen=True)
class TimeBucketCount:
    bucket_start: date
    count: int
    # True when this bucket's natural week/month span extends beyond
    # the source's real data coverage -- see
    # app/services/summary.py::_mark_partial_final_bucket. Always False
    # from this repository function itself; the service layer is what
    # knows the source's latest loaded date and sets this on the final
    # bucket when applicable.
    is_partial: bool = False


def latest_occurred_date(db: Session, *, source_id: Optional[uuid.UUID]) -> Optional[date]:
    """Latest `occurred_at` date currently loaded for this source (or
    across all sources if `source_id` is None) -- the anchor for the
    summary's default period and the boundary an explicit request's
    end_date gets clamped to (see app/services/summary.py::get_summary,
    "Explicit future dates"). A live source always lags behind the
    current wall-clock date by some amount."""
    stmt = select(func.max(Incident.occurred_at))
    stmt = apply_incident_filters(
        stmt,
        source_id=source_id,
        start_date=None,
        end_date=None,
        category=None,
        neighborhood=None,
    )
    result = db.execute(stmt).scalar_one_or_none()
    return result.date() if result is not None else None


@dataclass(frozen=True)
class NeighborhoodCount:
    neighborhood: Optional[str]
    count: int


def count_in_range(
    db: Session,
    *,
    source_id: Optional[uuid.UUID],
    start_date: Optional[date],
    end_date: Optional[date],
    category: Optional[str] = None,
    neighborhood: Optional[str] = None,
) -> int:
    stmt = select(func.count()).select_from(Incident)
    stmt = apply_incident_filters(
        stmt,
        source_id=source_id,
        start_date=start_date,
        end_date=end_date,
        category=category,
        neighborhood=neighborhood,
    )
    return db.execute(stmt).scalar_one()


def category_breakdown(
    db: Session,
    *,
    source_id: Optional[uuid.UUID],
    start_date: Optional[date],
    end_date: Optional[date],
    category: Optional[str] = None,
    neighborhood: Optional[str] = None,
    limit: int = 20,
) -> list[CategoryCount]:
    """Counts offenses (not incidents) per source_category -- an
    incident with two offenses of different categories should count
    toward both, which a per-incident count would miss.

    When `category` is given, this scopes to incidents matching that
    category first (same as every other summary query -- see
    `apply_incident_filters`), then breaks the *matching* incidents'
    offenses down by category. In the overwhelming majority of cases
    that yields a single row (the selected category itself); the one
    documented exception is an incident that also carries a *second*,
    different-category offense (e.g. one incident charged with both
    NARCOTICS and a WEAPONS VIOLATION) -- that incident still
    legitimately contributes a row under the other category too, by
    the same offense-level counting convention as the unfiltered case
    above. This is not the same bug as the breakdown ignoring the
    filter entirely: every row here still only counts incidents that
    matched `category` (and every other applied filter).
    """
    stmt = (
        select(Offense.source_category, func.count().label("count"))
        .select_from(Offense)
        .join(Incident, Offense.incident_id == Incident.id)
    )
    stmt = apply_incident_filters(
        stmt,
        source_id=source_id,
        start_date=start_date,
        end_date=end_date,
        category=category,
        neighborhood=neighborhood,
    )
    stmt = stmt.group_by(Offense.source_category).order_by(func.count().desc()).limit(limit)
    return [CategoryCount(category=row[0], count=row[1]) for row in db.execute(stmt).all()]


def incidents_by_time_bucket(
    db: Session,
    *,
    source_id: Optional[uuid.UUID],
    start_date: Optional[date],
    end_date: Optional[date],
    category: Optional[str] = None,
    neighborhood: Optional[str] = None,
    bucket: TimeBucket = "month",
) -> list[TimeBucketCount]:
    bucket_expr = func.date_trunc(bucket, Incident.occurred_at).label("bucket_start")
    stmt = select(bucket_expr, func.count().label("count")).select_from(Incident)
    stmt = apply_incident_filters(
        stmt,
        source_id=source_id,
        start_date=start_date,
        end_date=end_date,
        category=category,
        neighborhood=neighborhood,
    )
    stmt = stmt.where(Incident.occurred_at.isnot(None))
    stmt = stmt.group_by(bucket_expr).order_by(bucket_expr)
    return [
        TimeBucketCount(bucket_start=row.bucket_start.date(), count=row.count)
        for row in db.execute(stmt).all()
    ]


def top_neighborhoods_by_incident_count(
    db: Session,
    *,
    source_id: Optional[uuid.UUID],
    start_date: Optional[date],
    end_date: Optional[date],
    category: Optional[str] = None,
    neighborhood: Optional[str] = None,
    limit: int = 10,
) -> list[NeighborhoodCount]:
    """When `neighborhood` is already filtered, this necessarily
    collapses to (at most) that one neighborhood -- still correct, just
    not much of a "top" ranking anymore. Previously this ignored
    `neighborhood` entirely and always ranked across every
    neighborhood, which let it name a different neighborhood than the
    one actually selected -- a real filter-consistency bug (see
    app/services/summary.py::get_summary)."""
    stmt = select(Incident.neighborhood, func.count().label("count")).select_from(Incident)
    stmt = apply_incident_filters(
        stmt,
        source_id=source_id,
        start_date=start_date,
        end_date=end_date,
        category=category,
        neighborhood=neighborhood,
    )
    stmt = stmt.where(Incident.neighborhood.isnot(None))
    stmt = stmt.group_by(Incident.neighborhood).order_by(func.count().desc()).limit(limit)
    return [NeighborhoodCount(neighborhood=row[0], count=row[1]) for row in db.execute(stmt).all()]
