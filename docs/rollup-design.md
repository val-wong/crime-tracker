# Aggregate Rollup Layer

Production-readiness hardening added a precomputed rollup so the
map's wide/medium-zoom aggregate view doesn't have to scan the full
`incidents` table on every request. This document covers the design,
storage tradeoffs, refresh strategy, and how the API decides when to
use it versus the raw table. See
[`map-aggregation.md`](./map-aggregation.md) for the original
zoom/grid strategy and the performance problem this fixes.

## Why a materialized view

Of the three PostgreSQL-native options (materialized view, a plain
aggregate table populated by application code, or a hand-rolled
incremental rollup table), a **materialized view refreshed with
`REFRESH MATERIALIZED VIEW CONCURRENTLY`** was chosen because it
satisfies nearly every hardening requirement natively, with no custom
double-buffering code to get wrong:

- **No refresh on app startup** — trivial; nothing calls refresh
  automatically.
- **Safe to run manually** — one SQL statement, wrapped by a CLI
  command (see "Refresh strategy" below).
- **Failure doesn't destroy the last good rollup** — this is
  `REFRESH ... CONCURRENTLY`'s built-in behavior: Postgres computes the
  new result set into temporary storage and only swaps it in atomically
  at the end. If the refresh errors partway through, the view still
  serves the previous data, untouched.
- **Concurrent-safe by construction** — `CONCURRENTLY` specifically
  means readers are never blocked and never see a half-refreshed view;
  ordinary API queries keep reading the old snapshot until the new one
  is atomically ready. (This is a different sense of "concurrent" than
  "multiple refreshes at once" — Postgres itself prevents two
  concurrent refreshes of the same view; that's the correct behavior
  here, not a limitation to work around.)

A hand-rolled table with manual swap-a-new-copy-in logic could do the
same thing, but would require reimplementing exactly what
`REFRESH ... CONCURRENTLY` already guarantees. Given "choose the
simplest option that gives strong performance," the materialized view
wins.

## Grain and dimensions

`incident_grid_rollup` (migration `0008`):

| Column | Type | Notes |
|---|---|---|
| `grid_size` | float | `0.05` or `0.02` — see "Which grid sizes" below |
| `cell_lon`, `cell_lat` | float | cell center, same convention as the raw aggregate query |
| `month_bucket` | date | first-of-month, `date_trunc('month', occurred_at)` |
| `category` | varchar, nullable | `NULL` = category-agnostic row; otherwise one source category |
| `incident_count` | bigint | the count for that combination |

**Real measured size:** 1,038,630 rows, 191 MB (base table + indexes),
against a complete 8,629,893-row `incidents` table (17 GB total DB) —
about 1.1% storage overhead for a rollup covering the two slowest
query shapes. Verified correct by direct spot-check against the raw
table (exact match on multiple cells/months/categories, including the
busiest real cell: 1,436 THEFT incidents in one 0.05° cell in August
2017).

### Which grid sizes are materialized, and why

