"""Explicit operational commands for source ingestion.

Deliberately NOT invoked by application startup (`app.main`) or by any
import side effect — a real ingestion run (especially a full,
unbounded Chicago reconciliation against ~8.6M rows) must always be a
deliberate, explicit action. See docs/operations/chicago-ingestion.md.

Usage (from backend/, with the venv active and DATABASE_URL set):

    python -m app.cli bootstrap-chicago
    python -m app.cli refresh-iucr
    python -m app.cli smoke-ingest-chicago --limit 500
    python -m app.cli incremental-ingest-chicago
    python -m app.cli full-reconcile-chicago --confirm
    python -m app.cli full-reconcile-chicago --limit 100000   # bounded, for benchmarking
    python -m app.cli refresh-rollup                          # see docs/rollup-design.md
"""

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from itertools import islice

from app.adapters.chicago import ChicagoFetchError, ChicagoSourceAdapter
from app.config import get_settings
from app.constants import CHICAGO_SOURCE_KEY
from app.db.session import SessionLocal
from app.models.enums import IngestionRunStatus, PlausibilityStatus
from app.repositories import ingestion_runs as ingestion_runs_repo
from app.repositories import sources as sources_repo
from app.services.ingestion import run_ingestion_cycle
from app.services.iucr import refresh_iucr_codes
from app.services.rollup import refresh_rollup

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("app.cli")

# Every 5 batches (2,000 records each -- see
# app.services.reconciliation.DEFAULT_BATCH_SIZE) = a checkpoint every
# ~10,000 records. See docs/reconciliation-transactions.md for why this
# specific size was chosen.
DEFAULT_CHECKPOINT_BATCHES = 5


def _maybe_refresh_rollup(db) -> None:
    """Best-effort rollup refresh after a successful ingestion run (see
    docs/rollup-design.md "Refresh strategy"). Logged and swallowed on
    failure -- a rollup refresh failure must never be reported as an
    ingestion failure (the ingested data is already safely committed
    either way; the rollup just serves stale-but-valid data until the
    next successful refresh, which is exactly the safety property
    REFRESH ... CONCURRENTLY already gives us)."""
    try:
        result = refresh_rollup(db)
        logger.info(
            "Rollup refresh after ingestion: status=%s row_count=%s duration=%.2fs",
            result.status,
            result.row_count,
            result.duration_seconds,
        )
    except Exception as exc:
        logger.error("Rollup refresh after ingestion failed (ingested data is unaffected): %s", exc)


def _get_chicago_source(db):
    source = sources_repo.get_by_key(db, CHICAGO_SOURCE_KEY)
    if source is None:
        raise SystemExit(
            f"Source '{CHICAGO_SOURCE_KEY}' does not exist yet. "
            "Run `python -m app.cli bootstrap-chicago` first."
        )
    return source


def _make_adapter(args) -> ChicagoSourceAdapter:
    return ChicagoSourceAdapter(app_token=get_settings().chicago_app_token or None)


def _record_failed_schema_run(db, source, validation) -> None:
    """Never proceed to fetch/normalize on a schema we can't trust —
    see docs/sources/chicago-ingestion-design.md "Schema stability".
    Still leaves a real, inspectable IngestionRun row rather than just
    exiting silently.
    """
    run = ingestion_runs_repo.start_run(db, source_id=source.id)
    missing = list(validation.missing_required_fields)
    mismatches = list(validation.type_mismatches)
    error_message = (
        f"Schema validation failed. missing_required_fields={missing} type_mismatches={mismatches}"
    )
    ingestion_runs_repo.finish_run(
        db, run, is_complete=False, status=IngestionRunStatus.FAILED, error_message=error_message
    )
    db.commit()
    logger.error("Schema validation failed, no records fetched: %s", error_message)


def cmd_bootstrap_chicago(args) -> None:
    """Create/update the Chicago Source row with verified metadata.

    Idempotent — safe to run again to update configuration; never
    creates a duplicate row (keyed by `source_key`).
    """
    db = SessionLocal()
    try:
        source = sources_repo.get_by_key(db, CHICAGO_SOURCE_KEY)
        if source is None:
            source = sources_repo.create(
                db,
                source_key=CHICAGO_SOURCE_KEY,
                name="City of Chicago - Crimes - 2001 to Present",
                publisher="Chicago Police Department",
                source_url="https://data.cityofchicago.org/Public-Safety/Crimes-2001-to-Present/ijzp-q8t2",
                timezone_convention="inferred-america-chicago-floating-no-platform-timezone",
                enabled=True,
            )
            logger.info("Created source %s", source.source_key)
        else:
            logger.info("Source %s already exists; updating configuration", source.source_key)

        # See docs/sources/chicago-ingestion-design.md §5 for the
        # evidence behind these specific numbers.
        source.min_expected_records = args.min_expected_records
        source.max_record_count_drop_percent = args.max_drop_percent
        source.mass_disappearance_protection_enabled = True
        source.removal_confirmation_runs = args.removal_confirmation_runs
        db.commit()
        logger.info(
            "Chicago source configured: min_expected_records=%s max_record_count_drop_percent=%s "
            "removal_confirmation_runs=%s",
            source.min_expected_records,
            source.max_record_count_drop_percent,
            source.removal_confirmation_runs,
        )
    finally:
        db.close()


