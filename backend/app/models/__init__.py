"""Import every model so SQLAlchemy's mapper registry and Alembic's
autogeneration/metadata both see the full schema from one place.
"""

from app.db.base import Base
from app.models.enums import (
    IngestionRunStatus,
    LocationPrecision,
    PlausibilityBlockReason,
    PlausibilityStatus,
    RemovalConfidence,
    RollupRefreshStatus,
)
from app.models.incident import Incident
from app.models.ingestion_run import IngestionRun
from app.models.iucr_code import IucrCode
from app.models.offense import Offense
from app.models.raw_version import RawSourceRecordVersion
from app.models.rollup_refresh_run import RollupRefreshRun
from app.models.source import Source
from app.models.source_record import SourceRecord

__all__ = [
    "Base",
    "IngestionRunStatus",
    "LocationPrecision",
    "PlausibilityBlockReason",
    "PlausibilityStatus",
    "RemovalConfidence",
    "RollupRefreshStatus",
    "Incident",
    "IngestionRun",
    "IucrCode",
    "Offense",
    "RawSourceRecordVersion",
    "RollupRefreshRun",
    "Source",
    "SourceRecord",
]
