# Reconciliation Transaction Design

Production-readiness hardening replaced full reconciliation's
single-transaction design with a checkpointed one. This document covers
why, exactly where the transaction boundaries now are, and how failure
recovery works. See
[`sources/chicago-ingestion-design.md`](./sources/chicago-ingestion-design.md)
§7 "Known limitation: single-transaction full runs" for the original
problem this fixes.

## The problem

`full-reconcile-chicago` (unbounded) previously ran its entire
fetch-and-reconcile cycle inside **one database transaction**, held
open and committed only once at the very end — confirmed directly
during the real, unbounded Chicago census: `pg_stat_activity` showed
one transaction (`INSERT INTO raw_source_record_versions ...`) open
continuously for the run's whole ~3h44m duration. This had one real
benefit (a failure rolled back cleanly, with zero risk of partial
corruption) and one real, measured cost: it blocked concurrent schema
migrations and held back autovacuum for the entire run, and a crash
anywhere in those ~3.7 hours discarded the *entire* run's progress,
however far it had gotten.

## The redesign: checkpointed batches, not one transaction

`app/services/reconciliation.py`'s `reconcile_source_records` already
processed records in bounded batches (2,000 records each, doing O(1)
round trips per batch — see `docs/sources/chicago-ingestion-design.md`
"Performance"). The redesign adds an optional
`checkpoint_every_n_batches` parameter: when set, the function commits
`db` after every N batches, turning the single multi-hour transaction
into many short ones.

### Transaction boundaries

| Boundary | What's inside it | Committed when |
|---|---|---|
| Run start | The `IngestionRun` row itself (`status=running`) | Immediately, before any fetching begins |
| One checkpoint | N batches' worth of `SourceRecord`/`RawSourceRecordVersion` writes *and* their `Incident`/`Offense` sync (via the `on_batch` callback) | Every N batches (default: 5 batches = ~10,000 records) |
| Final step | Missing-record evidence + plausibility evaluation + baseline promotion | Once, after every batch has been processed |

**One batch's SourceRecord/RawVersion writes and its incident/offense
sync are always committed together, never split across a commit
boundary** — `on_batch` (which does the incident/offense sync) runs
*before* the checkpoint commit for that batch, so a checkpoint always
captures a fully-consistent unit of work. A batch never leaves a
`SourceRecord` committed without its corresponding `Incident`/`Offense`
rows, or vice versa.

**What checkpointing does *not* change:** missing-record detection and
plausibility evaluation (`app.services.plausibility`) still only ever
run once, after the *entire* fetch has been processed — exactly as
before. Checkpointing changes *when data is committed*, never *when
deletion evidence is computed*. This is the key property that makes
the redesign safe: "a partial/failed run must never look like deletion
evidence" (docs/ingestion-framework.md) holds exactly as it did before,
because the code path that computes deletion evidence hasn't moved —
it's simply now guaranteed to run within a fresh (post-checkpoint)
transaction rather than the run's one giant one.

## Failure recovery

`app/services/ingestion.py`'s `run_ingestion_cycle` wraps the
reconciliation call: if it raises for any reason, the function rolls
back any not-yet-checkpointed work, finalizes the `IngestionRun` row as
`FAILED` with the exception's message, commits *that*, and re-raises.
Concretely, this means:

- **Everything checkpointed before the failure is durably committed** —
  real `SourceRecord`/`Incident`/`Offense` data, not lost.
- **Nothing since the last checkpoint is** — a clean rollback, not a
  half-applied batch.
- **The `IngestionRun` row is never left stuck showing `RUNNING`**
  forever (previously, an exception mid-run left no trace at all, since
  the entire transaction rolled back including the run row itself —
  now the run row is committed immediately at start, so a failure is
  always visible and inspectable via `ingestion_runs`).
- **No missing-record evidence is ever applied** — the failure always
  happens before reaching that step (either during the batch loop, or
  the batch loop completing but a later step failing — either way,
  execution never reaches the point where deletion evidence would be
  written).
- **The source's trusted baseline (`last_trusted_active_count`,
  `last_trusted_run_id`) is never moved** by a failed run — that update
  only happens as part of the same final step that's skipped on
  failure.
- **The incremental watermark is never advanced** by a failed run — the
  CLI only sets it after `run_ingestion_cycle` returns successfully.