def cmd_refresh_rollup(args) -> None:
    """Explicit, manual rollup refresh -- see docs/rollup-design.md.
    Never invoked by application startup; safe to run repeatedly."""
    db = SessionLocal()
    try:
        result = refresh_rollup(db)
        logger.info(
            "Rollup refresh complete: status=%s row_count=%s duration=%.2fs",
            result.status,
            result.row_count,
            result.duration_seconds,
        )
    except Exception as exc:
        logger.error("Rollup refresh failed: %s", exc)
        raise SystemExit(1)
    finally:
        db.close()


def cmd_refresh_iucr(args) -> None:
    db = SessionLocal()
    try:
        with _make_adapter(args) as adapter:
            count = refresh_iucr_codes(db, adapter)
        db.commit()
        logger.info("Refreshed %d IUCR reference codes", count)
    finally:
        db.close()


def cmd_smoke_ingest_chicago(args) -> None:
    """A deliberately small, bounded fetch -- never treated as a
    complete census (is_complete is always False here), so it can
    never trigger deletion inference or move the trusted baseline.
    Safe to run repeatedly.
    """
    db = SessionLocal()
    try:
        source = _get_chicago_source(db)
        with _make_adapter(args) as adapter:
            validation = adapter.validate_schema()
            if not validation.is_valid:
                _record_failed_schema_run(db, source, validation)
                raise SystemExit(1)

            bounded_records = islice(adapter.fetch_current_records(), args.limit)
            start = time.monotonic()
            result = run_ingestion_cycle(
                db,
                source=source,
                adapter=_BoundedAdapter(adapter, bounded_records),
                is_complete=False,
                schema_valid=True,
            )
        db.commit()
        elapsed = time.monotonic() - start
        logger.info(
            "Smoke ingest complete in %.2fs: received=%d created=%d changed=%d unchanged=%d",
            elapsed,
            result.run.records_received,
            result.run.records_created,
            result.run.records_changed,
            result.run.records_unchanged,
        )
    finally:
        db.close()


def cmd_incremental_ingest_chicago(args) -> None:
    db = SessionLocal()
    try:
        source = _get_chicago_source(db)
        if source.incremental_watermark is None:
            raise SystemExit(
                "No incremental watermark set yet. Run "
                "`python -m app.cli full-reconcile-chicago --confirm` at least once first."
            )
        with _make_adapter(args) as adapter:
            validation = adapter.validate_schema()
            if not validation.is_valid:
                _record_failed_schema_run(db, source, validation)
                raise SystemExit(1)

            fetch_started_at = datetime.now(timezone.utc)
            try:
                records = adapter.fetch_incremental_records(
                    since=source.incremental_watermark, overlap=timedelta(hours=1)
                )
                result = run_ingestion_cycle(
                    db,
                    source=source,
                    adapter=_BoundedAdapter(adapter, records),
                    is_complete=False,  # incremental never sees the whole dataset
                    schema_valid=True,
                    checkpoint_every_n_batches=args.checkpoint_batches,
                )
                fetch_succeeded = True
            except ChicagoFetchError as exc:
                logger.error("Incremental fetch failed: %s", exc)
                fetch_succeeded = False
            except Exception as exc:
                # run_ingestion_cycle already finalized the run itself
                # as FAILED (see docs/reconciliation-transactions.md) --
                # this just stops the watermark from advancing and
                # exits non-zero.
                logger.error("Incremental ingest failed: %s", exc)
                fetch_succeeded = False

        if fetch_succeeded:
            # Only advance the watermark once the fetch is known to
            # have completed without error -- a failed fetch must
            # never let a future incremental run skip records.
            source.incremental_watermark = fetch_started_at
            db.commit()
            logger.info(
                "Incremental ingest complete: received=%d created=%d changed=%d unchanged=%d; "
                "watermark advanced to %s",
                result.run.records_received,
                result.run.records_created,
                result.run.records_changed,
                result.run.records_unchanged,
                fetch_started_at.isoformat(),
            )
            _maybe_refresh_rollup(db)
        else:
            db.rollback()
            raise SystemExit(1)
    finally:
        db.close()


