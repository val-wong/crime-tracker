"""Generic source-record reconciliation state machine.

Implements the six outcomes documented in
docs/sources/denver-ingestion-design.md §4-§5 and
docs/ingestion-framework.md, against any ``SourceAdapter`` — nothing
here is Denver-specific.

The one rule this module exists to enforce: a partial/failed run must
never be treated as evidence that a source record was deleted. That is
why "missing record" detection only runs when the caller explicitly
passes ``is_complete=True`` — see the ``is_complete`` parameter below,
not ``run.is_complete`` (the run's own flag is only set afterwards, by
``finish_run``, once the caller knows how the fetch actually went).

A second, distinct rule this module enforces (see
``app.services.plausibility``): a run being *complete* is not the same
as a run being *trusted for deletion evidence*. Even a complete run's
missing-record detection only proceeds once its snapshot has also
passed a plausibility check against the source's prior trusted
history — see ``run.deletion_evidence_allowed``.

**Batching.** Records are processed in bounded-size batches, each doing
O(1) round trips regardless of batch size (one SELECT to look up
existing rows, bulk INSERT for new rows, bulk UPDATE for changed/
unchanged rows) rather than one round trip per record. This matters at
real-source scale — see docs/sources/chicago-ingestion-design.md
"Performance": a naive per-record loop measured at roughly 8ms/record
in this project's own environment, which would make an 8.6M-row source
impractical. The externally-visible behavior (the six outcomes above,
and the exact ``ReconciliationOutcome`` list returned) is unchanged
from the original one-record-at-a-time implementation — only how the
database is talked to has changed.
"""

import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Literal, Optional

from sqlalchemy import bindparam, select, update
from sqlalchemy.orm import Session

from app.adapters.base import SourceAdapter
from app.config import get_settings
from app.db.mixins import utcnow
from app.fingerprint import fingerprint_record
from app.models.enums import PlausibilityStatus, RemovalConfidence
from app.models.ingestion_run import IngestionRun
from app.models.raw_version import RawSourceRecordVersion
from app.models.source import Source
from app.models.source_record import SourceRecord
from app.services.plausibility import evaluate_plausibility

OutcomeKind = Literal["created", "unchanged", "changed"]

DEFAULT_BATCH_SIZE = 2000

OnBatchCallback = Callable[[list[Mapping[str, Any]], list["ReconciliationOutcome"]], None]


@dataclass(frozen=True)
class ReconciliationOutcome:
    external_record_id: str
    source_record_id: uuid.UUID
    kind: OutcomeKind
    reappeared: bool
    new_version_created: bool


