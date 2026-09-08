"""Source adapter interface for future city integrations.

This defines the contract a real adapter (e.g. a future Denver
adapter — not implemented; see docs/sources/denver.md for why
production Denver ingestion is intentionally not built yet) must
satisfy so the reconciliation service, repositories, and tests can all
work against any source the same way.

No production adapter exists in this phase. ``app/adapters/fixture.py``
provides ``FixtureSourceAdapter``, used only by tests/development, to
exercise this interface end-to-end against synthetic data.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from app.models.enums import LocationPrecision


@dataclass(frozen=True)
class CanonicalSourceRecord:
    """One source record, in adapter-agnostic shape.

    ``payload`` holds exactly the fields the adapter considers part of
    the record's identity for fingerprinting/versioning purposes — see
    app/fingerprint.py. It should NOT include volatile fetch metadata.
    """

    external_record_id: str
    payload: Mapping[str, Any]
    observed_at: datetime


@dataclass(frozen=True)
class IncidentDraft:
    """A candidate normalized incident, derived from one canonical record.

    Deliberately loose/optional throughout — no source provides every
    field, and the normalized crime taxonomy doesn't exist yet (see
    docs/product.md).
    """

    external_incident_id: str
    occurred_at: Optional[datetime] = None
    occurred_at_raw: Optional[str] = None
    reported_at: Optional[datetime] = None
    reported_at_raw: Optional[str] = None
    source_time_convention: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_precision: LocationPrecision = LocationPrecision.UNKNOWN
    address_text: Optional[str] = None
    neighborhood: Optional[str] = None
    district: Optional[str] = None
    beat: Optional[str] = None


@dataclass(frozen=True)
class SchemaValidationResult:
    """Result of checking a source's *current* schema against what an
    adapter was built to expect.

    ``missing_required``/``type_mismatches`` being non-empty means the
    adapter cannot safely normalize incoming records — a caller should
    fail the run rather than proceed on a corrupted assumption (see
    docs/ingestion-framework.md and
    docs/sources/chicago-ingestion-design.md §"Schema stability").
    """

    is_valid: bool
    missing_required_fields: Sequence[str] = ()
    type_mismatches: Sequence[str] = ()
    new_unrecognized_fields: Sequence[str] = ()


@dataclass(frozen=True)
class OffenseDraft:
    external_offense_id: str
    raw_offense_code: Optional[str] = None
    # A separate, coarser classification some sources carry alongside
    # their own local code (e.g. Chicago's `fbi_code`) — preserved as
    # its own field rather than folded into `raw_offense_code`, since
    # it is not always a stable 1:1 function of it. Not the same as
    # `normalized_category` — this is still source-provided, not ours.
    fbi_code: Optional[str] = None
    source_category: Optional[str] = None
    source_subcategory: Optional[str] = None
    normalized_category: Optional[str] = None
    normalized_subcategory: Optional[str] = None
    victim_count: Optional[int] = None


class SourceAdapter(ABC):
    """Contract a source-specific adapter must implement.

    Fetching and canonicalizing are separate steps on purpose: fetching
    is the only part that talks to the network, which keeps
    ``canonicalize``/``normalize_incident_offense`` trivially unit
    testable against fixed inputs.
    """

    source_key: str

    @abstractmethod
    def fetch_schema(self) -> Mapping[str, Any]:
        """Return whatever schema/capability metadata the source exposes.

        Used for future schema-drift detection (see
        docs/sources/denver-ingestion-design.md §7) — not enforced by
        this base class, since what "schema" means is source-specific.
        """

    @abstractmethod
    def fetch_current_records(self) -> Iterable[Mapping[str, Any]]:
        """Yield every currently-published raw record from the source."""

    @abstractmethod
    def external_record_id(self, raw_record: Mapping[str, Any]) -> str:
        """Extract the source's own identifier for one raw record."""

    @abstractmethod
    def canonicalize(self, raw_record: Mapping[str, Any]) -> CanonicalSourceRecord:
        """Convert one raw record into the adapter-agnostic canonical shape."""

    def normalize_incident_offense(
        self, raw_record: Mapping[str, Any]
    ) -> tuple[IncidentDraft, OffenseDraft]:
        """Optionally map one raw record into incident/offense drafts.

        Not required by every adapter in this phase — the normalized
        taxonomy doesn't exist yet, so a minimal adapter can leave this
        unimplemented until that work happens.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement normalize_incident_offense"
        )

    def fingerprint_fields(self) -> Sequence[str]:
        """The payload keys that participate in change-detection fingerprints.

        Override to exclude volatile, source-specific fields. Defaults
        to "every key in the payload", which is fine for a fixed-shape
        source but should be tightened per-source in a real adapter.
        """
        return []

    def validate_schema(self) -> SchemaValidationResult:
        """Check the source's *current* schema against what this
        adapter expects, before a run is trusted to normalize records.

        Default: always valid — a minimal adapter isn't required to
        implement this. A real adapter should override it (see
        ``ChicagoSourceAdapter``) to actually call ``fetch_schema()``
        and compare against its own required-field list.
        """
        return SchemaValidationResult(is_valid=True)
