from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel


class DatasetStatusResponse(BaseModel):
    city: str
    source_name: str
    publisher: Optional[str] = None
    source_url: Optional[str] = None
    attribution: str
    is_population_complete: bool
    latest_successful_ingestion_at: Optional[datetime] = None
    earliest_occurred_date: Optional[date] = None
    latest_occurred_date: Optional[date] = None
    incident_count: int
    offense_count: int
