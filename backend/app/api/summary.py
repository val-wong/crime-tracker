"""Summary/trend API.

Reported-incident counts and comparisons only -- see
app/services/summary.py and docs/product.md "Safety & Ethics
Constraints" for why this deliberately never characterizes counts as
risk, danger, or safety.

Scoped to the Chicago source explicitly (rather than left unscoped like
the base `/api/incidents` list) since a trend comparison mixing sources
would be misleading before multi-source normalization exists (see
docs/product.md "Future Phases").
"""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.constants import CHICAGO_SOURCE_KEY
from app.db.session import get_db
from app.repositories import sources as sources_repo
from app.schemas.summary import (
    CategoryBreakdownItem,
    NeighborhoodItem,
    SummaryResponse,
    TimeBucketItem,
)
from app.services.summary import get_summary

router = APIRouter(prefix="/api/summary", tags=["summary"])


@router.get("", response_model=SummaryResponse)
def summary(
    db: Session = Depends(get_db),
    start_date: Optional[date] = Query(None, description="Defaults to 30 days before end_date"),
    end_date: Optional[date] = Query(
        None,
        description=(
            "Defaults to the source's latest loaded occurrence date, not today's date. "
            "A value past that latest date is clamped to it (see the response's "
            "end_date_clamped and end_date fields)."
        ),
    ),
    category: Optional[str] = Query(None),
    neighborhood: Optional[str] = Query(None),
) -> SummaryResponse:
    source = sources_repo.get_by_key(db, CHICAGO_SOURCE_KEY)
    source_id = source.id if source is not None else None

    result = get_summary(
        db,
        source_id=source_id,
        start_date=start_date,
        end_date=end_date,
        category=category,
        neighborhood=neighborhood,
    )
    p = result.period
    most_common_category = (
        result.category_breakdown[0].category if result.category_breakdown else None
    )
    most_represented_neighborhood = (
        result.top_neighborhoods[0].neighborhood if result.top_neighborhoods else None
    )

    return SummaryResponse(
        start_date=p.start_date,
        end_date=p.end_date,
        reported_incidents=p.current_count,
        previous_period_start_date=p.previous_start_date,
        previous_period_end_date=p.previous_end_date,
        previous_period_reported_incidents=p.previous_count,
        percent_change=p.percent_change,
        end_date_clamped=p.end_date_clamped,
        category_breakdown=[
            CategoryBreakdownItem(category=c.category, count=c.count)
            for c in result.category_breakdown
        ],
        most_common_category=most_common_category,
        time_bucket=result.time_bucket,
        incidents_by_time=[
            TimeBucketItem(bucket_start=t.bucket_start, count=t.count, is_partial=t.is_partial)
            for t in result.incidents_by_time
        ],
        top_neighborhoods=[
            NeighborhoodItem(neighborhood=n.neighborhood, count=n.count)
            for n in result.top_neighborhoods
        ],
        most_represented_neighborhood=most_represented_neighborhood,
    )