def _chunked(items: Iterable[Any], size: int) -> Iterator[list[Any]]:
    batch: list[Any] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def reconcile_source_records(
    db: Session,
    *,
    source: Source,
    run: IngestionRun,
    adapter: SourceAdapter,
    raw_records: Iterable[Mapping[str, Any]],
    is_complete: bool,
    removal_confirmation_runs: Optional[int] = None,
    schema_valid: bool = True,
    reported_total_count: Optional[int] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    on_batch: Optional[OnBatchCallback] = None,
    collect_outcomes: bool = True,
    checkpoint_every_n_batches: Optional[int] = None,
) -> list[ReconciliationOutcome]:
    """Reconcile a fetch's worth of raw records against stored state.

    Returns one ``ReconciliationOutcome`` per fetched record (in fetch
    order) when ``collect_outcomes=True`` (the default — matches every
    prior version of this function). For very large fetches, pass
    ``collect_outcomes=False`` and use ``on_batch`` to observe results
    batch-by-batch instead, so the full record set never needs to sit
    in memory at once (see ``app.services.ingestion.run_ingestion_cycle``,
    which uses this to stream incident/offense normalization per batch
    rather than materializing the whole fetch first).

    ``removal_confirmation_runs``, if not given, falls back to
    ``source.removal_confirmation_runs`` and then to the global
    ``Settings.removal_confirmation_runs`` default.

    ``schema_valid`` and ``reported_total_count`` are passed straight
    through to the plausibility check (``app.services.plausibility``)
    when ``is_complete``.

    **Checkpointing.** See docs/reconciliation-transactions.md for the
    full design. When ``checkpoint_every_n_batches`` is set, this
    function commits ``db`` after every N batches (each batch's
    SourceRecord/RawSourceRecordVersion writes *and* its
    incident/offense sync via ``on_batch`` — one fully-consistent unit
    — are always committed together, never split across a commit
    boundary). This turns one multi-hour transaction into many short
    ones: a crash mid-run leaves prior checkpoints durably committed
    rather than rolling back hours of progress, and a long-running
    transaction no longer blocks concurrent schema changes or
    autovacuum for the run's entire duration. Missing-record detection
    and plausibility evaluation still only ever run once, after every
    batch has been processed — checkpointing changes *when data is
    committed*, never *when deletion evidence is computed* -- so "a
    partial/failed run must never look like deletion evidence" holds
    exactly as before. When unset (the default), behavior is unchanged
    from before this phase: the caller's own transaction boundary
    applies, as always.
    """
    if removal_confirmation_runs is None:
        removal_confirmation_runs = (
            source.removal_confirmation_runs
            if source.removal_confirmation_runs is not None
            else get_settings().removal_confirmation_runs
        )
    fields = adapter.fingerprint_fields()
    outcomes: list[ReconciliationOutcome] = []
    seen_ids: set[str] = set()

    for batch_index, raw_batch in enumerate(_chunked(raw_records, batch_size)):
        batch_outcomes = _reconcile_batch(
            db, source=source, run=run, adapter=adapter, raw_batch=raw_batch, fields=fields
        )
        seen_ids.update(o.external_record_id for o in batch_outcomes)
        if on_batch is not None:
            on_batch(raw_batch, batch_outcomes)
        if collect_outcomes:
            outcomes.extend(batch_outcomes)

        if checkpoint_every_n_batches and (batch_index + 1) % checkpoint_every_n_batches == 0:
            db.commit()

    records_received = len(seen_ids)
    run.records_received = records_received

    if is_complete:
        result = evaluate_plausibility(
            source,
            records_received=records_received,
            schema_valid=schema_valid,
            reported_total_count=reported_total_count,
        )
        run.plausibility_status = result.status
        run.blocking_reason = result.blocking_reason
        run.deletion_evidence_allowed = result.deletion_evidence_allowed
        run.baseline_count_at_run = result.baseline_count_at_run
        run.percent_change_from_baseline = result.percent_change_from_baseline

        if result.deletion_evidence_allowed:
            # Only a run that is both complete AND plausible may ever
            # accumulate missing-evidence against previously-active
            # records, and only such a run may become the new trusted
            # baseline for future comparisons.
            missing_count = _apply_missing_record_evidence(
                db,
                source_id=source.id,
                seen_ids=seen_ids,
                removal_confirmation_runs=removal_confirmation_runs,
            )
            run.records_missing = missing_count

            source.last_trusted_active_count = records_received
            source.last_trusted_run_id = run.id
            run.promoted_to_baseline_at = utcnow()
        else:
            # Suspicious: preserve the run and its diagnostics (above),
            # but do not touch any source_record's missing-evidence,
            # do not mark anything removed, and do not move the
            # source's trusted baseline. The run is not discarded —
            # it's simply not trusted for this purpose.
            run.records_missing = 0
    else:
        # A partial/failed run intentionally skips plausibility
        # evaluation entirely — it's already blocked by incompleteness
        # alone, a separate, pre-existing gate. Still surface what the
        # current baseline is, for observability, without implying a
        # meaningful comparison was made against it.
        run.plausibility_status = PlausibilityStatus.NOT_EVALUATED
        run.deletion_evidence_allowed = False
        run.baseline_count_at_run = source.last_trusted_active_count
        run.percent_change_from_baseline = None
    # A partial/failed run intentionally skips missing-record detection
    # entirely — no missing-evidence is accumulated from an incomplete
    # fetch.

    db.flush()
    # The batch operations above write via Core insert()/update()
    # against SourceRecord's table directly, bypassing the ORM's
    # identity map for performance. Any SourceRecord/RawSourceRecordVersion
    # object already loaded into this session (e.g. by a caller that
    # queried one before calling this function) would otherwise keep
    # showing its pre-update attribute values. Expiring here forces a
    # fresh read on next access, at the cost of one extra query per
    # object actually re-accessed afterward -- cheap relative to the
    # bulk savings already made, and only paid once per run.
    db.expire_all()
    return outcomes


