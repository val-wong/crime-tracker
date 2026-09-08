"""Production adapter for Chicago's official "Crimes - 2001 to Present"
dataset (Socrata, dataset id `ijzp-q8t2`).

Every design choice here is traced to a specific finding in
docs/sources/chicago.md and docs/sources/chicago-ingestion-design.md —
see the docstring on each method for the relevant section. This module
talks to the network (via httpx) and to nothing else; it has no
knowledge of reconciliation, persistence, or plausibility — those stay
in app.services.* and are shared across every adapter (see
app/adapters/base.py).

Nothing in this module is invoked by application startup. A real
ingestion run is always an explicit, deliberate action — see
app/cli.py.
"""

import json
import logging
import time
from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx

from app.adapters.base import (
    CanonicalSourceRecord,
    IncidentDraft,
    OffenseDraft,
    SchemaValidationResult,
    SourceAdapter,
)
from app.models.enums import LocationPrecision

logger = logging.getLogger(__name__)

CHICAGO_TZ = ZoneInfo("America/Chicago")

# See docs/sources/chicago.md §3: Socrata's own developer documentation
# declares `calendar_date`/`floating_timestamp` fields carry NO
# timezone at all. This string records that basis honestly rather than
# claiming a verified UTC instant — see IncidentDraft.source_time_convention.
SOURCE_TIME_CONVENTION = "inferred-america-chicago-floating-no-platform-timezone"


class ChicagoFetchError(Exception):
    """A Chicago/Socrata fetch failed after exhausting retries.

    Callers (see app/cli.py) should catch this, mark the ingestion run
    as failed/partial, and never infer deletions from it — the same
    posture as any other partial-run safety case in
    docs/ingestion-framework.md.
    """


