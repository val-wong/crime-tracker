"""Orchestrates one ingestion cycle: fetch -> reconcile -> normalize.

This is the "front door" a test (or a real ingestion CLI command for
an *enabled* source) calls to run a full cycle against a
``SourceAdapter``. It is deliberately thin — the real logic lives in
``app.services.reconciliation`` (source-record state machine) and the
per-adapter ``normalize_incident_offense`` (incident/offense mapping).

Normalization is best-effort: an adapter that doesn't implement
``normalize_incident_offense`` yet (the normalized taxonomy isn't
built — see docs/product.md) simply doesn't get incidents/offenses
populated, without failing the whole run.

**Streaming.** For a small fetch (tests, fixtures), records are
processed and synced to incidents/offenses batch-by-batch but the full
list of ``ReconciliationOutcome``s is still collected and returned, as
before. For a large real fetch, pass ``collect_outcomes=False`` so
only per-batch aggregate counts are tracked (on ``run``) — this keeps
memory bounded to one batch's worth of raw records rather than the
entire source (see docs/sources/chicago-ingestion-design.md
"Performance").
"""

import logging
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional

from geoalchemy2 import WKTElement
from sqlalchemy.orm import Session

from app.adapters.base import SourceAdapter
from app.db.mixins import utcnow
from app.models.enums import IngestionRunStatus
from app.models.ingestion_run import IngestionRun
from app.models.source import Source
from app.repositories import incidents as incidents_repo
from app.repositories import ingestion_runs as ingestion_runs_repo
from app.services.reconciliation import (
    DEFAULT_BATCH_SIZE,
    ReconciliationOutcome,
    reconcile_source_records,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestionCycleResult:
    run: IngestionRun
    outcomes: list[ReconciliationOutcome]


def run_ingestion_cycle(
    db: Session,
    *,
    source: Source,
    adapter: SourceAdapter,
    is_complete: bool,
    removal_confirmation_runs: Optional[int] = None,
    schema_valid: bool = True,
    reported_total_count: Optional[int] = None,
    error_message: Optional[str] = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    collect_outcomes: bool = True,
    checkpoint_every_n_batches: Optional[int] = None,
) -> IngestionCycleResult:
    """Run one fetch/reconcile/normalize cycle and finalize the run row.

    ``is_complete`` and ``error_message`` describe how the *fetch*
    itself went (decided by the caller — e.g. a test simulating a
    network failure partway through pagination, or a real adapter
    catching a transport error) and are passed straight through to
    reconciliation and to the finalized run record.

    ``schema_valid`` and ``reported_total_count`` feed the
    mass-disappearance plausibility check (see
    ``app.services.plausibility``) and only matter when
    ``is_complete=True``.

    ``checkpoint_every_n_batches``: see
    ``app.services.reconciliation.reconcile_source_records`` and
    docs/reconciliation-transactions.md. When set, this function also
    commits immediately after starting the run (so the RUNNING row
    itself is durable even if the process is killed before any
    checkpoint), and — critically — if reconciliation raises partway
    through, catches it, rolls back only the uncommitted work since the
    last checkpoint, and finalizes the run as FAILED with the
    exception's message before re-raising. Without this, an exception
    partway through a long run would leave the run row stuck showing
    RUNNING forever, since nothing after the raise would ever execute.
    """
    run = ingestion_runs_repo.start_run(db, source_id=source.id)
    if checkpoint_every_n_batches:
        db.commit()

    def _on_batch(
        raw_batch: list[Mapping[str, Any]], batch_outcomes: list[ReconciliationOutcome]
    ) -> None:
        _sync_incidents_and_offenses(
            db, source=source, adapter=adapter, raw_records=raw_batch, outcomes=batch_outcomes
        )

    try:
        outcomes = reconcile_source_records(
            db,
            source=source,
            run=run,
            adapter=adapter,
            raw_records=adapter.fetch_current_records(),
            is_complete=is_complete,
            removal_confirmation_runs=removal_confirmation_runs,
            schema_valid=schema_valid,
            reported_total_count=reported_total_count,
            batch_size=batch_size,
            on_batch=_on_batch,
            collect_outcomes=collect_outcomes,
            checkpoint_every_n_batches=checkpoint_every_n_batches,
        )
    except Exception as exc:
        db.rollback()
        ingestion_runs_repo.finish_run(
            db, run, is_complete=False, status=IngestionRunStatus.FAILED, error_message=str(exc)
        )
        db.commit()
        logger.error("Ingestion cycle failed, run %s marked FAILED: %s", run.id, exc)
        raise

    status = (
        IngestionRunStatus.COMPLETED
        if is_complete
        else (IngestionRunStatus.FAILED if error_message else IngestionRunStatus.PARTIAL)
    )
    ingestion_runs_repo.finish_run(
        db, run, is_complete=is_complete, status=status, error_message=error_message
    )
    return IngestionCycleResult(run=run, outcomes=outcomes)


def _sync_incidents_and_offenses(
    db: Session,
    *,
    source: Source,
    adapter: SourceAdapter,
    raw_records: list[Mapping[str, Any]],
    outcomes: list[ReconciliationOutcome],
) -> None:
    """Bulk incident/offense sync for one batch.

    Only records this batch's reconciliation step actually created or
    changed need a fresh normalization pass; "unchanged" records
    already have a correct, previously-synced offense.
    """
    changed_ids = [o.external_record_id for o in outcomes if o.kind in ("created", "changed")]
    if not changed_ids:
        return

    by_external_id = {adapter.external_record_id(raw): raw for raw in raw_records}
    outcome_by_external_id = {o.external_record_id: o for o in outcomes}

    try:
        drafts = {
            external_id: adapter.normalize_incident_offense(by_external_id[external_id])
            for external_id in changed_ids
        }
    except NotImplementedError:
        # Every record from one adapter follows the same code path, so
        # if the first one isn't implemented, none will be.
        logger.info("Adapter %s has no normalization yet; skipping.", adapter.source_key)
        return

    # --- Incidents: batch-fetch existing, dedupe new ones within this
    # batch (e.g. Chicago's multi-victim homicide rows sharing one
    # case_number in the same page — see docs/sources/chicago.md §2),
    # bulk insert the rest. ---
    incident_external_ids = {draft[0].external_incident_id for draft in drafts.values()}
    existing_incidents = incidents_repo.get_existing_by_external_ids(
        db, source_id=source.id, external_incident_ids=list(incident_external_ids)
    )
    incident_id_by_external_id: dict[str, uuid.UUID] = {
        eid: inc.id for eid, inc in existing_incidents.items()
    }
    new_incident_rows: list[dict[str, Any]] = []
    for external_id in changed_ids:
        incident_draft, _ = drafts[external_id]
        eid = incident_draft.external_incident_id
        if eid in incident_id_by_external_id:
            continue
        new_id = uuid.uuid4()
        incident_id_by_external_id[eid] = new_id
        new_incident_rows.append(
            _incident_row(source_id=source.id, incident_id=new_id, draft=incident_draft)
        )
    if new_incident_rows:
        incidents_repo.bulk_insert_incidents(db, new_incident_rows)

    # --- Offenses: batch-fetch existing by source_record_id, split
    # into inserts vs. updates, bulk-apply both. ---
    source_record_ids = [outcome_by_external_id[eid].source_record_id for eid in changed_ids]
    existing_offenses = incidents_repo.get_offenses_by_source_record_ids(
        db, source_record_ids=source_record_ids
    )

    new_offense_rows: list[dict[str, Any]] = []
    update_offense_rows: list[dict[str, Any]] = []
    for external_id in changed_ids:
        incident_draft, offense_draft = drafts[external_id]
        source_record_id = outcome_by_external_id[external_id].source_record_id
        incident_id = incident_id_by_external_id[incident_draft.external_incident_id]
        existing_offense = existing_offenses.get(source_record_id)
        if existing_offense is not None:
            update_offense_rows.append(_offense_update_row(existing_offense.id, offense_draft))
        else:
            new_offense_rows.append(
                _offense_row(
                    incident_id=incident_id,
                    source_id=source.id,
                    source_record_id=source_record_id,
                    draft=offense_draft,
                )
            )
    if new_offense_rows:
        incidents_repo.bulk_insert_offenses(db, new_offense_rows)
    if update_offense_rows:
        incidents_repo.bulk_update_offenses(db, update_offense_rows)

    db.flush()


def _incident_row(*, source_id: uuid.UUID, incident_id: uuid.UUID, draft) -> dict[str, Any]:
    location = None
    if draft.latitude is not None and draft.longitude is not None:
        location = WKTElement(f"POINT({draft.longitude} {draft.latitude})", srid=4326)
    return {
        "id": incident_id,
        "source_id": source_id,
        "external_incident_id": draft.external_incident_id,
        "occurred_at": draft.occurred_at,
        "occurred_at_raw": draft.occurred_at_raw,
        "reported_at": draft.reported_at,
        "reported_at_raw": draft.reported_at_raw,
        "source_time_convention": draft.source_time_convention,
        "latitude": draft.latitude,
        "longitude": draft.longitude,
        "location": location,
        "location_precision": draft.location_precision,
        "address_text": draft.address_text,
        "neighborhood": draft.neighborhood,
        "district": draft.district,
        "beat": draft.beat,
    }


def _offense_row(
    *, incident_id: uuid.UUID, source_id: uuid.UUID, source_record_id: uuid.UUID, draft
) -> dict[str, Any]:
    return {
        "id": uuid.uuid4(),
        "incident_id": incident_id,
        "source_id": source_id,
        "source_record_id": source_record_id,
        "external_offense_id": draft.external_offense_id,
        "raw_offense_code": draft.raw_offense_code,
        "fbi_code": draft.fbi_code,
        "source_category": draft.source_category,
        "source_subcategory": draft.source_subcategory,
        "normalized_category": draft.normalized_category,
        "normalized_subcategory": draft.normalized_subcategory,
        "victim_count": draft.victim_count,
    }


def _offense_update_row(offense_id: uuid.UUID, draft) -> dict[str, Any]:
    return {
        "id": offense_id,
        "raw_offense_code": draft.raw_offense_code,
        "fbi_code": draft.fbi_code,
        "source_category": draft.source_category,
        "source_subcategory": draft.source_subcategory,
        "victim_count": draft.victim_count,
        "updated_at": utcnow(),
    }