def _reconcile_batch(
    db: Session,
    *,
    source: Source,
    run: IngestionRun,
    adapter: SourceAdapter,
    raw_batch: list[Mapping[str, Any]],
    fields: Sequence[str],
) -> list[ReconciliationOutcome]:
    """One batch: one SELECT to look up existing rows, then bulk
    INSERT/UPDATE — O(1) round trips regardless of batch size."""
    canonicalized = []
    for raw in raw_batch:
        external_id = adapter.external_record_id(raw)
        canonical = adapter.canonicalize(raw)
        fp = fingerprint_record(canonical.payload, fields)
        canonicalized.append((external_id, canonical, fp))

    # Defensive: a bulk INSERT of two new rows with the same
    # external_record_id would violate the unique constraint and fail
    # the whole batch. Not expected in practice (Chicago's `id` is
    # confirmed unique — see docs/sources/chicago.md §1), but cheap to
    # guard against; keep the last occurrence if a source ever repeats
    # one within a single page.
    deduped: dict[str, tuple[Any, Any]] = {}
    for external_id, canonical, fp in canonicalized:
        deduped[external_id] = (canonical, fp)
    canonicalized = [(eid, canonical, fp) for eid, (canonical, fp) in deduped.items()]

    external_ids = [c[0] for c in canonicalized]
    existing_by_external_id = _get_existing_source_records(
        db, source_id=source.id, external_record_ids=external_ids
    )

    new_record_rows: list[dict[str, Any]] = []
    new_version_rows: list[dict[str, Any]] = []
    update_rows: list[dict[str, Any]] = []
    outcomes: list[ReconciliationOutcome] = []

    for external_id, canonical, fp in canonicalized:
        existing = existing_by_external_id.get(external_id)

        if existing is None:
            new_id = uuid.uuid4()
            new_record_rows.append(
                {
                    "id": new_id,
                    "source_id": source.id,
                    "external_record_id": external_id,
                    "source_active": True,
                    "first_seen_at": canonical.observed_at,
                    "last_seen_at": canonical.observed_at,
                    "source_removed_at": None,
                    "removal_confidence": RemovalConfidence.NONE,
                    "consecutive_missing_runs": 0,
                    "current_fingerprint": fp,
                }
            )
            new_version_rows.append(
                _raw_version_row(
                    source_record_id=new_id,
                    ingestion_run_id=run.id,
                    fingerprint=fp,
                    raw_payload=canonical.payload,
                    observed_at=canonical.observed_at,
                )
            )
            run.records_created += 1
            outcomes.append(
                ReconciliationOutcome(
                    external_id, new_id, "created", reappeared=False, new_version_created=True
                )
            )
            continue

        reappeared = not existing["source_active"]
        changed = fp != existing["current_fingerprint"]

        update_rows.append(
            {
                "_id": existing["id"],
                "last_seen_at": canonical.observed_at,
                "consecutive_missing_runs": 0,
                "removal_confidence": RemovalConfidence.NONE,
                "source_active": True,
                "current_fingerprint": fp if changed else existing["current_fingerprint"],
            }
        )

        if changed:
            new_version_rows.append(
                _raw_version_row(
                    source_record_id=existing["id"],
                    ingestion_run_id=run.id,
                    fingerprint=fp,
                    raw_payload=canonical.payload,
                    observed_at=canonical.observed_at,
                )
            )
            run.records_changed += 1
            outcomes.append(
                ReconciliationOutcome(
                    external_id,
                    existing["id"],
                    "changed",
                    reappeared=reappeared,
                    new_version_created=True,
                )
            )
        else:
            run.records_unchanged += 1
            outcomes.append(
                ReconciliationOutcome(
                    external_id,
                    existing["id"],
                    "unchanged",
                    reappeared=reappeared,
                    new_version_created=False,
                )
            )

    if new_record_rows:
        db.execute(SourceRecord.__table__.insert(), new_record_rows)
    if new_version_rows:
        db.execute(RawSourceRecordVersion.__table__.insert(), new_version_rows)
    if update_rows:
        stmt = (
            update(SourceRecord.__table__)
            .where(SourceRecord.__table__.c.id == bindparam("_id"))
            .values(
                last_seen_at=bindparam("last_seen_at"),
                consecutive_missing_runs=bindparam("consecutive_missing_runs"),
                removal_confidence=bindparam("removal_confidence"),
                source_active=bindparam("source_active"),
                current_fingerprint=bindparam("current_fingerprint"),
            )
        )
        db.execute(stmt, update_rows)

    return outcomes


