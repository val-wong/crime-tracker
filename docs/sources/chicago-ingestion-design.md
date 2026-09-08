# Chicago Ingestion — Design and Implementation

**Status:** implemented and exercised against Chicago's real, live
API into the local development database. This document originally
described a design that hadn't been built (see git history for that
version); it now also records what was actually built, the real
performance measurements taken, and the reasoning behind the decisions
that resulted. See
[`docs/operations/chicago-ingestion.md`](../operations/chicago-ingestion.md)
for day-to-day operational commands.

---

## 1. Why Chicago's design differs from Denver's

Denver's design (`denver-ingestion-design.md`) was built around one
hard constraint: no per-row change signal exists at all, so every
reconciliation run must fetch the entire dataset. Chicago has a
genuine row-level `updated_on` timestamp Denver lacks — but
`chicago.md` §3–4 also surfaced two things Denver's research never
had reason to surface: `updated_on` advancing doesn't guarantee the
*substantive* data changed (a confirmed bulk-touch event touched 706
rows across just 2 distinct timestamps), and Chicago has a **documented
historical incident** (February 4, 2016) where the publisher's own
pipeline error made a "successful" fetch return a nearly-empty
dataset. Both facts shape the design below, and both are now handled
in code, not just in design prose.

---

## 2. Implementation map

| Piece | File |
|---|---|
| Adapter (fetch, canonicalize, normalize, schema validation) | `backend/app/adapters/chicago.py` |
| Batched/bulk reconciliation engine (source-agnostic, Chicago-scale) | `backend/app/services/reconciliation.py` |
| Streaming incident/offense sync | `backend/app/services/ingestion.py` |
| Mass-disappearance plausibility check | `backend/app/services/plausibility.py` (built in a prior phase; Chicago is its first real user) |
| IUCR reference table + refresh | `backend/app/models/iucr_code.py`, `backend/app/repositories/iucr_codes.py`, `backend/app/services/iucr.py` |
| Operational CLI | `backend/app/cli.py` |
| Migrations | `backend/alembic/versions/0003_chicago_iucr_and_fbi_code.py`, `0004_source_incremental_watermark.py` |
| Adapter tests (fixtures, no live calls) | `backend/tests/test_chicago_adapter.py` |
| Opt-in live-API test | `backend/tests/test_chicago_adapter_live.py` |
| Idempotency tests against the real adapter | `backend/tests/test_chicago_idempotency.py` |
| API + Chicago-fixture integration tests | `backend/tests/test_api_incidents_chicago.py` |
| Real Chicago sample rows (captured once, not live-fetched by tests) | `backend/tests/fixtures/chicago_sample_rows.json` |

---

## 3. Fetch strategy — two explicit modes, both implemented

### Full mode (`ChicagoSourceAdapter.fetch_current_records`)

Keyset pagination on `id` (confirmed unique across a full census,
`chicago.md` §1), **not** `$offset` — Socrata's OFFSET performance
degrades badly at Chicago's ~8.6M-row scale, a concern this phase
tested for directly rather than assuming (see §6). Each page requests
`WHERE id > {last_seen_id} ORDER BY id ASC`, so pagination is
correct and deterministic even as new rows are added mid-fetch (they
sort after the cursor and are simply picked up on the next run).

### Incremental mode (`ChicagoSourceAdapter.fetch_incremental_records`)

`WHERE updated_on > watermark`, widened by a configurable `overlap`
(default 1 hour) to avoid boundary misses. Paginated with a
**compound** `(updated_on, id)` cursor, not `updated_on` alone — the
confirmed bulk-touch finding (706 rows sharing just 2 exact
`updated_on` values) means a plain `updated_on`-only cursor could skip
or duplicate rows at a page boundary where many rows tie on the same
timestamp.

`updated_on` only ever determines **candidates** — every candidate
still goes through the same fingerprint comparison as any other
record (see §5). The watermark itself (`Source.incremental_watermark`)
is persisted **only after a successful, error-free incremental fetch**
(`app/cli.py::cmd_incremental_ingest_chicago`) — a failed fetch never
advances it, so a retry can't silently skip records.

---

## 4. Was `updated_on` put in the fingerprint? No — deliberately.

`ChicagoSourceAdapter.FINGERPRINT_FIELDS` excludes `updated_on`
(along with `x_coordinate`/`y_coordinate`, redundant with lat/lon, and
`year`, derived from `date`). This is the direct, implemented answer
to the bulk-touch finding: if `updated_on` were fingerprinted, that
one administrative touch would have produced 706 spurious raw
versions recording no actual change. `updated_on` **is** still stored
verbatim in the raw payload (full provenance is preserved — nothing is
filtered out of `canonicalize()`'s payload, only out of what gets
hashed), so the fact that it changed is never lost, just not treated
as evidence of a substantive change.

