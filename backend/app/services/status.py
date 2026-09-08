"""Public-safe dataset status.

Composes a small, deliberately narrow snapshot for the frontend to
answer one question honestly: is this full data, or partial
development data? See docs/operations/chicago-ingestion.md and
docs/product.md "Transparency about limitations".

Never exposes internal operational detail -- no plausibility
thresholds, no `manual_deletion_hold`, no run error messages, no
database identifiers beyond what's already public (the incident/offense
counts themselves).
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.source import Source
from app.repositories import incidents as incidents_repo
from app.repositories import ingestion_runs as ingestion_runs_repo

# Required verbatim by Chicago's Terms of Use for any derivative
# application -- see docs/sources/chicago.md §10 and
# docs/operations/chicago-ingestion.md "Required attribution". Do not
# reword or shorten.
CHICAGO_ATTRIBUTION = (
    "This site provides applications using data that has been modified "
    "for use from its original source, www.cityofchicago.org, the "
    "official website of the City of Chicago. The City of Chicago makes "
    "no claims as to the content, accuracy, timeliness, or completeness "
    "of any of the data provided at this site. The data provided at "
    "this site is subject to change at any time. It is understood that "
    "the data provided at this site is being used at one's own risk."
)


@dataclass(frozen=True)
class DatasetStatus:
    city: str
    source_name: str
    publisher: Optional[str]
    source_url: Optional[str]
    attribution: str
    is_population_complete: bool
    latest_successful_ingestion_at: Optional[datetime]
    earliest_occurred_date: Optional[date]
    latest_occurred_date: Optional[date]
    incident_count: int
    offense_count: int


def get_dataset_status(db: Session, *, source: Source, city: str) -> DatasetStatus:
    latest_completed = ingestion_runs_repo.get_latest_completed(db, source_id=source.id)
    latest_full = ingestion_runs_repo.get_latest_complete_full_run(db, source_id=source.id)
    earliest_date, latest_date = incidents_repo.get_occurred_at_range(db, source_id=source.id)
    incident_count = incidents_repo.count_incidents(db, source_id=source.id)
    offense_count = incidents_repo.count_offenses(db, source_id=source.id)

    return DatasetStatus(
        city=city,
        source_name=source.name,
        publisher=source.publisher,
        source_url=source.source_url,
        attribution=CHICAGO_ATTRIBUTION,
        is_population_complete=latest_full is not None,
        latest_successful_ingestion_at=(
            latest_completed.completed_at if latest_completed else None
        ),
        earliest_occurred_date=earliest_date,
        latest_occurred_date=latest_date,
        incident_count=incident_count,
        offense_count=offense_count,
    )
