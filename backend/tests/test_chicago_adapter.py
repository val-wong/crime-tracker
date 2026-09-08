"""ChicagoSourceAdapter tests.

Uses a small set of REAL rows captured once from Chicago's live API
into tests/fixtures/chicago_sample_rows.json (including the confirmed
multi-victim homicide case G023235 — see docs/sources/chicago.md §1).
No test in this module makes a live network call by default —
pagination/retry behavior is exercised against an in-process
httpx.MockTransport, and schema-validation tests monkeypatch
``fetch_schema`` directly. See test_chicago_adapter_live.py for the
opt-in, explicitly-invoked live-API smoke test.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from app.adapters.chicago import ChicagoFetchError, ChicagoSourceAdapter, _parse_chicago_timestamp
from app.models.enums import LocationPrecision

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "chicago_sample_rows.json"


@pytest.fixture()
def sample_rows() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text())


@pytest.fixture()
def adapter() -> ChicagoSourceAdapter:
    a = ChicagoSourceAdapter()
    yield a
    a.close()


def _row(sample_rows, row_id: str) -> dict:
    return next(r for r in sample_rows if r["id"] == row_id)


# ---------------------------------------------------------------------------
# Identifier extraction
# ---------------------------------------------------------------------------


def test_external_record_id_uses_id_not_case_number(adapter, sample_rows):
    row = sample_rows[0]
    assert adapter.external_record_id(row) == row["id"]
    assert adapter.external_record_id(row) != row["case_number"]


# ---------------------------------------------------------------------------
# Canonicalization
# ---------------------------------------------------------------------------


def test_canonicalize_preserves_full_raw_payload(adapter, sample_rows):
    row = sample_rows[0]
    canonical = adapter.canonicalize(row)
    assert canonical.external_record_id == row["id"]
    # Full provenance: nothing dropped, including fields not used for
    # fingerprinting (e.g. x_coordinate) or normalization.
    assert canonical.payload["x_coordinate"] == row["x_coordinate"]
    assert canonical.payload["case_number"] == row["case_number"]
    assert canonical.observed_at.tzinfo is not None


def test_fingerprint_fields_excludes_volatile_and_derived_fields(adapter):
    fields = set(adapter.fingerprint_fields())
    assert "updated_on" not in fields  # see docs/sources/chicago.md §3-4
    assert "x_coordinate" not in fields  # redundant with latitude/longitude
    assert "y_coordinate" not in fields
    assert "year" not in fields  # derived from `date`
    assert not any(f.startswith(":@computed_region_") for f in fields)
    assert "id" in fields
    assert "iucr" in fields


# ---------------------------------------------------------------------------
# Homicide / multi-offense-per-incident semantics
# ---------------------------------------------------------------------------


def test_homicide_victim_rows_share_incident_but_not_offense_id(adapter, sample_rows):
    row_a = _row(sample_rows, "650")
    row_b = _row(sample_rows, "651")
    assert row_a["case_number"] == row_b["case_number"] == "G023235"

    incident_a, offense_a = adapter.normalize_incident_offense(row_a)
    incident_b, offense_b = adapter.normalize_incident_offense(row_b)

    # Same incident (same case_number)...
    assert incident_a.external_incident_id == incident_b.external_incident_id == "G023235"
    # ...but distinct offenses (distinct row ids), each its own victim.
    assert offense_a.external_offense_id != offense_b.external_offense_id
    assert offense_a.external_offense_id == "650"
    assert offense_b.external_offense_id == "651"
    assert offense_a.victim_count == 1
    assert offense_b.victim_count == 1


def test_non_homicide_offense_has_no_victim_count(adapter, sample_rows):
    theft_rows = [r for r in sample_rows if r["primary_type"] == "THEFT"]
    assert theft_rows, "fixture should contain at least one THEFT row"
    _, offense = adapter.normalize_incident_offense(theft_rows[0])
    assert offense.victim_count is None


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------


def test_timestamp_parsed_as_america_chicago_then_converted_to_utc(adapter, sample_rows):
    row = _row(sample_rows, "634")
    assert row["date"] == "2001-01-01T10:40:00.000"
    incident, _ = adapter.normalize_incident_offense(row)

    assert incident.occurred_at_raw == "2001-01-01T10:40:00.000"
    # January 1 is Central Standard Time (UTC-6) -- 10:40 CST == 16:40 UTC.
    assert incident.occurred_at == datetime(2001, 1, 1, 16, 40, tzinfo=timezone.utc)
    assert incident.source_time_convention == (
        "inferred-america-chicago-floating-no-platform-timezone"
    )


def test_timestamp_handles_daylight_saving_correctly():
    # July is Central Daylight Time (UTC-5), not Standard Time (UTC-6) --
    # a fixed-offset parser would get this wrong.
    summer = _parse_chicago_timestamp("2001-07-04T12:00:00.000")
    assert summer == datetime(2001, 7, 4, 17, 0, tzinfo=timezone.utc)

    winter = _parse_chicago_timestamp("2001-01-04T12:00:00.000")
    assert winter == datetime(2001, 1, 4, 18, 0, tzinfo=timezone.utc)


def test_missing_or_unparseable_date_returns_none():
    assert _parse_chicago_timestamp(None) is None
    assert _parse_chicago_timestamp("") is None
    assert _parse_chicago_timestamp("not-a-date") is None


def test_no_reported_date_field_is_honestly_absent(adapter, sample_rows):
    """Chicago has no separate reported-date field -- confirmed absent
    in docs/sources/chicago.md §3, not silently invented."""
    incident, _ = adapter.normalize_incident_offense(sample_rows[0])
    assert incident.reported_at is None
    assert incident.reported_at_raw is None


# ---------------------------------------------------------------------------
# Geography / location precision
# ---------------------------------------------------------------------------


def test_location_precision_is_block_when_coordinates_present(adapter, sample_rows):
    incident, _ = adapter.normalize_incident_offense(sample_rows[0])
    assert incident.latitude is not None
    assert incident.longitude is not None
    assert incident.location_precision == LocationPrecision.BLOCK


def test_location_precision_is_unknown_when_coordinates_absent(adapter, sample_rows):
    row = dict(sample_rows[0])
    row.pop("latitude", None)
    row.pop("longitude", None)
    incident, _ = adapter.normalize_incident_offense(row)
    assert incident.location_precision == LocationPrecision.UNKNOWN
    assert incident.latitude is None
    assert incident.longitude is None


def test_socrata_numeric_strings_parsed_to_float(adapter, sample_rows):
    row = sample_rows[0]
    assert isinstance(row["latitude"], str)  # confirmed Socrata quirk
    incident, _ = adapter.normalize_incident_offense(row)
    assert isinstance(incident.latitude, float)
    assert isinstance(incident.longitude, float)


# ---------------------------------------------------------------------------
# Optional fields genuinely missing on some rows
# ---------------------------------------------------------------------------


def test_missing_optional_fields_do_not_break_normalization(adapter, sample_rows):
    row = _row(sample_rows, "650")  # confirmed: no "ward"/"community_area" key at all
    assert "ward" not in row
    incident, offense = adapter.normalize_incident_offense(row)
    assert incident.neighborhood is None  # community_area absent
    assert incident.district == "005"


# ---------------------------------------------------------------------------
# Schema validation (monkeypatched fetch_schema -- no network)
# ---------------------------------------------------------------------------


def _full_schema() -> dict:
    fields = list(ChicagoSourceAdapter.REQUIRED_FIELDS) + list(ChicagoSourceAdapter.OPTIONAL_FIELDS)
    types = dict(ChicagoSourceAdapter._EXPECTED_TYPES)
    return {"fields": fields, "types": types}


def test_validate_schema_passes_when_all_required_fields_present(adapter):
    adapter.fetch_schema = _full_schema
    result = adapter.validate_schema()
    assert result.is_valid is True
    assert result.missing_required_fields == []
    assert result.type_mismatches == []


def test_validate_schema_fails_when_required_field_missing(adapter):
    schema = _full_schema()
    schema["fields"].remove("case_number")
    adapter.fetch_schema = lambda: schema
    result = adapter.validate_schema()
    assert result.is_valid is False
    assert "case_number" in result.missing_required_fields


def test_validate_schema_fails_on_incompatible_type_change(adapter):
    schema = _full_schema()
    schema["types"]["id"] = "text"  # was "number"
    adapter.fetch_schema = lambda: schema
    result = adapter.validate_schema()
    assert result.is_valid is False
    assert any("id" in m for m in result.type_mismatches)


def test_validate_schema_reports_new_field_without_failing(adapter):
    schema = _full_schema()
    schema["fields"].append("brand_new_field")
    adapter.fetch_schema = lambda: schema
    result = adapter.validate_schema()
    assert result.is_valid is True
    assert "brand_new_field" in result.new_unrecognized_fields


def test_validate_schema_fails_safely_when_schema_fetch_itself_fails(adapter):
    def _boom():
        raise RuntimeError("network exploded")

    adapter.fetch_schema = _boom
    result = adapter.validate_schema()
    assert result.is_valid is False


# ---------------------------------------------------------------------------
# Pagination / retries (in-process MockTransport -- no real network)
# ---------------------------------------------------------------------------


def _paginated_transport(pages: list[list[dict]]):
    """A fake Socrata server serving `pages` in order, one per request."""
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        idx = calls["count"]
        calls["count"] += 1
        body = pages[idx] if idx < len(pages) else []
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler), calls


def test_fetch_current_records_paginates_via_keyset_on_id():
    page1 = [{"id": str(i), "case_number": f"C{i}"} for i in range(1, 4)]
    page2 = [{"id": str(i), "case_number": f"C{i}"} for i in range(4, 6)]
    transport, calls = _paginated_transport([page1, page2, []])
    client = httpx.Client(transport=transport)
    adapter = ChicagoSourceAdapter(client=client, page_size=3)

    rows = list(adapter.fetch_current_records())

    assert [r["id"] for r in rows] == ["1", "2", "3", "4", "5"]
    # Stops as soon as a short page confirms end-of-data -- does not
    # need a trailing empty-page request in this case (page2 was
    # already shorter than page_size).
    assert calls["count"] == 2


def test_fetch_current_records_uses_ascending_id_cursor_in_where_clause():
    seen_wheres = []

    def handler(request: httpx.Request) -> httpx.Response:
        where = request.url.params.get("$where")
        seen_wheres.append(where)
        # page_size=1 below, so returning exactly 1 row means "page is
        # full" -- the adapter must fetch again to confirm end-of-data.
        if where is None:
            return httpx.Response(200, json=[{"id": "10", "case_number": "C10"}])
        return httpx.Response(200, json=[])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = ChicagoSourceAdapter(client=client, page_size=1)
    list(adapter.fetch_current_records())

    assert seen_wheres[0] is None
    assert seen_wheres[1] == "id > 10"


def test_retries_on_transient_server_error_then_succeeds():
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json=[{"id": "1", "case_number": "C1"}])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = ChicagoSourceAdapter(client=client, max_retries=3)

    rows = list(adapter.fetch_current_records())
    assert len(rows) >= 1
    assert attempts["count"] == 3


def test_raises_chicago_fetch_error_after_exhausting_retries():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = ChicagoSourceAdapter(client=client, max_retries=2)

    with pytest.raises(ChicagoFetchError):
        list(adapter.fetch_current_records())


def test_app_token_sent_as_header_when_provided():
    seen_headers = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers["X-App-Token"] = request.headers.get("X-App-Token")
        return httpx.Response(200, json=[])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = ChicagoSourceAdapter(client=client, app_token="secret-token-value")
    list(adapter.fetch_current_records())

    assert seen_headers["X-App-Token"] == "secret-token-value"


def test_no_app_token_header_when_not_provided():
    seen_headers = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers["has_token"] = "X-App-Token" in request.headers
        return httpx.Response(200, json=[])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = ChicagoSourceAdapter(client=client)
    list(adapter.fetch_current_records())

    assert seen_headers["has_token"] is False


def test_incremental_fetch_uses_compound_updated_on_and_id_cursor():
    seen_wheres = []

    def handler(request: httpx.Request) -> httpx.Response:
        where = request.url.params.get("$where")
        seen_wheres.append(where)
        if len(seen_wheres) == 1:
            return httpx.Response(200, json=[{"id": "5", "updated_on": "2026-01-01T00:00:00.000"}])
        return httpx.Response(200, json=[])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    adapter = ChicagoSourceAdapter(client=client, page_size=1)
    since = datetime(2026, 1, 2, tzinfo=timezone.utc)
    list(adapter.fetch_incremental_records(since=since, overlap=timedelta(hours=1)))

    assert "updated_on >" in seen_wheres[0]
    # Second request's cursor should reflect the last row seen, not the
    # original watermark.
    assert "2026-01-01T00:00:00.000" in seen_wheres[1]
    assert "id > 5" in seen_wheres[1]