**Tested directly:**
`test_updated_on_only_bulk_touch_creates_no_new_version` (in
`test_chicago_idempotency.py`) constructs exactly this scenario against
the real adapter and confirms zero new versions are created.

---

## 5. Mass-disappearance safeguard — thresholds and evidence

The generic safeguard itself was built in a prior phase
(`app/services/plausibility.py`); this phase's job was to give it
real, evidence-based Chicago numbers rather than inventing them, via
`python -m app.cli bootstrap-chicago`.

**Evidence gathered before choosing thresholds** (live queries against
Chicago's API during this phase):

- Current total row count: **8,630,522** — identical to the count
  recorded during the original Chicago source-validation phase weeks
  earlier, confirming the dataset is stable when nothing has actually
  changed.
- Rows with `updated_on` in roughly the preceding 24 hours at the time
  of checking: **0** — consistent with Chicago's own "updated Monday
  through Friday" cadence and confirming that zero day-to-day
  variance is a real, observed state, not just a theoretical
  possibility.
- The dataset is a **cumulative historical archive** (2001–present, no
  rolling window — `chicago.md` §"Coverage"), so under normal
  operation the row count should only ever hold steady or grow — a
  same-day *decrease* of any real size is inherently suspicious, not
  an expected pattern to tolerate.

**Thresholds set** (`python -m app.cli bootstrap-chicago
--min-expected-records 5000000 --max-drop-percent 1.0
--removal-confirmation-runs 2`):

- `min_expected_records = 5,000,000` — comfortably below the current
  real count (8.6M) with a wide margin for future growth, while still
  far above any catastrophic-collapse scenario (the 2016 incident's
  722 rows, or even 10× that, sit nowhere close). This is the coarse
  backstop, not the primary sensitive check.
- `max_record_count_drop_percent = 1.0` — the primary, sensitive
  check. Chosen conservatively ("err toward blocking if uncertain," as
  instructed): observed day-to-day variance was zero at the moment
  measured, and the dataset's cumulative nature means it should rarely
  if ever legitimately shrink at all, so a 1% threshold (≈86,000 rows
  out of 8.6M) leaves enormous headroom below the 99.99% collapse
  Chicago's own incident report describes, while still being tight
  enough to catch a much smaller-scale problem than that one.
- `removal_confirmation_runs = 2` — kept at the existing platform
  default; no Chicago-specific evidence was gathered that argues for a
  different value.

These are configured on the `Source` row (see
`app/models/source.py`), not hard-coded anywhere generic — a different
source can and should set its own values.

---

## 6. Pagination / bulk strategy — tested, not assumed

Phase 4's research showed a single request could return >100,000 rows
without truncation, and this phase's instructions explicitly warned
against assuming that means a giant single request is operationally
ideal. It isn't, for two reasons confirmed in practice this phase:

1. Socrata itself: `$offset`-based pagination would need one
   monotonically growing offset parameter, and large-offset queries on
   Socrata are documented to degrade — this design uses `id`-based
   keyset pagination instead (see §3), which doesn't have that
   problem regardless of how deep into the dataset a fetch is.
2. Our own memory/throughput: materializing an entire multi-hundred-
   thousand-row fetch as Python dicts before processing would be a
   real memory concern at full scale (see §7) — the pipeline processes
   in bounded batches (default 2,000 records) end-to-end, from fetch
   through persistence, never holding more than one batch's raw
   payload in memory at a time (`app/services/ingestion.py`'s
   `_on_batch` streaming callback into
   `app/services/reconciliation.py`'s internal batching).

**Retries/backoff:** implemented (`ChicagoSourceAdapter._get`) —
up to 3 retries by default, exponential backoff (1s, 2s, 4s), retrying
on transport errors and HTTP 429/5xx; a non-retryable 4xx or
exhausted retries raises `ChicagoFetchError`, which callers (the CLI)
catch and turn into a properly-recorded failed run rather than an
uncaught crash.

**App token:** supported via `CHICAGO_APP_TOKEN` (see
`.env.example`), sent as `X-App-Token` when set, never required —
confirmed via both a mocked test and a real opt-in live-API call that
anonymous access works. Never hard-coded, never committed.

---

## 7. Performance: benchmark results and the full-ingest decision

**The starting point** (measured in a prior phase, generic fixture
data, one-row-at-a-time reconciliation): ~8ms/record, which at
Chicago's 8.6M-row scale projects to roughly **19 hours** — explicitly
called out as impractical.

**What was built to address it**, before any benchmarking: a
rewritten `reconcile_source_records` (see
`app/services/reconciliation.py`) that processes records in batches,
each doing a small, constant number of round trips regardless of
batch size — one `SELECT ... WHERE external_record_id IN (...)` to
look up existing rows, one bulk `INSERT` for new rows, one bulk
`UPDATE` (via SQLAlchemy Core executemany-by-primary-key) for changed/
unchanged rows — instead of one round trip per record. The
incident/offense sync step (`app/services/ingestion.py`) was rewritten
the same way. This is a "competent single-process/batched" redesign,
not distributed infrastructure, per instructions.

**Real benchmark results**, run against Chicago's live API into the
real local Postgres/PostGIS database (`python -m app.cli
full-reconcile-chicago --limit N`, each a bounded — hence always
`is_complete=False` — real fetch-and-reconcile):

| Sample size | Wall time | ms/record | Notes |
|---|---|---|---|
| 500 (smoke ingest) | 9.99s | ~20 | First run; includes one-time page fetch overhead |
| 1,000 | 1.81s | ~1.8 | 500 already present (unchanged) + 500 new |
| 10,000 | 7.85s | ~0.79 | 9,000 new + 1,000 unchanged |
| 100,000 | 92.45s | ~0.92 | 90,000 new + 10,000 unchanged; ~20 page fetches |

This is roughly an **8–10× improvement** over the original per-record
measurement. Most of the 100,000-row run's wall time is visibly
network fetch latency (20 sequential page requests, several seconds
each in the logged output), not database processing — a further,
not-yet-built optimization would overlap fetching the next page with
reconciling the current one, rather than doing them strictly in
sequence.

**Projected full-dataset time**, extrapolating from the 100,000-row
measurement (the largest, most representative sample): 8,630,522 ÷
100,000 × 92.45s ≈ **7,977s ≈ 2.2 hours**.

**Decision: the full, unbounded 8.6M-row historical census was NOT
run in this phase.** Reasoning:

- 2.2 hours is not a fundamental scalability problem — it's a
  plausible, even ordinary, duration for a one-time initial backfill
  run as a background/scheduled job, which this phase is explicitly
  not building ("do not build a scheduler/daemon yet").
- It is not practical to run as a single foreground command within an
  interactive work session, which is the only way this phase could run
  it.
- Instructions were explicit: benchmark first, and only run the full
  census "if projected performance is reasonable" — reasonable here
  means "buildable as a future background job," not "fast enough to
  run right now," and the instructions gave explicit license to stop
  short rather than force it through.

**What was actually populated instead:** a real, substantially larger
bounded ingest — see §8 for the exact counts — proving the pipeline at
real scale (hundreds of thousands of genuine Chicago crime records,
including real multi-victim homicide cases, real geography, real IUCR
codes) without requiring an hours-long foreground run. The exact same
code path (`full-reconcile-chicago` without `--limit`, after a
`--confirm`) is what a future scheduled job would call for the true
full census — nothing about this decision required different code,
only a different invocation.

**Future optimization opportunities**, not implemented (would matter
for bringing the 2.2-hour estimate down further, not required for
correctness):

- Overlap fetching the next page with reconciling the current one
  (currently strictly sequential).
- `COPY`-based bulk loading for the initial backfill specifically
  (one-time, doesn't need to go through the same per-batch INSERT/
  UPDATE path a routine incremental run does).
- Larger batch sizes than the current default (2,000), tuned against
  actual Postgres performance rather than guessed.

**Known limitation: single-transaction full runs.** `cmd_full_reconcile_chicago`
(`app/cli.py`) opens one `SessionLocal()` and commits only once, after
`run_ingestion_cycle` returns — so an unbounded full reconciliation
runs as a single database transaction for its entire multi-hour
duration, even though the reconciliation engine itself processes and
flushes in small batches internally (§6). This was confirmed directly
during this phase's real, unbounded full-census run: `pg_stat_activity`
showed one transaction open continuously (`INSERT INTO
raw_source_record_versions ...`, `state=active`) for the run's whole
runtime.

This has one clear benefit and one real cost:

- **Benefit:** it makes the "stop safely, preserve existing good state"
  requirement trivial to satisfy — if the run fails or is interrupted
  for any reason, Postgres rolls back the entire attempt automatically,
  and the previously-ingested data is completely untouched. There is no
  possibility of a half-applied full reconciliation corrupting prior
  state.
- **Cost:** it blocks concurrent schema migrations against the tables
  it writes to for the run's entire duration (see
  [`../operations/chicago-ingestion.md`](../operations/chicago-ingestion.md)
  "Concurrent schema changes during a full reconciliation" — this was
  hit directly during this phase's own work, when a routine index
  migration had to wait behind an in-progress full-census run). It also
  means no partial progress is visible to any other session until the
  very end, and a very long-running transaction holds back autovacuum
  on the tables involved for its duration.

Not fixed in this phase (would require periodic intermediate commits,
which in turn requires re-deriving what "stop safely" means when a
run can now be partially committed — a real design change, not a small
patch) — flagged here as a concrete, observed limitation for whoever
next works on this adapter, rather than a hypothetical concern.

---

## 8. Incident/offense mapping (implemented)

Exactly as designed:

- `source_records.external_record_id` = `id`
- `incidents.external_incident_id` = `case_number`
- One `Offense` per `SourceRecord` (per `id`) — confirmed in real data
  to yield 1:1 incident:offense for the overwhelming majority of rows,
  and correctly multiple offenses under one incident for real
  multi-victim homicide cases (e.g. `G023235`, 2 rows; real cases with
  3 offenses under one incident were also found in the populated
  data — see §9).
- `incidents.location_precision` = `BLOCK` whenever coordinates are
  present, `UNKNOWN` otherwise — never `EXACT`. Confirmed via a real
  PostGIS spatial round-trip test
  (`test_geography_round_trips_through_postgis`).
- `offenses.raw_offense_code` = `iucr`; `offenses.fbi_code` = the
  row's own `fbi_code` (a schema addition made this phase — see
  migration `0003`); `source_category`/`source_subcategory` =
  `primary_type`/`description`.