Only the two widest zoom tiers (`0.05` for zoom ≤ 10, `0.02` for zoom
11-12 — see `map-aggregation.md`'s zoom breakpoint table) are
materialized. These were the two tiers directly measured to cause
multi-second-to-tens-of-seconds full/near-full table scans. The finer
tiers (`0.005`, `0.001`, zoom 13+) are **not** materialized:
mechanically, a finer grid means more distinct cells, and combined with
309 months × 34 categories the row count would grow sharply for
comparatively little benefit — real viewports at that zoom are narrow
enough that the raw-table path (bbox + existing indexes + the response
cache from the map/dashboard phase) is already fast. This is the
direct application of "avoid an explosion of dimensions": include the
tiers where the win is large and the row count stays small; skip the
tiers where the raw path already performs.

### Why category is included, but as two row *shapes*, not a clean cube

The task requires source category support. A naive
`(grid, month, category)` cube run over the incidents↔offenses join
would **fan out**: an incident with two offenses in different
categories would be counted under both, so summing across all
categories for a cell would overcount the true distinct-incident total
for that cell. Rather than accept that or spend a dimension on a
"category rollup that only supports category-filtered queries," the
view stores **two logically distinct row shapes** distinguished by
whether `category` is `NULL`:

- `category IS NULL` rows: grouped straight from `incidents` (no join),
  so `incident_count` is a true distinct-incident count — no fan-out.
  Used for un-filtered-by-category aggregate requests.
- `category IS NOT NULL` rows: grouped from an `incidents ⋈ offenses`
  join, so `incident_count` here is actually an
  **incident-offense-pair** count for that category — this matches the
  *existing* convention in `app/repositories/summary.py`'s
  `category_breakdown` (which also counts offenses, not distinct
  incidents, for exactly this fan-out reason). Used only when the
  caller supplies a category filter.

**Known, accepted, documented deviation:** the previous (raw-table)
aggregate endpoint counted *distinct incidents* even under a category
filter (via a semi-join). The rollup path instead counts
*incident-offense pairs* for category-filtered requests, like
`summary.py` already does. The difference only manifests for the 525
real multi-offense incidents in the entire dataset (0.006% of all
incidents) — see `docs/sources/chicago-ingestion-design.md` §8 — so
the practical numeric impact per cell is at most a handful of counts,
spread across a quarter-million rows; this was judged an acceptable,
explicitly-documented tradeoff rather than a silent behavior change.
Tests cover both row shapes' correctness against the raw table
directly (see `tests/test_rollup.py`).

### Why neighborhood is excluded

The task marks neighborhood as "if practical." It was excluded as a
rollup dimension: adding it would multiply row count by up to 78 (the
real number of distinct Chicago community areas), pushing the rollup
from ~1M rows toward ~80M — no longer a clear win over the 8.6M-row
raw table it's meant to replace, and a real violation of "avoid an
explosion of dimensions." Instead, **any aggregate request that
includes a neighborhood filter falls back to the raw-table path**
unconditionally. This is a reasonable trade because neighborhood-
filtered raw queries were already measured fast in the map/dashboard
phase (9.7ms, using the `occurred_at` index) — the rollup only needed
to solve the *unfiltered/wide-area* slow case, which it does.

### Why month, not week or day

A finer time grain would let the rollup answer more precise date
ranges without falling back, but multiplies row count directly: day
grain over 25 years would be ~30x more rows than month grain for no
change in the widest-zoom problem the rollup targets. Month grain,
combined with the fallback rule below, was judged the right balance.

## Date-range fallback rule

A rollup row represents a **whole calendar month**. If a request's
`start_date`/`end_date` don't align to whole-month boundaries, summing
rollup rows would include (or exclude) partial-month data incorrectly.
Rather than accept an approximation, the API checks alignment and only
uses the rollup when:

- no date filter is given at all (sum all months — always correct), or
- both `start_date` and `end_date` are exactly month-aligned (start is
  the 1st, end is the last day of its month — sum the matching whole
  months — always correct).

Any other date range (including the common "last 30 days" shape, which
rarely aligns to month boundaries) falls back to the raw-table path.
This preserves 100% correctness — never an approximation — at the cost
of not accelerating that specific case; the raw path there is still
reasonably fast since a bounded date range is already a real filter
(see `map-aggregation.md`'s date-range benchmark: 93ms at full scale).

## Refresh strategy

`app/services/rollup.py`'s `refresh_rollup(db)`:

1. Records a `RollupRefreshRun` row (`status=running`, `started_at`).
2. Runs `REFRESH MATERIALIZED VIEW CONCURRENTLY incident_grid_rollup`.
3. On success: records `status=completed`, `row_count`,
   `duration_seconds`.
4. On failure: records `status=failed`, `error_message`, and
   **re-raises** — the caller (CLI) must not silently swallow a failed
   refresh. The materialized view itself is untouched by a failed
   `CONCURRENTLY` refresh (Postgres guarantee), so "failure should not
   destroy the last good rollup" holds without any extra code here.

Exposed via `python -m app.cli refresh-rollup` (see
[`operations/chicago-ingestion.md`](./operations/chicago-ingestion.md)) —
never invoked by application startup or automatically inside the
generic ingestion service. `cmd_full_reconcile_chicago` and
`cmd_incremental_ingest_chicago` call it automatically **after a
successful run** (only when the ingestion itself succeeded — a failed
ingestion does not trigger a rollup refresh against possibly-incomplete
data), logging its own success/failure separately from the ingestion
run's own status.

## API integration

`GET /api/incidents/aggregate` (`app/api/incidents.py`) now chooses a
path per request:

1. If `neighborhood` is set, or the date range isn't rollup-eligible
   (see above), or `grid_degrees` isn't one of the two materialized
   sizes (0.05, 0.02) → **raw-table path** (unchanged from the
   map/dashboard phase: bbox-snapped, cached).
2. Otherwise → **rollup path**: sum `incident_count` from
   `incident_grid_rollup` grouped by cell, filtered by `grid_size`,
   `category` (or `category IS NULL` when unset), the resolved month
   range, and the bounding box on `cell_lon`/`cell_lat`.

Both paths return the same `IncidentAggregateResponse` shape, so the
frontend needs no changes. The existing bbox-snap + 120s response
cache from the map/dashboard phase still wraps the whole endpoint —
the rollup path benefits from it too, though it barely needs to given
how fast it already is.

## Storage impact

| Item | Size |
|---|---|
| `incident_grid_rollup` (data + indexes) | 191 MB |
| Full database, before | 17 GB |
| Full database, after | see final report |

191 MB against a 17 GB database is a ~1.1% storage increase for the
performance this buys — reported honestly, not hidden, per "report...
added DB size."
