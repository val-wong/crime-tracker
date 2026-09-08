# Map Query & Aggregation Strategy

This document explains how the map/dashboard phase queries incident
data at different zoom levels, and why. It's deliberately small — see
[`ingestion-framework.md`](./ingestion-framework.md) for the ingestion
side and [`architecture.md`](./architecture.md) for the overall system.

## Why two endpoints

At full-dataset scale (hundreds of thousands to millions of points),
sending raw incident markers to the browser for anything wider than a
neighborhood-level zoom is both wasteful (megabytes of JSON, mostly
overlapping pixels) and slow to render. Two endpoints exist so the
frontend can pick the cheap one for the common case (a wide view) and
the precise one only when it's actually useful (a close view):

| Endpoint | Use | Response |
|---|---|---|
| `GET /api/incidents` | Close zoom, list views, incident detail | Individual incidents, paginated, capped at `MAX_LIMIT` (500) |
| `GET /api/incidents/aggregate` | Wide/medium zoom | Grid-cell counts, capped at `max_cells` (default 5000) |

Neither endpoint ever returns the full dataset in one response — see
"Performance" in
[`sources/chicago-ingestion-design.md`](./sources/chicago-ingestion-design.md)
for why that matters at Chicago's real scale.

## Bounding-box incident queries

`GET /api/incidents` accepts `min_lon`, `min_lat`, `max_lon`, `max_lat`
(the same west/south/east/north order MapLibre's `map.getBounds()`
produces). The repository translates this into
`ST_Intersects(location, ST_MakeEnvelope(...)::geography)`, evaluated
against the existing GIST index (`ix_incidents_location`, migration
0001) — no new spatial index was needed.

