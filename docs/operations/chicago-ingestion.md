# Operations: Chicago Ingestion

Concrete, day-to-day commands for running the Chicago adapter against
the real dataset. For the design reasoning behind these choices, see
[`../sources/chicago-ingestion-design.md`](../sources/chicago-ingestion-design.md).
For the source research these decisions are based on, see
[`../sources/chicago.md`](../sources/chicago.md).

All commands below assume: Postgres/PostGIS is running
(`docker compose up -d`), the schema is migrated (`alembic upgrade
head`), the backend venv is active, and `DATABASE_URL` is set (see
`.env.example`). Run them from `backend/`.

**Nothing here runs automatically.** `app.main` (the FastAPI app) never
imports or triggers any of this. Every command below is a deliberate,
explicit CLI invocation — this is intentional, especially for
`full-reconcile-chicago`, which can fetch Chicago's entire ~8.6M-row
dataset if run unbounded.

---

## Required attribution (before any public release)

Chicago's Terms of Use require this **exact sentence** to appear at any
site that serves an application built on this data (see
[`../sources/chicago.md`](../sources/chicago.md) §10 for the full
disclaimer and citation):

> This site provides applications using data that has been modified
> for use from its original source, www.cityofchicago.org, the
> official website of the City of Chicago. The City of Chicago makes
> no claims as to the content, accuracy, timeliness, or completeness of
> any of the data provided at this site. The data provided at this
> site is subject to change at any time. It is understood that the
> data provided at this site is being used at one's own risk.

This has not yet been added to the (not-yet-built) frontend — flagged
here so it isn't forgotten before any public release.

## Environment variables

| Variable | Required? | Purpose |
|---|---|---|
| `DATABASE_URL` | yes | Target Postgres/PostGIS instance. |
| `CHICAGO_APP_TOKEN` | no | Sent as `X-App-Token` if set. Anonymous access works without one; a token just avoids the shared anonymous-IP throttling pool for repeated real ingestion. Free at Chicago's Socrata developer settings page. **Never commit a real value.** |
| `REMOVAL_CONFIRMATION_RUNS` | no | Fallback used if `Source.removal_confirmation_runs` is unset (default `2`). |

See `.env.example` for the full annotated list.

## 1. One-time setup

```bash
python -m app.cli bootstrap-chicago
python -m app.cli refresh-iucr
```

`bootstrap-chicago` creates (or updates) the Chicago `Source` row —
`source_key`, name, publisher, source URL, timezone convention, and the
plausibility-safeguard thresholds (`min_expected_records=5000000`,
`max_record_count_drop_percent=1.0`, `removal_confirmation_runs=2` —
see the design doc §5 for why these specific numbers). It's idempotent
— safe to run again to update configuration; it never creates a
duplicate `Source` row.

`refresh-iucr` populates the local IUCR reference table from Chicago's
companion dataset. Independent of crime-record ingestion — run it on
its own schedule (it changes far less often).

## 2. Smoke ingestion (do this first, always)

```bash
python -m app.cli smoke-ingest-chicago --limit 500
```

A small, bounded fetch. Always `is_complete=False`, so it can never
trigger deletion inference or move the trusted baseline — safe to run
repeatedly, including against a database that already has real data in
it. Use this to confirm the adapter, schema validation, and DB
connectivity all work before running anything larger. Inspect the
result with `psql`:

```sql
select count(*) from source_records;
select count(*) from raw_source_record_versions;
select count(*) from incidents;
select count(*) from offenses;
```

## 3. Incremental ingestion (routine use)

```bash
python -m app.cli incremental-ingest-chicago
```

Fetches only records with `updated_on` past the source's stored
watermark (plus a 1-hour overlap). Requires a watermark to already be
set — the first `full-reconcile-chicago --confirm` run sets it. Every
candidate record still goes through the same fingerprint comparison as
a full run; `updated_on` only selects candidates, it never substitutes
for a real change check (see design doc §4). The watermark only
advances after a fetch completes without error — a failed fetch leaves
it untouched, so nothing is silently skipped on the next attempt.

There is no scheduler wired up yet (out of scope for this phase) — for
now, run this manually or via cron/a manual trigger of your choosing.

## 4. Full reconciliation

```bash
# Bounded (for benchmarking, or a partial initial population) --
# never treated as a complete census, never affects deletion inference:
python -m app.cli full-reconcile-chicago --limit 100000

# Unbounded -- fetches the ENTIRE current Chicago dataset (~8.6M rows,
# currently estimated at ~2.2 hours -- see design doc §7). Requires
# --confirm as a deliberate safety gate:
python -m app.cli full-reconcile-chicago --confirm
```

