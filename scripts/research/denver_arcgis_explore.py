"""Exploratory / research tooling for the Denver ArcGIS crime dataset.

**This is NOT production ingestion code.** It exists to let a human
reproduce the empirical findings in ``docs/sources/denver.md`` and
``docs/sources/denver-ingestion-design.md`` (OFFENSE_ID uniqueness,
incident/offense cardinality, layer capabilities, taxonomy-drift
checks, etc.) without re-deriving the queries from scratch, and to
give future ingestion work a starting point for the *kinds* of queries
the source supports.

Safety notes:

- Every function here issues a small, bounded number of read-only
  HTTPS GET requests against Denver's public ArcGIS REST API. Nothing
  loops unboundedly or polls in a tight loop.
- ``download_full_csv()`` is the one function that pulls the entire
  dataset (via Denver's own bulk-export endpoint, not the paginated
  query endpoint) and is opt-in only — it is never called by
  ``main()`` by default. It polls the export job at a slow interval
  and gives up after a bounded number of attempts.
- No API key, token, or credential is required or used by this
  source, so there is nothing to keep secret here.
- Do not commit the output of ``download_full_csv()`` (or any other
  bulk extract) to git. Write it outside the repository (e.g. this
  project's scratch/temp directory) and delete it once you're done.

Usage:

    python3 scripts/research/denver_arcgis_explore.py

Requires only the Python standard library.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any

LAYER_URL = "https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/arcgis/rest/services/ODC_CRIME_OFFENSES_P/FeatureServer/324"
BULK_EXPORT_URL = "https://opendata-geospatialdenver.hub.arcgis.com/api/download/v1/items/16d9c82bb36c4475bf87189cfaed653c/csv?layers=324"


def _query(params: dict[str, Any]) -> dict[str, Any]:
    """Issue one read-only query against the layer's REST endpoint."""
    url = f"{LAYER_URL}/query?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.load(resp)


def layer_metadata() -> dict[str, Any]:
    """Fetch the layer's own schema/capabilities metadata (one request)."""
    url = f"{LAYER_URL}?f=json"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.load(resp)


def offense_id_uniqueness() -> dict[str, Any]:
    """Check OFFENSE_ID uniqueness across the entire published dataset.

    Uses server-side distinct-value counting (the layer advertises
    ``supportsCountDistinct``/``supportsDistinct``), so this is a
    full census, not a sample — but it is still only an empirical
    result, not a publisher-documented guarantee. Four requests.
    """
    total = _query({"where": "1=1", "returnCountOnly": "true", "f": "json"})["count"]
    distinct = _query(
        {
            "where": "1=1",
            "outFields": "OFFENSE_ID",
            "returnDistinctValues": "true",
            "returnCountOnly": "true",
            "f": "json",
        }
    )["count"]
    nulls = _query({"where": "OFFENSE_ID IS NULL", "returnCountOnly": "true", "f": "json"})["count"]
    blanks = _query({"where": "OFFENSE_ID = ''", "returnCountOnly": "true", "f": "json"})["count"]
    return {
        "total_rows": total,
        "distinct_offense_id": distinct,
        "duplicate_offense_id_rows": total - distinct,
        "null_offense_id": nulls,
        "blank_offense_id": blanks,
        "empirically_unique": (total == distinct and nulls == 0 and blanks == 0),
    }


def incident_offense_cardinality(max_offenses_to_check: int = 12) -> dict[str, int]:
    """Histogram of "offenses per incident" via grouped HAVING-clause counts.

    One request per cardinality level checked (bounded by
    ``max_offenses_to_check``), plus one for "more than that many" —
    cheap because each request asks the server for a single count of
    matching *groups*, not the underlying rows.
    """
    stats = json.dumps(
        [{"statisticType": "count", "onStatisticField": "OBJECTID", "outStatisticFieldName": "cnt"}]
    )
    histogram: dict[str, int] = {}
    for n in range(1, max_offenses_to_check + 1):
        result = _query(
            {
                "where": "1=1",
                "outStatistics": stats,
                "groupByFieldsForStatistics": "INCIDENT_ID",
                "having": f"count(OBJECTID)={n}",
                "returnCountOnly": "true",
                "f": "json",
            }
        )
        histogram[str(n)] = result["count"]
    overflow = _query(
        {
            "where": "1=1",
            "outStatistics": stats,
            "groupByFieldsForStatistics": "INCIDENT_ID",
            "having": f"count(OBJECTID)>{max_offenses_to_check}",
            "returnCountOnly": "true",
            "f": "json",
        }
    )
    histogram[f">{max_offenses_to_check}"] = overflow["count"]
    return histogram


def current_object_ids() -> list[int]:
    """Fetch every currently-live OBJECTID in one request.

    ``returnIdsOnly`` is not subject to the layer's normal
    ``maxRecordCount`` page cap — confirmed empirically to return the
    full id list in a single response for this dataset's current size
    (~378K rows). This is the cheap building block for detecting
    additions/deletions between two points in time: diff this list
    against a previously stored one. It does NOT tell you what
    *changed* about a record that still exists in both lists — see
    ``docs/sources/denver-ingestion-design.md`` for why a periodic
    full-attribute reconciliation pass is still needed.
    """
    result = _query({"where": "1=1", "returnIdsOnly": "true", "f": "json"})
    return result.get("objectIds", [])


def download_full_csv(out_path: str, max_poll_attempts: int = 24, poll_interval_seconds: int = 5) -> str:
    """Download the full dataset via Denver's bulk CSV export endpoint.

    This is the *only* function in this module that pulls the whole
    dataset, and it is opt-in (never called by ``main()``). The Hub
    export API is an async job: the first request kicks off
    generation and returns a "Pending" status; the same URL is polled
    until the file is ready, at which point it starts returning CSV
    bytes instead of JSON.

    Do not commit the resulting file to git. Write ``out_path``
    somewhere outside the repository and delete it once you're done
    analyzing it.
    """
    for attempt in range(max_poll_attempts):
        with urllib.request.urlopen(BULK_EXPORT_URL, timeout=60) as resp:
            body = resp.read()
        try:
            status = json.loads(body)
            if status.get("status") in ("Pending", "Processing"):
                time.sleep(poll_interval_seconds)
                continue
        except json.JSONDecodeError:
            # Not JSON => the actual CSV file has arrived.
            with open(out_path, "wb") as f:
                f.write(body)
            return out_path
    raise TimeoutError(f"Export not ready after {max_poll_attempts} attempts")


def main() -> None:
    print("=== Layer capabilities (selected keys) ===")
    meta = layer_metadata()
    for key in (
        "dateFieldsTimeReference",
        "capabilities",
        "supportsChangeTracking",
        "changeTrackingInfo",
        "globalIdField",
        "objectIdField",
        "maxRecordCount",
        "standardMaxRecordCount",
        "hasStaticData",
        "isDataVersioned",
    ):
        print(f"  {key}: {meta.get(key, '<not present>')}")

    print("\n=== OFFENSE_ID uniqueness ===")
    print(" ", offense_id_uniqueness())

    print("\n=== Incident -> offense cardinality histogram ===")
    print(" ", incident_offense_cardinality())

    print("\n=== Current OBJECTID count (via returnIdsOnly) ===")
    ids = current_object_ids()
    print(f"  {len(ids)} live rows as of this run")


if __name__ == "__main__":
    main()