class ChicagoSourceAdapter(SourceAdapter):
    source_key = "chicago-pd-open-data"

    BASE_URL = "https://data.cityofchicago.org"
    DATASET_ID = "ijzp-q8t2"
    IUCR_DATASET_ID = "c7ck-438e"

    # See docs/sources/chicago.md §1-§2, §6: fields the adapter cannot
    # safely normalize without. `id` is the row identifier (Socrata's
    # own declared rowIdentifier — see chicago.md §1); `case_number` is
    # the incident identifier; `date`/`updated_on` drive timestamp and
    # incremental-fetch logic; `iucr`/`primary_type` anchor taxonomy.
    REQUIRED_FIELDS = ("id", "case_number", "date", "updated_on", "iucr", "primary_type")

    # Present but not required for a safe normalization -- missing
    # values are acceptable and simply leave the corresponding
    # normalized field null (see docs/architecture.md: "not every city
    # provides every field").
    OPTIONAL_FIELDS = (
        "block",
        "description",
        "location_description",
        "arrest",
        "domestic",
        "beat",
        "district",
        "ward",
        "community_area",
        "fbi_code",
        "latitude",
        "longitude",
    )

    # Present in the source but not currently consumed by this adapter.
    IGNORED_FIELDS = (
        "x_coordinate",
        "y_coordinate",
        "year",
        "location",
        ":@computed_region_awaf_s7ux",
        ":@computed_region_6mkv_f3dw",
        ":@computed_region_vrxf_vc4k",
        ":@computed_region_bdys_3d7i",
        ":@computed_region_43wa_7qmu",
        ":@computed_region_rpca_8um6",
        ":@computed_region_d9mm_jgwp",
        ":@computed_region_d3ds_rm58",
        ":@computed_region_8hcu_yrd4",
    )

    # Expected SODA types for the fields we actually depend on --
    # checked by validate_schema() so an incompatible type change fails
    # the run rather than silently corrupting downstream parsing. Not
    # exhaustive -- just the fields this adapter's own logic branches
    # on the type of.
    #
    # NOTE: these values match the `X-SODA2-Types` response header
    # vocabulary (what fetch_schema() actually reads), which is NOT the
    # same vocabulary as the separate `/api/views/{id}.json` metadata
    # endpoint's `dataTypeName` (e.g. a boolean column is reported as
    # "boolean" here but "checkbox" there) -- confirmed by an opt-in
    # live-API test (tests/test_chicago_adapter_live.py) catching this
    # exact discrepancy during this phase's own validation.
    _EXPECTED_TYPES = {
        "id": "number",
        "case_number": "text",
        "date": "floating_timestamp",
        "updated_on": "floating_timestamp",
        "iucr": "text",
        "primary_type": "text",
        "latitude": "number",
        "longitude": "number",
        "arrest": "boolean",
    }

    # See docs/sources/chicago-ingestion-design.md §3: excludes
    # `updated_on` deliberately -- a confirmed bulk-touch event (706
    # rows, 2 distinct timestamps, spanning a 25-year-old record)
    # showed `updated_on` can advance with no substantive field
    # actually changing. Also excludes x/y coordinates (redundant with
    # lat/lon), `year` (derived from `date`), and the Socrata-computed
    # region joins (not source-authoritative).
    FINGERPRINT_FIELDS = [
        "id",
        "case_number",
        "date",
        "block",
        "iucr",
        "primary_type",
        "description",
        "location_description",
        "arrest",
        "domestic",
        "beat",
        "district",
        "ward",
        "community_area",
        "fbi_code",
        "latitude",
        "longitude",
    ]

    def __init__(
        self,
        *,
        app_token: Optional[str] = None,
        client: Optional[httpx.Client] = None,
        page_size: int = 5000,
        request_timeout: float = 30.0,
        max_retries: int = 3,
    ):
        self._app_token = app_token
        self._page_size = page_size
        self._timeout = request_timeout
        self._max_retries = max_retries
        self._client = client or httpx.Client()
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "ChicagoSourceAdapter":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # -- Schema -----------------------------------------------------------

    def fetch_schema(self) -> Mapping[str, Any]:
        """Read the current field/type list from Socrata's own response
        headers (`X-SODA2-Fields`/`X-SODA2-Types`) — confirmed present
        on every query response in docs/sources/chicago.md §6/§9, so
        this piggybacks on a minimal (`$limit=1`) real request rather
        than needing a separate metadata endpoint.
        """
        resp = self._get(f"{self.BASE_URL}/resource/{self.DATASET_ID}.json", params={"$limit": 1})
        fields = json.loads(resp.headers.get("X-SODA2-Fields", "[]"))
        types = json.loads(resp.headers.get("X-SODA2-Types", "[]"))
        return {"fields": fields, "types": dict(zip(fields, types))}

    def validate_schema(self) -> SchemaValidationResult:
        """See docs/sources/chicago-ingestion-design.md "Schema
        stability": fail loud (return is_valid=False) rather than
        silently proceed if a required field disappears or a field
        this adapter's logic depends on changes to an incompatible
        type. A brand-new, unrecognized field is reported but does not
        fail validation -- additive changes are safe.
        """
        # Deliberately broad: any fetch failure here means "cannot
        # confirm the schema is safe", which is itself a validation
        # failure, not something to let propagate as an exception.
        try:
            schema = self.fetch_schema()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Chicago schema fetch failed: %s", exc)
            return SchemaValidationResult(
                is_valid=False, missing_required_fields=[f"<schema fetch failed: {exc}>"]
            )

        fields = set(schema.get("fields", []))
        types: dict[str, str] = schema.get("types", {})

        missing = [f for f in self.REQUIRED_FIELDS if f not in fields]
        mismatches = [
            f"{field}: expected {expected!r}, got {types.get(field)!r}"
            for field, expected in self._EXPECTED_TYPES.items()
            if field in types and types[field] != expected
        ]
        known_fields = (
            set(self.REQUIRED_FIELDS) | set(self.OPTIONAL_FIELDS) | set(self.IGNORED_FIELDS)
        )
        new_fields = sorted(fields - known_fields)

        return SchemaValidationResult(
            is_valid=not missing and not mismatches,
            missing_required_fields=missing,
            type_mismatches=mismatches,
            new_unrecognized_fields=new_fields,
        )

    # -- Fetching -----------------------------------------------------------

    def fetch_current_records(self) -> Iterator[Mapping[str, Any]]:
        """Full-dataset fetch, for initial population, periodic
        deletion detection, and source-count validation — see
        docs/sources/chicago-ingestion-design.md §"Full reconciliation
        mode". Uses keyset pagination on `id` (confirmed unique across
        a full census — chicago.md §1), not OFFSET, since OFFSET
        performance degrades badly at Chicago's scale (~8.6M rows) —
        see docs/sources/chicago-ingestion-design.md §"Pagination".
        """
        last_id: Optional[int] = None
        while True:
            where = f"id > {last_id}" if last_id is not None else None
            page = self._fetch_page(order="id ASC", where=where)
            if not page:
                return
            yield from page
            last_id = int(page[-1]["id"])
            if len(page) < self._page_size:
                return

    def fetch_incremental_records(
        self, *, since: datetime, overlap: timedelta = timedelta(hours=1)
    ) -> Iterator[Mapping[str, Any]]:
        """Candidate-changed records since ``since`` (a prior watermark),
        widened by ``overlap`` to avoid boundary misses — see
        docs/sources/chicago-ingestion-design.md §"Incremental mode".

        `updated_on` only identifies *candidates*; the caller's
        fingerprint comparison (already the case for every source, per
        app/fingerprint.py) is what actually decides whether a
        candidate changed. This method does not filter on that — it
        just narrows what gets fetched at all.

        Ordered by ``(updated_on, id)`` with a compound cursor, not
        `updated_on` alone: a confirmed bulk-touch event showed many
        rows can share the exact same `updated_on` value (706 rows
        across just 2 timestamps in one observed case — chicago.md
        §3-4), so paginating on `updated_on` alone risks skipping or
        duplicating rows at a page boundary where the cursor value
        ties across pages.
        """
        watermark = since - overlap
        cursor_updated_on = watermark.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
        cursor_id = -1
        while True:
            where = (
                f"updated_on > '{cursor_updated_on}' "
                f"OR (updated_on = '{cursor_updated_on}' AND id > {cursor_id})"
            )
            page = self._fetch_page(order="updated_on ASC, id ASC", where=where)
            if not page:
                return
            yield from page
            last = page[-1]
            cursor_updated_on = last["updated_on"]
            cursor_id = int(last["id"])
            if len(page) < self._page_size:
                return

    def fetch_iucr_codes(self) -> list[dict[str, Any]]:
        """Chicago's official IUCR reference table (~434 rows, small
        enough to fetch in a couple of pages) — see
        docs/sources/chicago.md §7. Returns raw rows; enrichment/
        persistence is handled by app/services/iucr.py, not here.
        """
        rows: list[dict[str, Any]] = []
        offset = 0
        page_size = 1000
        while True:
            resp = self._get(
                f"{self.BASE_URL}/resource/{self.IUCR_DATASET_ID}.json",
                params={"$limit": page_size, "$offset": offset, "$order": "iucr ASC"},
            )
            page = resp.json()
            if not page:
                break
            rows.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
        return rows

    def fetch_reported_total_count(self) -> int:
        """An independently-reported total, for the plausibility
        check's `source_count_mismatch` signal (see
        app/services/plausibility.py) — a cheap `count(*)` query,
        separate from actually paginating through every row.
        """
        resp = self._get(
            f"{self.BASE_URL}/resource/{self.DATASET_ID}.json",
            params={"$select": "count(*) as cnt"},
        )
        body = resp.json()
        return int(body[0]["cnt"])

    def _fetch_page(self, *, order: str, where: Optional[str]) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"$limit": self._page_size, "$order": order}
        if where:
            params["$where"] = where
        resp = self._get(f"{self.BASE_URL}/resource/{self.DATASET_ID}.json", params=params)
        return resp.json()

    def _get(self, url: str, *, params: Mapping[str, Any]) -> httpx.Response:
        headers = {"X-App-Token": self._app_token} if self._app_token else {}
        last_exc: Optional[Exception] = None
        for attempt in range(self._max_retries + 1):
            try:
                resp = self._client.get(url, params=params, headers=headers, timeout=self._timeout)
            except httpx.TransportError as exc:
                last_exc = exc
            else:
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_exc = httpx.HTTPStatusError(
                        f"retryable status {resp.status_code}", request=resp.request, response=resp
                    )
                else:
                    resp.raise_for_status()
                    return resp
            if attempt < self._max_retries:
                backoff = 2**attempt
                logger.warning(
                    "Chicago fetch attempt %d/%d failed (%s); retrying in %ds",
                    attempt + 1,
                    self._max_retries + 1,
                    last_exc,
                    backoff,
                )
                time.sleep(backoff)
        raise ChicagoFetchError(
            f"Failed after {self._max_retries + 1} attempts: {last_exc}"
        ) from last_exc

    # -- Canonicalization / normalization -----------------------------------

    def external_record_id(self, raw_record: Mapping[str, Any]) -> str:
        """`id`, not `case_number` — see docs/sources/chicago.md §1:
        Socrata's own metadata designates `id` as this dataset's row
        identifier, and it is empirically unique across a full census
        (8,630,522 distinct values across 8,630,522 rows), unlike
        `case_number` (629 confirmed duplicates, entirely explained by
        the documented one-row-per-victim homicide policy — see §2).
        """
        return str(raw_record["id"])

    def fingerprint_fields(self) -> Sequence[str]:
        return self.FINGERPRINT_FIELDS

    def canonicalize(self, raw_record: Mapping[str, Any]) -> CanonicalSourceRecord:
        """The payload preserves the *entire* raw record (for full
        provenance in `raw_source_record_versions`) — fingerprinting
        separately projects onto `FINGERPRINT_FIELDS` (see
        app/fingerprint.py); this method does not filter anything out.
        """
        return CanonicalSourceRecord(
            external_record_id=self.external_record_id(raw_record),
            payload=dict(raw_record),
            observed_at=datetime.now(timezone.utc),
        )

    def normalize_incident_offense(
        self, raw_record: Mapping[str, Any]
    ) -> tuple[IncidentDraft, OffenseDraft]:
        """See docs/sources/chicago.md §2 for the incident/offense
        mapping this implements: `external_incident_id = case_number`
        (so multiple homicide-victim rows sharing one case number
        correctly collapse to one Incident — see
        app.services.ingestion._sync_incidents_and_offenses, which
        already dedupes by external_incident_id within a batch), one
        `Offense` per row (`external_offense_id = id`).
        """
        occurred_at_raw = raw_record.get("date")
        occurred_at = _parse_chicago_timestamp(occurred_at_raw)

        latitude = _parse_float(raw_record.get("latitude"))
        longitude = _parse_float(raw_record.get("longitude"))

        incident = IncidentDraft(
            external_incident_id=str(raw_record["case_number"]),
            occurred_at=occurred_at,
            occurred_at_raw=occurred_at_raw,
            # Chicago has no separate reported-date field -- confirmed
            # absent in docs/sources/chicago.md §3, not assumed.
            reported_at=None,
            reported_at_raw=None,
            source_time_convention=SOURCE_TIME_CONVENTION,
            latitude=latitude,
            longitude=longitude,
            # Always BLOCK, unconditionally -- confirmed uniform (100%
            # of rows, not category-dependent) in docs/sources/chicago.md
            # §8: "shifted from the actual location for partial
            # redaction but falls on the same block." Never EXACT.
            location_precision=(
                LocationPrecision.BLOCK
                if (latitude is not None and longitude is not None)
                else LocationPrecision.UNKNOWN
            ),
            address_text=raw_record.get("block"),
            # No named-neighborhood field exists; `community_area` is
            # the closest analog (a numbered code, not a name -- a
            # documented simplification, not a name lookup table,
            # which is out of scope here).
            neighborhood=raw_record.get("community_area"),
            district=raw_record.get("district"),
            beat=raw_record.get("beat"),
        )

        primary_type = raw_record.get("primary_type")
        victim_count = 1 if primary_type == "HOMICIDE" else None

        offense = OffenseDraft(
            external_offense_id=str(raw_record["id"]),
            raw_offense_code=raw_record.get("iucr"),
            fbi_code=raw_record.get("fbi_code"),
            source_category=primary_type,
            source_subcategory=raw_record.get("description"),
            victim_count=victim_count,
        )
        return incident, offense