def cmd_full_reconcile_chicago(args) -> None:
    if args.limit is None and not args.confirm:
        raise SystemExit(
            "Refusing to run an unbounded full reconciliation without --confirm "
            "(this would fetch Chicago's entire dataset -- see "
            "docs/operations/chicago-ingestion.md before running this for real)."
        )
    db = SessionLocal()
    try:
        source = _get_chicago_source(db)
        with _make_adapter(args) as adapter:
            validation = adapter.validate_schema()
            if not validation.is_valid:
                # Never proceed to fetch/normalize on a schema we can't
                # trust -- see docs/sources/chicago-ingestion-design.md
                # "Schema stability". Record the failure and stop.
                _record_failed_schema_run(db, source, validation)
                raise SystemExit(1)

            is_complete = args.limit is None  # a bounded run is never "complete"
            reported_total_count = None
            if is_complete:
                reported_total_count = adapter.fetch_reported_total_count()

            fetch_started_at = datetime.now(timezone.utc)
            records = adapter.fetch_current_records()
            if args.limit is not None:
                records = islice(records, args.limit)

            start = time.monotonic()
            try:
                result = run_ingestion_cycle(
                    db,
                    source=source,
                    adapter=_BoundedAdapter(adapter, records),
                    is_complete=is_complete,
                    schema_valid=True,  # confirmed above; invalid schema exits before this point
                    reported_total_count=reported_total_count,
                    collect_outcomes=False,  # unbounded runs: don't hold every outcome in memory
                    checkpoint_every_n_batches=args.checkpoint_batches,
                )
            except Exception as exc:
                # run_ingestion_cycle already rolled back the
                # not-yet-checkpointed work and finalized the run as
                # FAILED -- see docs/reconciliation-transactions.md.
                # Everything checkpointed before the failure is still
                # safely committed; the watermark is not advanced.
                logger.error("Full reconciliation failed: %s", exc)
                raise SystemExit(1)
        elapsed = time.monotonic() - start

        if result.run.deletion_evidence_allowed:
            source.incremental_watermark = fetch_started_at

        db.commit()
        logger.info(
            "Full reconciliation complete in %.2fs: received=%d created=%d changed=%d "
            "unchanged=%d missing=%d plausibility=%s deletion_evidence_allowed=%s",
            elapsed,
            result.run.records_received,
            result.run.records_created,
            result.run.records_changed,
            result.run.records_unchanged,
            result.run.records_missing,
            result.run.plausibility_status,
            result.run.deletion_evidence_allowed,
        )
        if result.run.plausibility_status == PlausibilityStatus.SUSPICIOUS:
            logger.warning(
                "Run flagged SUSPICIOUS (%s) -- deletion evidence withheld. "
                "See docs/ingestion-framework.md 'Manual override' if this drop is legitimate.",
                result.run.blocking_reason,
            )
        if is_complete:
            _maybe_refresh_rollup(db)
    finally:
        db.close()


class _BoundedAdapter:
    """Wraps a real adapter so `fetch_current_records()` yields a
    pre-computed/bounded iterable instead of re-fetching, while
    delegating every other method (canonicalize, normalize, etc.) to
    the wrapped adapter unchanged.
    """

    def __init__(self, inner, records):
        self._inner = inner
        self._records = records

    def fetch_current_records(self):
        return self._records

    def __getattr__(self, name):
        return getattr(self._inner, name)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("bootstrap-chicago", help="Create/update the Chicago Source configuration")
    p.add_argument("--min-expected-records", type=int, default=None)
    p.add_argument("--max-drop-percent", type=float, default=None)
    p.add_argument("--removal-confirmation-runs", type=int, default=None)
    p.set_defaults(func=cmd_bootstrap_chicago)

    p = sub.add_parser("refresh-iucr", help="Refresh the local IUCR reference table")
    p.set_defaults(func=cmd_refresh_iucr)

    p = sub.add_parser("smoke-ingest-chicago", help="Bounded smoke-test ingest")
    p.add_argument("--limit", type=int, default=500)
    p.set_defaults(func=cmd_smoke_ingest_chicago)

    p = sub.add_parser(
        "incremental-ingest-chicago", help="Fetch records changed since the last watermark"
    )
    p.add_argument(
        "--checkpoint-batches",
        type=int,
        default=DEFAULT_CHECKPOINT_BATCHES,
        help="Commit every N batches instead of one giant transaction (0 disables checkpointing)",
    )
    p.set_defaults(func=cmd_incremental_ingest_chicago)

    p = sub.add_parser("full-reconcile-chicago", help="Full dataset fetch and reconciliation")
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Bound the fetch (for benchmarking); never treated as complete",
    )
    p.add_argument(
        "--confirm", action="store_true", help="Required to run an unbounded full reconciliation"
    )
    p.add_argument(
        "--checkpoint-batches",
        type=int,
        default=DEFAULT_CHECKPOINT_BATCHES,
        help="Commit every N batches instead of one giant transaction (0 disables checkpointing)",
    )
    p.set_defaults(func=cmd_full_reconcile_chicago)

    p = sub.add_parser("refresh-rollup", help="Refresh the incident_grid_rollup materialized view")
    p.set_defaults(func=cmd_refresh_rollup)

    return parser


def main(argv=None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main(sys.argv[1:])
