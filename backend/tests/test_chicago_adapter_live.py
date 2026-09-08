"""Opt-in live-API test against Chicago's real Socrata endpoint.

Skipped by default -- the normal test suite (and CI, if any is ever
added) must never depend on Chicago's live API being reachable or
behaving a particular way. Run explicitly with:

    RUN_LIVE_CHICAGO_TESTS=1 pytest tests/test_chicago_adapter_live.py -v
"""

import os

import pytest

from app.adapters.chicago import ChicagoSourceAdapter

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE_CHICAGO_TESTS") != "1",
    reason="opt-in only; set RUN_LIVE_CHICAGO_TESTS=1 to run against Chicago's live API",
)


def test_live_schema_validates():
    with ChicagoSourceAdapter() as adapter:
        result = adapter.validate_schema()
    assert result.is_valid, result


def test_live_fetch_small_page():
    with ChicagoSourceAdapter(page_size=5) as adapter:
        rows = []
        for row in adapter.fetch_current_records():
            rows.append(row)
            if len(rows) >= 5:
                break
    assert len(rows) == 5
    for row in rows:
        assert "id" in row
        assert "case_number" in row