Verified directly (`tests/test_reconciliation_checkpointing.py`): a
simulated crash partway through a run leaves exactly the
already-checkpointed records committed, marks the run `FAILED` with the
real error message, leaves previously-active records completely
untouched (still active, zero missing-runs, unchanged removal
confidence), and leaves the source's trusted baseline exactly where it
was before the failed attempt.

## How a partially-processed run resumes or restarts safely

**The documented, safe recovery is to simply re-run the same command
from scratch** (`full-reconcile-chicago --confirm` again) — not to
build cursor-based resume logic. This is deliberate, not a shortcut:

- Reconciliation is idempotent per record: re-fetching an
  already-committed record either finds it unchanged (no-op, just
  updates `last_seen_at`) or genuinely changed (correctly creates
  exactly one new version). Nothing about re-processing
  already-checkpointed records is unsafe or produces duplicates (the
  unique constraint on `(source_id, external_record_id)` guarantees
  that, and reconciliation's own "existing vs. new" branch handles it
  either way).
- Because a failed run never applies missing-record evidence or moves
  the baseline (see above), there is no inconsistent intermediate state
  to "clean up" before retrying — the source's state is exactly as
  trustworthy as it was before the failed attempt.
- Building true resume-from-cursor logic would add real complexity
  (persisting and validating a fetch cursor across process restarts,
  handling the case where the source's data shifted between attempts)
  for a benefit that's already mostly captured by checkpointing itself:
  the *previous* attempt's checkpointed batches are already committed,
  so a full re-run mostly just re-confirms "unchanged" for those and
  quickly catches up to new ground — it does not repeat the *cost* of
  those records' database writes at anywhere near the original rate
  (an "unchanged" record is one lightweight bulk `SELECT`/`UPDATE`
  touch, not a full re-normalization).

Verified directly
(`test_rerun_after_failure_is_safe_and_completes_correctly`): after a
simulated failure partway through a 6-record fetch, simply re-running
the identical fetch produces the correct final state (all 6 records
present, no duplicates, 4 correctly reported as unchanged and 2 as
newly created) with no special recovery steps.

## Configuration

`--checkpoint-batches N` (default 5, i.e. ~10,000 records per
checkpoint at the default 2,000-record batch size) is available on
`full-reconcile-chicago` and `incremental-ingest-chicago`. Passing `0`
disables checkpointing entirely, reverting to the original
single-transaction behavior (kept as an escape hatch, not the default).
The default was chosen as a reasonable balance — frequent enough that a
crash loses at most a few minutes of progress, infrequent enough that
commit overhead stays negligible relative to the batch work itself; it
has not been tuned against a real production incident, since none has
occurred, and can be revisited if real operational experience suggests
otherwise.

## A related lesson from this same phase's own migration work

Migration `0009` (denormalizing `Offense.source_id` — see
`docs/performance-validation.md`) backfills all ~8.6M existing
`offenses` rows with a single unbatched
`UPDATE ... FROM incidents WHERE ...`. In practice this took
**57 minutes 48 seconds** against the real database, I/O-bound
(`DataFileRead`) the whole time — a genuinely long-running single
transaction, for exactly the same structural reason full reconciliation
used to be one: no intermediate commits. This wasn't batched to match
the checkpointing pattern above, on the reasoning that a **one-time
schema migration** has a different risk profile than a **recurring
ingestion job** (no concurrent DDL to block, no repeated multi-hour
exposure), but the real measured duration is worth recording honestly
here rather than only in a benchmark table: if a future migration needs
to backfill a similarly-large column, applying the same batched-commit
approach documented above (chunk by primary key range, commit
periodically) would be the more consistent, hardening-minded choice,
and this run is the concrete evidence for why.

**A second lesson from the same migration:** immediately afterward,
the very endpoint this migration was meant to speed up
(`GET /api/status`) was *still* slow (21-34s) — not because the fix was
wrong, but because a ~8.6M-row rewrite leaves the table bloated with
dead row versions and the query planner's statistics on the new column
stale, and neither is cleaned up automatically until the next
autovacuum cycle gets to it. A one-time `VACUUM ANALYZE offenses`
(38s) resolved it completely — see `docs/performance-validation.md` §7.
**Any migration that bulk-rewrites a large table should be followed by
an explicit `VACUUM ANALYZE` on that table**, and performance shouldn't
be judged until after that's done.
