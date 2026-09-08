"""Response schema for GET /api/summary.

Field names are deliberately neutral -- "reported incidents", not
"risk" or "danger" -- per docs/product.md. See app/services/summary.py
for the calculations behind these fields.
"""

from datetime import date
from typing import Optional

from pydantic import BaseModel


class CategoryBreakdownItem(BaseModel):
    category: Optional[str] = None
    count: int


class TimeBucketItem(BaseModel):
    bucket_start: date
    count: int
    # True when the source's real data coverage ends before this
    # bucket's natural week/month span -- see
    # app/services/summary.py::_mark_partial_final_bucket. Only ever
    # set on the final item in `incidents_by_time`.
    is_partial: bool = False


class NeighborhoodItem(BaseModel):
    neighborhood: Optional[str] = None
    # Official Chicago community area name for `neighborhood`'s code --
    # see app/chicago_community_areas.py. `None` when `neighborhood` is
    # `None` or isn't a recognized code; `neighborhood` itself is always
    # preserved unchanged for filtering/querying.
    name: Optional[str] = None
    count: int


class SummaryResponse(BaseModel):
    start_date: date
    end_date: date
    reported_incidents: int
    previous_period_start_date: date
    previous_period_end_date: date
    previous_period_reported_incidents: int
    percent_change: Optional[float] = None
    # True when a requested (or defaulted) end_date reached past the
    # source's real data coverage and was pulled back to it -- see
    # app/services/summary.py::get_summary "Explicit user-selected
    # future dates". `end_date` above is always the *effective*
    # (already-clamped) date that was actually analyzed.
    end_date_clamped: bool = False

    category_breakdown: list[CategoryBreakdownItem] = []
    most_common_category: Optional[str] = None

    time_bucket: str  # "week" or "month"
    incidents_by_time: list[TimeBucketItem] = []

    top_neighborhoods: list[NeighborhoodItem] = []
    most_represented_neighborhood: Optional[str] = None
    # Official name for `most_represented_neighborhood`'s code -- added
    # alongside the existing code field (backward compatible; existing
    # callers filtering/querying by the numeric code are unaffected).
    # `None` when there's no most-represented neighborhood, or its code
    # isn't a recognized Chicago community area.
    most_represented_neighborhood_name: Optional[str] = None
