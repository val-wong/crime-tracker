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
    neighborhood: Optional[str] = None,
    limit: int = 20,
) -> list[CategoryCount]:
    """Counts offenses (not incidents) per source_category -- an
    incident with two offenses of different categories should count
    toward both, which a per-incident count would miss."""
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
        category=None,
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
    limit: int = 10,
) -> list[NeighborhoodCount]:
    stmt = select(Incident.neighborhood, func.count().label("count")).select_from(Incident)
    stmt = apply_incident_filters(
        stmt,
        source_id=source_id,
        start_date=start_date,
        end_date=end_date,
        category=category,
        neighborhood=None,
    )
    stmt = stmt.where(Incident.neighborhood.isnot(None))
    stmt = stmt.group_by(Incident.neighborhood).order_by(func.count().desc()).limit(limit)
    return [NeighborhoodCount(neighborhood=row[0], count=row[1]) for row in db.execute(stmt).all()]
