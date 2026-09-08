"""Aggregate trend calculations for the dashboard's summary cards and
charts (see app/api/summary.py).

Every value here is a **raw reported-incident/offense count**. Nothing
in this module computes or implies a risk, danger, or safety score —
see docs/product.md "Safety & Ethics Constraints": "Do not characterize
raw counts as risk... more police reports does not mean an area is
inherently more dangerous."
"""

from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Optional
from uuid import UUID

from sqlalchemy.orm import Session

from app.repositories import rollup as rollup_repo
from app.repositories import summary as summary_repo
from app.repositories.summary import CategoryCount, NeighborhoodCount, TimeBucketCount

DEFAULT_PERIOD_DAYS = 30
# See docs/map-aggregation.md-style reasoning: a fixed, documented
# breakpoint rather than a continuous formula, so behavior is
# predictable. A period longer than this uses monthly buckets so a
# multi-year query doesn't return thousands of weekly points.
WEEKLY_BUCKET_MAX_DAYS = 60


@dataclass(frozen=True)
class PeriodComparison:
    start_date: date
    end_date: date
    current_count: int
    previous_start_date: date
    previous_end_date: date
    previous_count: int
    percent_change: Optional[float]  # None when previous_count == 0 (undefined ratio)
    # True when the caller's (explicit or defaulted) end_date was later
    # than the source's own latest loaded occurrence date and had to be
    # pulled back to it -- see `get_summary`'s "Explicit future dates"
    # handling below. `end_date` above already reflects the *effective*
    # (clamped) date actually analyzed, not the raw request.
    end_date_clamped: bool


@dataclass(frozen=True)
class SummaryResult:
    period: PeriodComparison
    category_breakdown: list[CategoryCount]
    incidents_by_time: list[TimeBucketCount]
    time_bucket: str
    top_neighborhoods: list[NeighborhoodCount]


def _default_period(anchor: date) -> tuple[date, date]:
    """The default period ends at `anchor`, not the caller's wall-clock
    date -- see `get_summary`, which passes the source's own latest
    loaded occurrence date here. A live source always lags "now" by
    some amount (ingestion cadence, publication delay); anchoring on
    `date.today()` instead would silently count zero-data days as part
    of the "current" period, understating it and making the
    previous-period comparison look artificially negative even though
    nothing about real reported crime actually changed.
    """
    start = anchor - timedelta(days=DEFAULT_PERIOD_DAYS)
    return start, anchor


def _previous_period(start_date: date, end_date: date) -> tuple[date, date]:
    span = (end_date - start_date).days
    previous_end = start_date - timedelta(days=1)
    previous_start = previous_end - timedelta(days=span)
    return previous_start, previous_end


def _bucket_natural_end(bucket_start: date, bucket: str) -> date:
    """The last calendar day a `date_trunc('week'|'month', ...)` bucket
    actually spans -- used to tell whether the final bucket in a trend
    series is only partially covered by real source data (see
    `_mark_partial_final_bucket`)."""
    if bucket == "week":
        # Postgres's date_trunc('week', ...) truncates to Monday (ISO
        # 8601 week) -- see incidents_by_time_bucket. A week is 7 days.
        return bucket_start + timedelta(days=6)
    # "month"
    if bucket_start.month == 12:
        next_month_start = date(bucket_start.year + 1, 1, 1)
    else:
        next_month_start = date(bucket_start.year, bucket_start.month + 1, 1)
    return next_month_start - timedelta(days=1)


def _mark_partial_final_bucket(
    buckets: list[TimeBucketCount], *, bucket: str, latest_available: Optional[date]
) -> list[TimeBucketCount]:
    """Flag the final bucket as partial when the source's real data
    coverage ends before that bucket's natural end -- e.g. the default
    period now always ends exactly on the latest loaded date (see
    `_default_period`), which essentially never falls precisely on a
    week/month boundary, so the final bucket in a trend series is
    almost always incomplete. Only the last bucket can ever be partial:
    every earlier one is, by the query's own ascending order, already
    fully in the past relative to it."""
    if not buckets or latest_available is None:
        return buckets
    last = buckets[-1]
    if _bucket_natural_end(last.bucket_start, bucket) > latest_available:
        return [*buckets[:-1], replace(last, is_partial=True)]
    return buckets