This is the only mode that can produce deletion evidence (a record
missing from a complete run) and the only mode that can move the
trusted baseline used by the mass-disappearance safeguard — and only
when the run is *both* complete (`--confirm`, no `--limit`) *and*
plausible (see design doc §5 and
[`../ingestion-framework.md`](../ingestion-framework.md) "Complete vs.
trusted for deletion inference"). A bounded (`--limit`) run is always
treated as incomplete, so it can populate real data without ever
risking a false deletion inference from records outside its bound.

Watch the log line at the end of the run:

```
Full reconciliation complete in NNs: received=... created=... changed=... unchanged=... missing=... plausibility=... deletion_evidence_allowed=...
```

If `plausibility=SUSPICIOUS`, deletion evidence was withheld for that
run — see "Recovering from a suspicious run" below.

## 5. Recovering from a suspicious or failed run

**A suspicious plausibility result** (the fetched count dropped more
than the configured threshold vs. the last trusted baseline, or fell
below the absolute floor): nothing is deleted or reactivated
automatically. Investigate the run's `blocking_reason` and counts
(`ingestion_runs` table, or the CLI's log line) against Chicago's own
status — check whether this looks like Chicago's own well-documented
2016 pipeline-failure pattern (see
[`../sources/chicago.md`](../sources/chicago.md) §5) rather than a real
mass removal. Records that *were* fetched in that run are still stored
normally; only the missing-record inference is withheld. If, after
investigation, the drop is confirmed real and legitimate, an operator
can deliberately promote that run as the new trusted baseline via
`app/repositories/sources.py`'s `promote_run_to_trusted_baseline` (not
exposed through the CLI — intentionally a deliberate, out-of-band
action, not something a script does on its own).

**A failed schema validation:** the run is recorded as `FAILED` with
the specific missing fields/type mismatches in its `error_message`; no
records are fetched or normalized. Fix is to update
`ChicagoSourceAdapter`'s expected schema (`_EXPECTED_TYPES` /
`REQUIRED_FIELDS` in `app/adapters/chicago.py`) to match Chicago's
actual current schema, verify with the opt-in live test (below), then
re-run.

**A failed fetch (network/HTTP error) during incremental ingestion:**
the watermark is not advanced; nothing else changes. Just re-run
`incremental-ingest-chicago` once the underlying problem is resolved.

**In all of the above, prior good state is left intact** — a bad run
never overwrites or discards previously-ingested data.

## 6. Concurrent schema changes during a full reconciliation

`full-reconcile-chicago` (unbounded) currently runs its entire
fetch-and-reconcile cycle inside **one single database transaction**,
committed only at the very end (see
[`../sources/chicago-ingestion-design.md`](../sources/chicago-ingestion-design.md)
"Known limitation: single-transaction full runs" for why, and the
real-world timing this was confirmed against). Two consequences to
plan around, both confirmed during this phase's own validation:

- **Any DDL against `incidents` or `offenses`** (an `alembic upgrade`
  adding or indexing a column) will block for the full duration of an
  in-progress unbounded run — hours, at full scale. `CREATE INDEX
  CONCURRENTLY` avoids blocking *other queries*, but still has to wait
  for the long-running transaction's snapshot to close before it can
  finish, so it will sit in a "waiting for old snapshots" state for as
  long as the reconciliation runs. If you need to apply a migration
  immediately, cancel the DDL statement (or its client) rather than
  waiting — it's safe to retry after the reconciliation run ends. A
  migration using `CREATE INDEX CONCURRENTLY IF NOT EXISTS` /
  `DROP INDEX CONCURRENTLY IF EXISTS` (see migration `0005`) is safe to
  interrupt and re-run.
- **The API will error on the new column until the migration actually
  completes.** SQLAlchemy's ORM builds `SELECT` statements from the
  Python model, not the live database schema — if a model already
  declares a new column (e.g. `Incident.beat`) but the migration adding
  it is still blocked per above, every ORM query touching that table
  fails with `UndefinedColumn` until the migration finishes. This is
  expected, not a data-loss risk, but it means **an application-model
  change and a full-reconciliation run should not be deployed at the
  same time** against a database also serving live API traffic.

Plan schema migrations either before starting a full reconciliation, or
after it completes — not concurrently with it, until reconciliation
itself is broken into periodically-committed batches (see the
"Known limitation" cross-reference above).

## 7. Tests

```bash
pytest                              # full suite -- no live network calls, ever
RUN_LIVE_CHICAGO_TESTS=1 pytest tests/test_chicago_adapter_live.py -v  # opt-in, hits Chicago's real API
```

The default suite (`test_chicago_adapter.py`, `test_chicago_idempotency.py`,
`test_api_incidents_chicago.py`) runs entirely against
`tests/fixtures/chicago_sample_rows.json` (22 real rows captured once
from the live API) and mocked HTTP transports — it must never depend
on Chicago's API being reachable. The live test is opt-in only, for
catching real-world drift (it already caught one: see design doc §9,
the `X-SODA2-Types` vs `dataTypeName` boolean/checkbox discrepancy).