**Real `EXPLAIN ANALYZE` behavior, checked against the complete
~8.63M-row dataset:** the planner doesn't always choose the spatial
index — because the endpoint also does `ORDER BY occurred_at DESC
LIMIT N`, for a wide bounding box Postgres sometimes prefers walking
`ix_incidents_occurred_at_desc_nulls_last` backward and filtering each
row spatially, rather than scanning the spatial index and sorting the
(potentially large) result afterward. Both plans were observed to be
correct and fast at full data scale; which one wins depends on the box
size and how selective it is relative to the total row count. This is
the query planner doing its job adaptively across two real indexes,
not a sign either index is unused. A tighter bounding box (a close map
zoom, which is when this endpoint is actually called — see "Why two
endpoints" above) makes the spatial index the more obviously
attractive plan.

**Real bug found and fixed via this benchmarking:** the *unfiltered*
`GET /api/incidents` request (no date/category/neighborhood/bbox
filter — the plainest possible call) took ~2 seconds even after
migration 0005 added `ix_incidents_occurred_at`. Root cause, confirmed
via `EXPLAIN ANALYZE`: the query orders by
`occurred_at DESC NULLS LAST`, but a plain ascending btree index's two
natural traversal orders are `ASC NULLS LAST` and `DESC NULLS FIRST` —
`DESC NULLS LAST` matches neither, so Postgres fell back to a full
parallel sequential scan and sort of all 8.6M rows on every request,
regardless of the `LIMIT`. Migration `0007` replaces the index with one
built in the exact matching order
(`(occurred_at DESC NULLS LAST)`) — confirmed directly to turn that
same request into a sub-millisecond backward index scan. This is the
single most impactful fix found during this phase's performance
validation, and a concrete example of why "add an index" isn't enough
on its own — the index's *sort order* has to match the query's ORDER
BY exactly, including NULLS placement, or Postgres can't use it for
that purpose even though it could still serve a plain equality/range
filter on the same column.

## Grid aggregation (no H3)

`GET /api/incidents/aggregate` groups incidents into square
degree-sized cells:

```sql
floor(ST_X(location::geometry) / grid) * grid + grid/2  -- cell center longitude
floor(ST_Y(location::geometry) / grid) * grid + grid/2  -- cell center latitude
```

grouped and counted per cell, ordered by count descending, capped at
`max_cells` (default 5000) so the response size never depends on how
many incidents are in view — only on how many *cells* are, which is
bounded by the viewport and the chosen grid size.

**H3 was deliberately not introduced.** A plain lon/lat grid is
sufficient for a single city at V1: it needs no new dependency, no
precomputed index table, and the cell math is a few lines of SQL. H3
would be worth revisiting if this platform ever needs cross-city
aggregation with consistent cell identity across zoom levels — not a
V1 concern (see docs/product.md "Design restraint").

### Zoom → grid size mapping

The frontend passes a `zoom` level (standard web-map convention,
roughly 0-20); the API maps it to a cell size in degrees using a fixed
lookup rather than a continuous formula, so behavior at each zoom
level is predictable and easy to reason about:

| zoom | grid size (degrees) | approx. cell width |
|---|---|---|
| ≤ 10 | 0.05 | ~5.5 km |
| 11-12 | 0.02 | ~2.2 km |
| 13-14 | 0.005 | ~550 m |
| ≥ 15 | 0.001 | ~110 m |

At zoom ≥ 15, the frontend should generally prefer `/api/incidents`
directly (individual incidents are usually sparse enough at that scale
to render one-by-one, and a click needs a specific incident anyway for
the detail panel) — the aggregate endpoint still works at that zoom if
called, just with small cells.

These specific breakpoints and cell sizes are a pragmatic V1 choice,
not derived from a rigorous rendering-density study — revisit if real
usage shows cells that are too coarse (losing visible clustering detail
users expect) or too fine (too many markers to render smoothly).

### Known limitation: wide-zoom aggregate performance

Measured directly against the complete, real ~8.63M-row Chicago
dataset after this phase's full census (see
`docs/sources/chicago-ingestion-design.md`): a citywide aggregate query
(zoom ≤ 10, the dashboard's default first view) takes **10-22 seconds**
via `EXPLAIN ANALYZE` and a real cold API request, regardless of
whether a bounding box is supplied — because at that zoom, the
bounding box itself covers nearly the entire city, so it excludes
almost no rows. This is a **near-100%-selectivity full-table scan**:
no index (a plain btree, a GIST spatial index, or a functional index
on the grid expression — all three were tried and measured) can make
that faster, because an index only pays off when it lets Postgres
avoid visiting most of the table, and this query legitimately needs to
touch nearly every row to produce a citywide count. `VACUUM ANALYZE`
was run and confirmed current before drawing this conclusion, so it
isn't an artifact of stale statistics either.

**This isn't limited to the single widest zoom bucket.** A follow-up
measurement at zoom 13 with a realistic ~0.1°×0.07° bounding box (still
a fraction of the city, not "citywide") also took ~24-38 seconds — any
box wide enough to leave a large fraction of the table as candidates
hits the same near-full-scan cost, and Chicago's geographic size means
that's true across a wider range of zoom levels than just the very
widest one.

**The correct real fix** is a periodically-refreshed rollup/materialized
table (e.g., one row per coarse grid cell + category + week, refreshed
after each ingestion run) so the wide-zoom endpoint reads a small
precomputed table instead of scanning the raw incidents table. That's
a real architecture addition, not a quick query tweak, and is
explicitly deferred — consistent with `docs/product.md` "Design
restraint" for V1.

**The pragmatic, in-scope mitigation actually shipped this phase:** a
short-lived (120s) in-process response cache in `app/api/incidents.py`,
keyed on grid size + filters + bounding box. Two things were needed to
make it actually effective, not just theoretically effective:

1. A cache keyed on the *exact* bbox alone would miss on almost every
   request in practice — ordinary map panning changes the bbox by a
   few hundredths of a degree on every move, so back-to-back requests
   from the same user looking at roughly the same area would each be a
   cache miss and each cost 10-38s. So the bbox used for **both the
   cache key and the actual query** is first snapped outward to the
   nearest 0.1° boundary (`_snap_bbox`) — nearby pans then land on the
   identical snapped box and reuse the same cached result. The response
   covers a slightly larger-than-requested area, which is an acceptable
   approximation at the zoom levels this matters for (a heatmap-style
   aggregate view, not a precise viewport).
2. The TTL is safe specifically because the underlying data only
   changes via periodic ingestion runs (hours to days apart), not
   continuously.

**Measured effect:** a cold citywide request took 22.8s; the identical
request immediately after was served from cache in 5.5ms. A cold
zoom-13 request took 37.6s; a *panned* (not identical) request landing
in the same snapped bucket was served in 5.0ms. This does not fix the
underlying query cost — the *first* viewer of a given snapped-bbox
combination still pays it — but it means that cost is paid at most
once per 120 seconds per broad area, not once per pan. Restarting the
API process clears the cache (it's in-process, not shared across
workers or persisted) — acceptable for a single-process V1 deployment,
worth revisiting (a shared cache, or the rollup table above) before
running multiple API processes or workers in production.

## Frontend map implementation

`frontend/src/components/MapView.tsx` renders the map with
[MapLibre GL JS](https://maplibre.org/) — an open-source (BSD-licensed)
fork of Mapbox GL JS with no commercial license or usage-based billing,
chosen specifically so this project isn't locked into a paid map
provider (see `docs/product.md` "Design restraint"). It's the largest
single frontend dependency (~1MB minified), so it's loaded via
`React.lazy`/`Suspense` rather than in the main bundle — the rest of
the dashboard (filters, summary, trends) becomes interactive before the
map library finishes downloading.

**Tile/style source:** CARTO's free "Positron" vector basemap style
(`https://basemaps.cartocdn.com/gl/positron-gl-style/style.json`),
built on OpenStreetMap data. No API key or signup is required for this
style at ordinary usage levels, and none is embedded in the frontend
code — this is a deliberate choice, not an oversight: an API-key-gated
provider would mean a secret to manage and a bill to watch, neither
appropriate for a V1 whose whole point is avoiding vendor lock-in. The
required attribution ("© OpenStreetMap contributors © CARTO") is
rendered automatically by MapLibre's built-in `AttributionControl`,
sourced from the style JSON's own metadata — not something this
project's code hard-codes, so it stays correct if CARTO's own
attribution text changes.

**Known constraint:** CARTO's free basemap tier is intended for
reasonable, non-commercial-scale usage and doesn't come with an uptime
SLA or committed rate limit — acceptable for local development and a
V1 demo, but worth revisiting (a paid CARTO plan, a self-hosted tile
server, or another open provider) before any production traffic
commitment. This is flagged here rather than silently assumed away.

## Filters shared between both endpoints

Both `/api/incidents` and `/api/incidents/aggregate` accept the same
filter vocabulary: `start_date`/`end_date`, `category`, `neighborhood`,
and a bounding box. This keeps the frontend's filter state (see
`docs/product.md`, URL state) equally applicable regardless of which
endpoint the current zoom level calls.
