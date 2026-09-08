"""Regression test for a real bug found during production-readiness
hardening's browser QA: `count_offenses` previously joined `offenses`
to `incidents` just to filter by source, confirmed via EXPLAIN ANALYZE
to take ~17s at full Chicago scale and dominate `/api/status`'s
latency. Fixed by denormalizing `source_id` directly onto `offenses`
(migration 0009) -- see docs/performance-validation.md.
"""

import json
from pathlib import Path

from sqlalchemy import select

from app.adapters.chicago import ChicagoSourceAdapter
from app.models.offense import Offense
from app.models.source import Source
from app.repositories import incidents as incidents_repo
from app.services.ingestion import run_ingestion_cycle

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "chicago_sample_rows.json"


def test_new_offenses_get_source_id_populated_automatically(db):
    rows = json.loads(FIXTURE_PATH.read_text())
    source = Source(source_key="chicago-offense-source-id-test", name="Offense source_id test")
    db.add(source)
    db.flush()

    adapter = ChicagoSourceAdapter()
    adapter.fetch_current_records = lambda: iter(rows)
    try:
        run_ingestion_cycle(db, source=source, adapter=adapter, is_complete=False)
    finally:
        adapter.close()
    db.commit()

    offenses = db.execute(select(Offense)).scalars().all()
    assert len(offenses) == len(rows)
    assert all(o.source_id == source.id for o in offenses)


def test_count_offenses_matches_actual_row_count_without_a_join(db):
    rows = json.loads(FIXTURE_PATH.read_text())
    source_a = Source(source_key="chicago-offense-count-a", name="Source A")
    source_b = Source(source_key="chicago-offense-count-b", name="Source B")
    db.add_all([source_a, source_b])
    db.flush()

    for source in (source_a, source_b):
        adapter = ChicagoSourceAdapter()
        adapter.fetch_current_records = lambda: iter(rows)
        try:
            run_ingestion_cycle(db, source=source, adapter=adapter, is_complete=False)
        finally:
            adapter.close()
    db.commit()

    # Each source's offense count must reflect only its own offenses,
    # not the other source's -- proving the direct source_id filter
    # (not just "count everything") is correct.
    assert incidents_repo.count_offenses(db, source_id=source_a.id) == len(rows)
    assert incidents_repo.count_offenses(db, source_id=source_b.id) == len(rows)