def _raw_version_row(
    *,
    source_record_id: uuid.UUID,
    ingestion_run_id: uuid.UUID,
    fingerprint: str,
    raw_payload: Mapping[str, Any],
    observed_at: datetime,
) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "source_record_id": source_record_id,
        "ingestion_run_id": ingestion_run_id,
        "fingerprint": fingerprint,
        "raw_payload": dict(raw_payload),
        "observed_at": observed_at,
    }


def _get_existing_source_records(
    db: Session, *, source_id: uuid.UUID, external_record_ids: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """Lightweight dict rows (not full ORM objects) for one batch's
    lookup — avoids instantiating ORM entities we're about to bulk
    UPDATE via Core anyway."""
    if not external_record_ids:
        return {}
    rows = db.execute(
        select(
            SourceRecord.id,
            SourceRecord.external_record_id,
            SourceRecord.source_active,
            SourceRecord.current_fingerprint,
        ).where(
            SourceRecord.source_id == source_id,
            SourceRecord.external_record_id.in_(external_record_ids),
        )
    ).all()
    return {
        row.external_record_id: {
            "id": row.id,
            "source_active": row.source_active,
            "current_fingerprint": row.current_fingerprint,
        }
        for row in rows
    }


def _apply_missing_record_evidence(
    db: Session,
    *,
    source_id: uuid.UUID,
    seen_ids: set[str],
    removal_confirmation_runs: int,
) -> int:
    """Bulk version of "find active records not in this fetch, advance
    their missing-evidence". Fetches lightweight tuples (not full ORM
    rows) for every currently-active record of this source, computes
    the new state per-row in Python, and applies it via one bulk
    UPDATE — avoids instantiating one ORM object per active record,
    which matters once a source has millions of them.
    """
    active_rows = db.execute(
        select(
            SourceRecord.id,
            SourceRecord.external_record_id,
            SourceRecord.consecutive_missing_runs,
        ).where(SourceRecord.source_id == source_id, SourceRecord.source_active.is_(True))
    ).all()

    missing = [row for row in active_rows if row.external_record_id not in seen_ids]
    if not missing:
        return 0

    now = utcnow()
    # Two separate statements, not one: a record that hasn't yet
    # crossed the confirmation threshold must have `source_removed_at`
    # left completely untouched (it may already carry a historical
    # marker from a *previous* removal cycle before it reappeared —
    # see app/models/source_record.py). Only a newly-CONFIRMED removal
    # may write to that column. Folding both cases into one bulk
    # UPDATE would force every row through the same SET clause and
    # incorrectly null out that history for the not-yet-confirmed rows.
    not_yet_confirmed_rows = []
    newly_confirmed_rows = []
    for row in missing:
        new_missing_count = row.consecutive_missing_runs + 1
        if new_missing_count >= removal_confirmation_runs:
            newly_confirmed_rows.append(
                {
                    "_id": row.id,
                    "consecutive_missing_runs": new_missing_count,
                    "removal_confidence": RemovalConfidence.CONFIRMED,
                    "source_active": False,
                    "source_removed_at": now,
                }
            )
        else:
            confidence = (
                RemovalConfidence.LIKELY if new_missing_count >= 2 else RemovalConfidence.PENDING
            )
            not_yet_confirmed_rows.append(
                {
                    "_id": row.id,
                    "consecutive_missing_runs": new_missing_count,
                    "removal_confidence": confidence,
                }
            )

    if not_yet_confirmed_rows:
        stmt = (
            update(SourceRecord.__table__)
            .where(SourceRecord.__table__.c.id == bindparam("_id"))
            .values(
                consecutive_missing_runs=bindparam("consecutive_missing_runs"),
                removal_confidence=bindparam("removal_confidence"),
            )
        )
        db.execute(stmt, not_yet_confirmed_rows)

    if newly_confirmed_rows:
        stmt = (
            update(SourceRecord.__table__)
            .where(SourceRecord.__table__.c.id == bindparam("_id"))
            .values(
                consecutive_missing_runs=bindparam("consecutive_missing_runs"),
                removal_confidence=bindparam("removal_confidence"),
                source_active=bindparam("source_active"),
                source_removed_at=bindparam("source_removed_at"),
            )
        )
        db.execute(stmt, newly_confirmed_rows)

    return len(missing)