def _category_breakdown(
    db: Session,
    *,
    source_id: Optional[UUID],
    start_date: date,
    end_date: date,
    category: Optional[str],
    neighborhood: Optional[str],
) -> list[CategoryCount]:
    """Prefer the precomputed rollup (see docs/rollup-design.md and
    docs/performance-validation.md "Optimize /api/summary") whenever it
    can answer exactly: no category filter and no neighborhood filter
    (neither is a rollup dimension this query can slice by) and a
    month-aligned date range. This is a pure substitution, not a
    semantic change -- the rollup's category rows already count
    incident-offense pairs, identical to the raw query's own documented
    convention. Real evidence: ~24s (raw, full-table join) vs. ~76ms
    (rollup) for the same real one-year range at full Chicago scale --
    a category like THEFT matches too large a fraction of `offenses`
    for any index to help the raw path; falls back to the raw query
    for anything the rollup can't serve exactly (a non-month-aligned
    range, a category filter, or a neighborhood filter).

    The category filter matters here specifically because a prior
    version of this function never threaded `category` through to
    either query path at all -- the breakdown (and therefore
    `most_common_category`) silently ignored a selected category
    filter entirely, which could report a category the caller had
    explicitly filtered out. See app/repositories/summary.py's
    `category_breakdown` for how `category` is now applied.
    """
    if (
        category is None
        and neighborhood is None
        and rollup_repo.is_month_aligned_range(start_date, end_date)
    ):
        rows = rollup_repo.category_breakdown(db, start_date=start_date, end_date=end_date)
        return [CategoryCount(category=cat, count=count) for cat, count in rows]
    return summary_repo.category_breakdown(
        db,
        source_id=source_id,
        start_date=start_date,
        end_date=end_date,
        category=category,
        neighborhood=neighborhood,
    )


def get_summary(
    db: Session,
    *,
    source_id: Optional[UUID],
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    category: Optional[str] = None,
    neighborhood: Optional[str] = None,
) -> SummaryResult:
    # The source's own latest loaded occurrence date -- the anchor for
    # the default period (see `_default_period`) and the boundary an
    # explicit request gets clamped to below. `None` only when this
    # source has no incidents loaded at all yet (e.g. a fresh dev DB).
    latest_available = summary_repo.latest_occurred_date(db, source_id=source_id)

    if start_date is None or end_date is None:
        anchor = latest_available if latest_available is not None else date.today()
        default_start, default_end = _default_period(anchor)
        start_date = start_date or default_start
        end_date = end_date or default_end

    # Explicit future dates: a request whose end_date reaches past the
    # source's real coverage must not silently report those uncovered
    # days as zero
    # incidents -- that reads as "confirmed zero crime" when it's
    # actually just "not ingested yet". Clamp the *analyzed* period to
    # what the source actually covers, and say so explicitly via
    # `end_date_clamped` -- `end_date` in the response below already
    # reflects this effective (clamped) date, not the raw request, so
    # the period a caller sees is always the period that was truly
    # analyzed.
    end_date_clamped = latest_available is not None and end_date > latest_available
    effective_end_date = latest_available if end_date_clamped else end_date

    previous_start, previous_end = _previous_period(start_date, effective_end_date)

    current_count = summary_repo.count_in_range(
        db,
        source_id=source_id,
        start_date=start_date,
        end_date=effective_end_date,
        category=category,
        neighborhood=neighborhood,
    )
    previous_count = summary_repo.count_in_range(
        db,
        source_id=source_id,
        start_date=previous_start,
        end_date=previous_end,
        category=category,
        neighborhood=neighborhood,
    )
    percent_change = (
        None
        if previous_count == 0
        else round((current_count - previous_count) / previous_count * 100, 1)
    )

    time_bucket = (
        "week" if (effective_end_date - start_date).days <= WEEKLY_BUCKET_MAX_DAYS else "month"
    )

    incidents_by_time = summary_repo.incidents_by_time_bucket(
        db,
        source_id=source_id,
        start_date=start_date,
        end_date=effective_end_date,
        category=category,
        neighborhood=neighborhood,
        bucket=time_bucket,
    )
    incidents_by_time = _mark_partial_final_bucket(
        incidents_by_time, bucket=time_bucket, latest_available=latest_available
    )

    return SummaryResult(
        period=PeriodComparison(
            start_date=start_date,
            end_date=effective_end_date,
            current_count=current_count,
            previous_start_date=previous_start,
            previous_end_date=previous_end,
            previous_count=previous_count,
            percent_change=percent_change,
            end_date_clamped=end_date_clamped,
        ),
        category_breakdown=_category_breakdown(
            db,
            source_id=source_id,
            start_date=start_date,
            end_date=effective_end_date,
            category=category,
            neighborhood=neighborhood,
        ),
        incidents_by_time=incidents_by_time,
        time_bucket=time_bucket,
        top_neighborhoods=summary_repo.top_neighborhoods_by_incident_count(
            db,
            source_id=source_id,
            start_date=start_date,
            end_date=effective_end_date,
            category=category,
            neighborhood=neighborhood,
        ),
    )