def _parse_float(value: Any) -> Optional[float]:
    """Socrata serializes numeric fields as JSON strings (e.g.
    `"latitude": "41.710180828"`), a confirmed platform quirk, not a
    Chicago-specific one -- parse defensively either way."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_chicago_timestamp(raw_value: Optional[str]) -> Optional[datetime]:
    """Parse a Socrata floating timestamp (no timezone attached at the
    platform level — confirmed in docs/sources/chicago.md §3) as
    America/Chicago wall-clock time, using a real IANA timezone
    database so historical DST rule changes across the 2001-present
    range are handled correctly, rather than a fixed UTC offset.

    Does NOT claim this is a verified-correct interpretation of what
    CPD originally recorded -- it is the best-supported inference
    available (see chicago.md §3), stored alongside the untouched raw
    string precisely so that inference can be revisited later without
    re-fetching anything.

    A confirmed-real ambiguous case exists: the one hour each November
    when America/Chicago clocks fall back means one wall-clock time
    occurs twice (once in CDT, once in CST) -- 48 rows were found
    directly in this exact window on one sampled date (chicago.md §3).
    `fold=0` (Python's default) resolves this deterministically to the
    first (CDT) occurrence; there is no way to recover which the
    source actually meant from the raw value alone. The one-hour gap
    each March when clocks spring forward (a nonexistent wall-clock
    time) was confirmed empirically to contain zero real records, so
    is not separately handled here.
    """
    if not raw_value:
        return None
    try:
        naive = datetime.strptime(raw_value, "%Y-%m-%dT%H:%M:%S.%f")
    except ValueError:
        try:
            naive = datetime.strptime(raw_value, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            logger.warning("Could not parse Chicago timestamp: %r", raw_value)
            return None
    localized = naive.replace(tzinfo=CHICAGO_TZ)
    return localized.astimezone(timezone.utc)
