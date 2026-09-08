"""Public dataset-status endpoint.

Lets the frontend tell full data from partial development data (see
docs/product.md "Transparency about limitations") without exposing
anything operational -- no plausibility thresholds, no manual holds, no
run error messages, no internal identifiers. If the Chicago source
hasn't been bootstrapped yet (see docs/operations/chicago-ingestion.md),
this returns a well-formed "no data yet" response rather than an error,
since an empty dev database is a normal, expected state.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.constants import CHICAGO_CITY_NAME, CHICAGO_SOURCE_KEY
from app.db.session import get_db
from app.repositories import sources as sources_repo
from app.schemas.status import DatasetStatusResponse
from app.services.status import CHICAGO_ATTRIBUTION, get_dataset_status

router = APIRouter(prefix="/api/status", tags=["status"])


@router.get("", response_model=DatasetStatusResponse)
def dataset_status(db: Session = Depends(get_db)) -> DatasetStatusResponse:
    source = sources_repo.get_by_key(db, CHICAGO_SOURCE_KEY)
    if source is None:
        return DatasetStatusResponse(
            city=CHICAGO_CITY_NAME,
            source_name="City of Chicago - Crimes - 2001 to Present",
            attribution=CHICAGO_ATTRIBUTION,
            is_population_complete=False,
            incident_count=0,
            offense_count=0,
        )

    status = get_dataset_status(db, source=source, city=CHICAGO_CITY_NAME)
    return DatasetStatusResponse(
        city=status.city,
        source_name=status.source_name,
        publisher=status.publisher,
        source_url=status.source_url,
        attribution=status.attribution,
        is_population_complete=status.is_population_complete,
        latest_successful_ingestion_at=status.latest_successful_ingestion_at,
        earliest_occurred_date=status.earliest_occurred_date,
        latest_occurred_date=status.latest_occurred_date,
        incident_count=status.incident_count,
        offense_count=status.offense_count,
    )
