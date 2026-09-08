"""Incident API response schema.

Reflects the ``incidents``/``offenses`` tables (see
app/models/incident.py, app/models/offense.py). ``offenses`` is a
small nested list of per-offense detail (primary type, description,
IUCR/FBI code) needed by the incident-detail panel (see
docs/product.md V1 scope: "display incidents on a map" implies being
able to show what a clicked incident actually was) — populated via
``list_incidents``'s eager-loaded ``Incident.offenses`` relationship,
so this never triggers an N+1 query per incident.
"""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel

from app.models.enums import LocationPrecision


class OffenseSummary(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    external_offense_id: str
    raw_offense_code: Optional[str] = None
    fbi_code: Optional[str] = None
    # The source's own primary classification (e.g. Chicago's
    # `primary_type`) and its finer description (e.g. Chicago's
    # `description`) -- not the normalized taxonomy, which doesn't
    # exist yet (see docs/product.md).
    source_category: Optional[str] = None
    source_subcategory: Optional[str] = None
    victim_count: Optional[int] = None


class Incident(BaseModel):
    model_config = {"from_attributes": True}

    id: UUID
    source_id: UUID
    external_incident_id: str

    occurred_at: Optional[datetime] = None
    reported_at: Optional[datetime] = None

    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_precision: LocationPrecision = LocationPrecision.UNKNOWN

    address_text: Optional[str] = None
    neighborhood: Optional[str] = None
    district: Optional[str] = None
    beat: Optional[str] = None

    # A light summary for the map/dashboard (e.g. to color-code
    # markers) -- distinct offense source_categories on this incident's
    # offenses. Populated from Incident.categories (a plain Python
    # property on the ORM model, computed from its `offenses`
    # relationship -- see app/models/incident.py) rather than a real
    # database column.
    categories: list[str] = []

    offenses: list[OffenseSummary] = []


class IncidentListResponse(BaseModel):
    items: list[Incident] = []
    # Capped at app.repositories.incidents.COUNT_CAP (10,000) for
    # performance -- an exact count of a large, poorly-selective filter
    # (e.g. a wide bounding box over a dense area) was confirmed to take
    # ~1.8s on its own. A value of exactly 10,000 means "at least
    # 10,000 matched," not necessarily the true total.
    total: int = 0
    limit: int = 100
    offset: int = 0
