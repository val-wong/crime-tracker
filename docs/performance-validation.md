# Performance Validation (Production Readiness Hardening)

All numbers measured against the **complete, real 8,629,893-incident /
8,630,522-offense Chicago dataset** (17 GB base, before this phase's
changes), via repeated distinct requests (not a single cached
best-case) hitting a real running API process. See
[`rollup-design.md`](./rollup-design.md) for the aggregate rollup and
[`reconciliation-transactions.md`](./reconciliation-transactions.md)
for the reconciliation redesign.

## 1. Aggregate — citywide (zoom ≤ 10, rollup path)

6 distinct bounding boxes (0.15° apart, so each lands in a different
snapped/cache bucket — genuinely separate queries, not repeats):

| Request | Time |
|---|---|
| #1 | 7ms |
| #2 | 13ms |
| #3 | 17ms |
| #4 | 15ms |
| #5 | 32ms |
| #6 | 10ms |

**Before this phase:** 10-22 seconds (full/near-full table scan every
time; see `map-aggregation.md` "Known limitation: wide-zoom aggregate
performance"). **After:** consistently under 35ms — a >500x
improvement, well under the ~500ms target.

## 2. Aggregate — medium zoom (zoom 11-12, rollup path)

Same distinct-bucket methodology:

| Request | Time |
|---|---|
| #1 | 7ms |
| #2 | 15ms |
| #3 | 43ms |
| #4 | 35ms |
| #5 | 29ms |
| #6 | 7ms |

**Before:** up to 37.6s cold (see `map-aggregation.md`). **After:**
under 45ms throughout.

## 3. Aggregate — close zoom (zoom 13-14, raw table, unchanged path)

A real bug was found and fixed here (see "Bugs found and fixed"
below): bbox snapping — designed for the wide-zoom cache — was being
applied to this path too, and could inflate a genuinely small
close-zoom viewport into a query touching millions of rows in a dense
area (measured: 24-32 seconds). After restricting snapping to the
rollup-eligible tiers only, 8 distinct realistic-size (~0.02°) boxes
spread across the city:

| Request | Time |
|---|---|
| #1 | 15ms |
| #2 | 13ms |
| #3 | 124ms |
| #4 | 1,471ms |
| #5 | 485ms |
| #6 | 688ms |
| #7 | 138ms |
| #8 | 10ms |

Median well under 500ms; one outlier at 1.47s in a dense area (still a
>16x improvement over the pre-fix 24-32s worst case). This remains the
one place performance isn't uniformly excellent — see "Remaining
production blockers" in the final report. Not addressed further this
phase: the close-zoom raw-table path was explicitly told to stay
as-is ("at close zoom levels, continue using the raw incident table"),
and the true fix (extending the rollup to a third, finer grid tier)
was deliberately not built — see `rollup-design.md` "avoid an
explosion of dimensions."

**Update (Pre-Staging Cleanup phase): root cause identified and fixed
— see §9 below.**

## 9. Close-zoom dense-area outlier (Pre-Staging Cleanup phase)

Investigated via repeated `EXPLAIN (ANALYZE, BUFFERS, TIMING ON)` runs
of the same dense-downtown close-zoom query (a ~0.02°×0.01° bbox,
matching ~155,000 rows). Two back-to-back identical runs both cost
~1.72s with nearly identical buffer stats (`shared hit≈15,300
read≈144,000` both times) — the *second* run showed no cache benefit
at all, which ruled out a simple one-time cold-cache effect and pointed
at the buffer pool itself.

Root cause: `shared_buffers` was left at Postgres's stock default,
**128MB** — far smaller than the ~1.1GB of distinct 8KB pages
(`read≈144,000 × 8KB`) this single query's GIST index scan touches over
a dense area. The buffer pool couldn't hold the query's own working
set, so pages read at the start of the scan were already evicted by the
time the scan reached the end — an identical repeat query got zero
benefit no matter how many times it ran, because there was never enough
room for anything to "stick."

This is the taxonomy category **cache miss / cold cache behavior caused
by an undersized buffer pool** (not spatial-index selectivity — the
GIST index is being used correctly; not join cost — there is no join;
not count aggregation — the aggregate itself is cheap; not planner
choice — the plan was already the right one).

**Fix:** raised `shared_buffers` from 128MB to 1GB
(`docker-compose.yml`, `command: ["postgres", "-c",
"shared_buffers=1GB"]`) — comfortably within this container's 7.75GB
memory limit (observed usage ~1.9GB before the change) and the host's
16GB. This is a standard, low-risk Postgres tuning change for a
single-instance dev/local database, requiring a container restart to
take effect (verified: the app reconnects cleanly afterward).

**Measured effect**, same dense downtown area, repeated after the
change: run 1 (right after container restart, genuinely cold on both
Postgres's buffer pool *and* Docker Desktop's virtualized disk) —
25.9s. Run 2 — 2.96s. Run 3 — 1.63s, with `Buffers: shared hit` fully
covering the scan and **zero reads**. A second, previously-untouched
dense area confirmed the same pattern at a much smaller scale: run 1
(first touch) 3.33s, run 2 351ms, run 3 298ms (fully cached, zero
reads).

**Interpretation:** repeat queries against the same dense area now
stabilize at ~300ms–1.6s instead of never improving past ~1.7s — the
outlier as originally reported is resolved for the case that matters
(a user panning/zooming around the same area of the map, which repeats
requests against overlapping data). A **true** first-touch cold read
(right after a Postgres restart, or a genuinely never-before-queried
area) still costs several seconds — this is expected, one-time, and
not worth engineering away further for a V1 local/dev deployment: a
long-running server only pays this cost once per region of the data,
and a fresh container restart is not a hot code path. Documented here
as expected behavior rather than further optimized, per this phase's
explicit "only fix if there's a clear low-risk fix, otherwise document"
instruction — the buffer-pool-size fix already applied *is* that
low-risk fix; chasing the residual first-touch cost further (e.g.
pre-warming, larger shared_buffers still) was judged not worth it for
a dev database with no concurrent real users.

## 4. Summary endpoint

5 distinct full-year ranges: 1.77s – 2.48s, consistently. Root cause
(via `EXPLAIN ANALYZE`): `category_breakdown`'s
`incidents ⋈ offenses` join does a parallel sequential scan of the
entire 8.6M-row `offenses` table for every request — there's no way to
jump straight from a date-filtered `incidents` subset to matching
`offenses` rows without touching most of the table, given the current
schema. **Not fixed this phase** — this endpoint was not part of the
explicit rollup/reconciliation scope, and the same
`incident_grid_rollup` infrastructure built this phase (grid, month,
category) could very plausibly serve it too (sum across grid cells
instead of by cell) as a well-scoped future addition — flagged here
rather than silently left unexplained.

**Update (Pre-Staging Cleanup phase): fixed — see §8 below.**

## 8. Summary endpoint — category breakdown (Pre-Staging Cleanup phase)

Confirmed via `EXPLAIN (ANALYZE, BUFFERS)` against the real dataset:
`category_breakdown`'s `incidents ⋈ offenses` join took **~24s** for a
real one-year range. Postgres correctly refuses to use
`ix_offenses_source_category` for a non-selective category like THEFT
(matches >20% of the table — a sequential scan is genuinely cheaper
there), so no index alone fixes this; it's a full-table-scale problem
regardless of indexing.

Fixed by reusing the existing `incident_grid_rollup` (already built for
the map's aggregate endpoint) rather than building a new denormalization
layer, per this phase's explicit constraint. `rollup_repo.category_breakdown`
sums `incident_count` by category across the rollup's fixed 0.05° grid
tier. Eligibility gate: no neighborhood filter (not a rollup dimension)
and a month-aligned date range (`rollup_repo.is_month_aligned_range`) —
anything else falls back to the original raw query unchanged, so
semantics are never silently altered for a request the rollup can't
answer exactly. A dedicated test
(`test_rollup_and_raw_category_breakdown_agree_on_month_aligned_range`)
asserts numeric equality between the rollup and raw results for the
same real range, guarding against drift.

**Measured:** rollup-path `category_breakdown` alone: ~76ms warm (~300x
faster than the ~24s raw join). Full `/api/summary` response (all 4
sub-queries + HTTP/serialization overhead) for a month-aligned
full-year range: 0.59–0.78s warm (down from the reported ~2s baseline).
The far more common real-world case — no explicit date filter, the
default rolling 30-day window — was already 100-200ms warm and remains
so.

**Deliberately not extended:** `count_in_range`,
`incidents_by_time_bucket`, and `top_neighborhoods_by_incident_count`
are also slow when category-filtered (measured 4.8s and 1.96s for a
category-filtered full year) but were **not** routed through the
rollup, because doing so would silently change their counting semantics
from *distinct incidents* to *incident-offense pairs* — a real,
reachable divergence for a HOMICIDE-category filter given the
multi-victim-homicide pattern in the data. Preserving exact semantics
was an explicit, higher-priority constraint than closing this
remaining gap; documented here as an accepted V1 limitation rather than
silently left unfixed.

**Update (filter-consistency bugfix): the "Eligibility gate" above was
incomplete.** It only named "no neighborhood filter" — it never
mentioned `category`, and neither did the code: `_category_breakdown`'s
rollup branch was gated on `neighborhood is None` alone, so a request
with a `category` filter *and* a month-aligned date range still took
the rollup path. `rollup_repo.category_breakdown` has no `category`
parameter at all, so that filter was silently dropped — the breakdown
(and therefore `most_common_category`) reflected *all* categories in
range, not just the selected one, regardless of which path served it.
A second, independent instance of the same class of bug was found in
`top_neighborhoods_by_incident_count`, which never accepted or applied
a `neighborhood` argument at all (always ignored it, in both the
service-layer call and the repository function itself) — so
`most_represented_neighborhood` could name a neighborhood other than
the one explicitly filtered. Both are now fixed: the rollup gate is
`category is None and neighborhood is None and is_month_aligned_range(...)`,
and both repository functions correctly thread every filter (category
*and* neighborhood) through `apply_incident_filters` on every path
(raw and rollup-gate). See app/services/summary.py and
app/repositories/summary.py, and
`backend/tests/test_summary_filter_consistency.py` for the regression
coverage.

**A previously-undetected, unrelated bug found during this
investigation:** `query_rollup`'s date-filter SQL used
`:start_date::date` / `:end_date::date` — a bind parameter immediately
followed by a `::cast` with no space. SQLAlchemy's `text()` bindparam
scanner fails to recognize this as a parameter token at all, silently
drops it from what's sent to the driver, and leaves the literal
`:start_date::date` in the SQL for Postgres to reject as a syntax
error. This was a **real, reachable production bug**: any user
filtering the map's aggregate view by an exact calendar month at
wide/medium zoom would have hit a 500 error — it went undetected only
because every prior rollup test used either no date filter or a
deliberately non-month-aligned range (which falls back to the raw
path). Fixed by removing the unnecessary cast (the Python `date` value
already has the correct type; `date_trunc` accepts it directly) in both
`query_rollup` and the new `category_breakdown`. Two regression tests
added (`test_query_rollup_with_an_explicit_month_aligned_date_range`,
`test_api_uses_rollup_with_an_explicit_month_aligned_date_range`).

## 5. Incident list (no bbox)

5 distinct offsets: fast at realistic offsets (33-89ms up to
offset=20,000), degrading at extreme offsets (524ms at 200,000; 4.0s at
2,000,000) — the well-known cost of `OFFSET`-based pagination scanning
and discarding rows to reach a deep page. Not a regression and not
fixed: the frontend never requests a deep offset (`MapView` always
fetches from `offset=0`; there is no deep pagination UI), so this is a
theoretical rather than an actually-hit cost — documented, not silently
ignored.

## 6. Bounding-box query (`/api/incidents`, realistic pan-sized box)

6 distinct positions (~0.02°×0.02° boxes): 14ms, 14ms, 303ms, 269ms,
302ms, 342ms — all comfortably sub-second, most well under 350ms.

## 7. Dataset status endpoint (`/api/status`)

Found via real browser QA (see the final report's browser/mobile QA
section), not the curl-based sweep above: the header's status banner
never resolved, staying on "Checking dataset status…" indefinitely.
Direct investigation found two compounding real bugs:

1. **Duplicate fetching.** `App.tsx` and `StatusBanner.tsx` each called
   `fetchStatus()` independently, quadrupling concurrent load on the
   endpoint under React StrictMode's double-invoked effects. Fixed:
   `StatusBanner` now takes `status`/`loading` as props from a single
   fetch in `App.tsx`.
2. **`count_offenses` was genuinely slow on its own** — confirmed via
   `EXPLAIN ANALYZE` to take ~17 seconds at full Chicago scale. Root
   cause: counting a source's offenses required joining `offenses` to
   `incidents` (offenses had no direct `source_id`), forcing a parallel
   sequential scan of both multi-million-row tables into a hash join.
   Fixed by denormalizing `source_id` directly onto `offenses`
   (migration `0009`) and querying it directly — a single indexed
   count.

**A third factor, found immediately after the migration:** the
denormalization migration's ~8.6M-row single-transaction `UPDATE` (see
`reconciliation-transactions.md`'s note on this same migration) left
the `offenses` table bloated with dead row versions and the planner's
statistics on the new column stale — `/api/status` was *still* slow
(21-34s) immediately after the migration finished, even though the
query itself was now a plain indexed count. `VACUUM ANALYZE offenses`
(38s, one-time) resolved it completely. This is now a documented
operational step: **any migration that bulk-rewrites a large table
should be followed by `VACUUM ANALYZE` on that table** before judging
its performance impact — measuring immediately after a big rewrite and
before vacuuming would have been a misleading benchmark.

**Measured effect:** `/api/status` dropped from 29-34 seconds to
0.67-1.04 seconds once all three fixes (deduplicated fetch, denormalized
count, post-migration vacuum) were in place — a >30x improvement.

## Bugs found and fixed during this validation

1. **Bbox snapping applied to the close-zoom raw path** (see §3 above)
   — fixed by restricting `_snap_bbox` to only the rollup-eligible
   (wide-zoom) requests; the raw path now always uses the caller's
   exact bounding box.
2. **Unbounded `COUNT(*)` in `/api/incidents`'s pagination `total`** —
   confirmed via `EXPLAIN ANALYZE` to take 1.8s on its own for a
   bounding box matching 175,000 rows in a dense area, on top of the
   list query itself. Fixed with a capped count (`LIMIT 10,000` inside
   the count subquery — see `app/repositories/incidents.py::COUNT_CAP`):
   exact for any realistic result set, and fast-by-construction
   (Postgres stops scanning once it hits the cap) for a pathological
   one. Confirmed effect: the same dense-area request dropped from
   ~1.9-3.9s to 0.29-0.56s.

3. **`/api/status` never resolving (duplicate fetch + slow
   `count_offenses`)** — see §7 above. Found via real browser QA, not
   the curl sweep, which is exactly why both kinds of validation
   (repeated direct requests *and* an actual browser session) were
   worth doing — a curl-only pass would have measured `/api/status` in
   isolation and reported a slow-but-working endpoint, never surfacing
   that the real page issued it four times concurrently.

All three were found by deliberately testing multiple distinct real
positions/scenarios rather than a single spot-check — exactly the
"p50-ish repeated timings, not just one lucky request" this validation
pass was asked to produce.

## 10. Database bloat cleanup (Pre-Staging Cleanup phase)

Inspected real physical bloat (not just dead-tuple counts, which were
already 0 on every large table — a regular `VACUUM` had already run
after the earlier backfill/denormalization migrations) via the
`pgstattuple` extension, which does a real full-table scan to measure
actual free space inside each table's pages:

| Table | Total size | Free space | Free % |
|---|---|---|---|
| `offenses` | 4,458 MB | 1,349 MB | **47.77%** |
| `raw_source_record_versions` | 8,109 MB | 495 MB | 6.39% |
| `source_records` | 2,789 MB | 126 MB | 6.92% |
| `incidents` | 3,936 MB | 27 MB | 1.18% |

`offenses` stood out clearly: nearly half the table was genuinely
empty, reclaimable space — real MVCC bloat left over from the earlier
backfill migration's bulk `UPDATE` (see §7 above) that a regular
`VACUUM` cleans up *inside* the table's existing pages but cannot
return to the OS; only a full rewrite does that. The other three tables'
free space (1–7%) is normal operational overhead, not bloat — not worth
the lock and I/O cost of a rewrite.

Before running anything: confirmed no application process was
connected to the database (no `uvicorn` process running this phase),
and confirmed disk headroom — the Docker Desktop VM's backing disk had
190GB free, and the host had 17GB free (`VACUUM FULL` needs roughly the
table's own size again in temporary space; `offenses` alone needed
~4.5GB, well within both).

**Action taken:** `VACUUM (FULL, ANALYZE, VERBOSE) offenses;` only —
not the other three tables, since their bloat was within normal range
and didn't justify the exclusive-lock/rewrite cost. Took 28s CPU-reported
(57.8s wall including psql/docker overhead).

**Result:**

| | Before | After |
|---|---|---|
| Total size | 4,458 MB | 2,308 MB (**-48%**) |
| Table only | 2,693 MB | 1,413 MB |
| Indexes only | 1,764 MB | 895 MB |
| Free space (`pgstattuple`) | 47.77% | 0.87% |

Data integrity verified after the rewrite: row count unchanged
(8,630,522 before and after) and an `offenses ⋈ incidents` join count
matches the raw offense count exactly (no orphaned or lost rows). A
database-wide `ANALYZE` was run afterward as routine post-change
maintenance (7.98s).
