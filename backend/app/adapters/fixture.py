"""A synthetic, in-memory source adapter used only by tests/development.

This is NOT a real city. It exists solely to exercise the
``SourceAdapter`` interface, reconciliation, and persistence layers
end-to-end without touching any real, licensing-unresolved data
source (see docs/sources/denver.md — Denver production ingestion is
intentionally not built yet).
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Optional

from app.adapters.base import CanonicalSourceRecord, IncidentDraft, OffenseDraft, SourceAdapter
from app.models.enums import LocationPrecision

# Fields considered part of a fixture record's identity for
# fingerprinting. Deliberately excludes nothing here since fixture
# records are already hand-shaped for tests — a real adapter should
# curate this list more carefully (see SourceAdapter.fingerprint_fields).
FIXTURE_FINGERPRINT_FIELDS = [
    "external_record_id",
    "external_incident_id",
    "category",
    "subcategory",
    "offense_code",
    "occurred_at",
    "reported_at",
    "address_text",
    "latitude",
    "longitude",
    "neighborhood",
    "district",
    "victim_count",
]


class FixtureSourceAdapter(SourceAdapter):
    """Serves a fixed, in-memory list of synthetic raw records.

    ``records`` is a list of plain dicts shaped like:

        {
            "external_record_id": "FX-1-100",
            "external_incident_id": "FX-1",
            "category": "theft",
            "subcategory": "theft-bicycle",
            "offense_code": "2399",
            "occurred_at": "2026-01-01T12:00:00+00:00",
            "reported_at": "2026-01-01T13:00:00+00:00",
            "address_text": "100 BLK MAIN ST",
            "latitude": 39.7,
            "longitude": -104.9,
            "location_precision": "block",
            "neighborhood": "downtown",
            "district": "1",
            "victim_count": 1,
        }

    Tests construct one adapter instance per "fetch" they want to
    simulate, so changing ``records`` between reconciliation runs is
    how a test models a source that adds/changes/removes records over
    time.
    """

    source_key = "fixture-test-source"

    def __init__(self, records: Sequence[Mapping[str, Any]]):
        self._records = list(records)

    def fetch_schema(self) -> Mapping[str, Any]:
        return {"fields": FIXTURE_FINGERPRINT_FIELDS}

    def fetch_current_records(self) -> Iterable[Mapping[str, Any]]:
        return list(self._records)

    def external_record_id(self, raw_record: Mapping[str, Any]) -> str:
        return str(raw_record["external_record_id"])

    def fingerprint_fields(self) -> Sequence[str]:
        return FIXTURE_FINGERPRINT_FIELDS

    def canonicalize(self, raw_record: Mapping[str, Any]) -> CanonicalSourceRecord:
        return CanonicalSourceRecord(
            external_record_id=self.external_record_id(raw_record),
            payload=dict(raw_record),
            observed_at=datetime.now(timezone.utc),
        )

    def normalize_incident_offense(
        self, raw_record: Mapping[str, Any]
    ) -> tuple[IncidentDraft, OffenseDraft]:
        occurred_at = _parse_dt(raw_record.get("occurred_at"))
        reported_at = _parse_dt(raw_record.get("reported_at"))
        precision = LocationPrecision(raw_record.get("location_precision", "unknown"))

        incident = IncidentDraft(
            external_incident_id=str(raw_record["external_incident_id"]),
            occurred_at=occurred_at,
            occurred_at_raw=raw_record.get("occurred_at"),
            reported_at=reported_at,
            reported_at_raw=raw_record.get("reported_at"),
            source_time_convention="fixture-utc",
            latitude=raw_record.get("latitude"),
            longitude=raw_record.get("longitude"),
            location_precision=precision,
            address_text=raw_record.get("address_text"),
            neighborhood=raw_record.get("neighborhood"),
            district=raw_record.get("district"),
        )
        offense = OffenseDraft(
            external_offense_id=self.external_record_id(raw_record),
            raw_offense_code=raw_record.get("offense_code"),
            source_category=raw_record.get("category"),
            source_subcategory=raw_record.get("subcategory"),
            victim_count=raw_record.get("victim_count"),
        )
        return incident, offense


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