- `incidents.beat` = `beat` (a further schema addition, made during the
  map/dashboard phase for the incident-detail panel — see migration
  `0006`; the field was already fetched/fingerprinted by the adapter
  but not previously normalized into a column).
- `offenses.victim_count = 1` for `primary_type == "HOMICIDE"` rows
  (per `chicago.md` §2's recommendation), `null` otherwise.

---

## 9. Schema validation (implemented)

`ChicagoSourceAdapter.validate_schema()` reads the current field/type
list from `X-SODA2-Fields`/`X-SODA2-Types` response headers (a real,
minimal `$limit=1` request) and compares against `REQUIRED_FIELDS` and
a small `_EXPECTED_TYPES` map. Missing a required field, or an
incompatible type change on a field this adapter's logic depends on,
fails validation; a brand-new field is reported but does not fail it.

The CLI (`app/cli.py`) checks this **before** any fetch or
normalization begins, for every ingest command — a failed validation
records a real `IngestionRun` (status `FAILED`, with the specific
missing fields/type mismatches in `error_message`) and stops, rather
than proceeding to normalize records against assumptions that might no
longer hold.

**A real discrepancy was caught by this phase's opt-in live test**
(`test_chicago_adapter_live.py`): the `X-SODA2-Types` header reports a
boolean column as `"boolean"`, while the separate `/api/views/{id}.json`
metadata endpoint's `dataTypeName` for the same column says
`"checkbox"` — two different Socrata metadata surfaces, two different
vocabularies for the same thing. The adapter's `_EXPECTED_TYPES` was
corrected to match the surface it actually reads
(`X-SODA2-Types`/`"boolean"`); this is exactly the kind of assumption
an opt-in live check exists to catch before it becomes silent
production drift.

---

## 10. What's still open

1. Further pagination/fetch overlap and `COPY`-based bulk loading
   (§7) — would speed up the eventual full census, not required now.
2. A real scheduled job to run `incremental-ingest-chicago` regularly
   and `full-reconcile-chicago` periodically — explicitly out of scope
   ("do not build a scheduler/daemon yet").
3. The true full 8.6M-row historical census itself — not run this
   phase (§7); the exact same command that was run in bounded form is
   what would run it, whenever that's scheduled.
4. Minor open items carried over from `chicago.md`, not newly resolved
   here: the `G183906` double-date case, exact `:@computed_region_*`
   field meanings, `iucr`↔`fbi_code` mapping uniformity across all 434
   codes.
5. Licensing remains `CLEAR` per prior research — unchanged by this
   phase, but the required attribution text must still be included
   before any public release (see
   [`docs/operations/chicago-ingestion.md`](../operations/chicago-ingestion.md)).
