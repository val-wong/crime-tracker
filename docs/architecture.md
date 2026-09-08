# Architecture

## High-Level Flow

```
Public source
  -> ingestion
  -> immutable/raw source snapshot
  -> normalization
  -> validation
  -> PostgreSQL/PostGIS
  -> API
  -> frontend
  -> trend/pattern analysis
```

- **Public source** — a city's official open-data portal or API
  (e.g., a Socrata/ArcGIS endpoint or CSV export). Evaluated per the
  template in [`data-sources.md`](./data-sources.md) before
  integration.
- **Ingestion** — a `SourceAdapter` per source (see
  `backend/app/adapters/`) fetches data and hands it, unmodified, to
  the reconciliation/raw-versioning layer described in
  [`ingestion-framework.md`](./ingestion-framework.md). Adapter code is
  source-specific; it knows how to talk to one city's data format, but
  does not transform the data. No production adapter exists for any
  city yet — only `FixtureSourceAdapter`, used solely by tests.
- **Immutable/raw source snapshot** (`data/raw/`) — the exact response
  from the source, stored as-is (e.g., the original CSV/JSON), tagged
  with when it was fetched and from where. Never edited in place.
- **Normalization** — a separate step that maps raw, source-specific
  records into the internal incident schema below. Normalization is
  the only place that understands the mapping between "however this
  city represents a burglary" and the platform's internal categories.
- **Validation** — schema and sanity checks (required fields present,
  coordinates in range, timestamps parseable) applied to normalized
  records before they are persisted.
- **PostgreSQL/PostGIS** — the system of record for normalized
  incidents. PostGIS extends PostgreSQL with spatial types and indexes
  needed for geographic queries (radius search, containment within a
  neighborhood polygon, etc.). Wired up as of Phase 3 (see
  [`ingestion-framework.md`](./ingestion-framework.md) and the root
  `docker-compose.yml`) — but **only with synthetic test data**. No
  real city's data has been ingested into it; see
  [`sources/denver.md`](./sources/denver.md) for why Denver
  specifically is still held back.
- **API** (`backend/`) — a FastAPI service exposing normalized
  incidents with filters (date, category, location, time). The API
  never exposes raw source records directly; it serves the normalized
  model.
- **Frontend** (`frontend/`) — a React/TypeScript dashboard that
  queries the API to render incidents on a map, list/filter them, and
  show trends.
- **Trend/pattern analysis** — downstream, read-only analysis over
  normalized incidents (counts over time, by category, by area). Runs
  against the normalized data, not the raw snapshots, and never
  produces person-level output (see [`product.md`](./product.md) for
  the constraints that bound this layer).

## Why Raw and Normalized Data Stay Separate

- **Provenance and auditability.** If a normalized record looks wrong,
  the raw snapshot lets you check whether the error came from the
  source or from the normalization logic. Overwriting raw data with
  normalized data destroys that trail.
- **Cities change their schemas.** Source formats, field names, and
  offense-code taxonomies change over time and across cities.
  Preserving the raw record means a future re-normalization (e.g., to
  fix a mapping bug or support a new field) can be replayed from
  the original data without re-fetching it from the source.
- **Reproducibility.** Keeping ingestion (fetch) and normalization
  (transform) as distinct, replayable steps means normalization logic
  can be corrected and re-run deterministically against the same raw
  inputs, rather than mutating already-transformed data.
- **Trust.** Publishing that raw source data is retained unmodified is
  itself a transparency commitment: anyone can verify that a
  normalized incident traces back to a real, unmodified source record.

**Raw versioning for sources that mutate records without a per-row
timestamp:** some sources (confirmed for Denver's PD open data — see
[`sources/denver-ingestion-design.md`](./sources/denver-ingestion-design.md#6-raw-record-preservation-strategy))
explicitly allow existing records to be added, modified, or deleted at
any time, but expose no per-row "last updated" field to tell us which
records changed. For sources like this, "immutable raw snapshot"
should mean an append-only version per source record — a new raw
version is stored only when a record's fingerprint (a hash of its raw
fields) differs from the most recently stored version for that
record's source key — rather than either (a) overwriting the one raw
copy we keep per record, which would lose history, or (b) storing a
full duplicate snapshot of the entire dataset on every ingestion run,
which wastes storage on the (typically large) majority of records that
haven't changed.

**`is_complete` protects against *our* fetch failing, not against the
*source* silently collapsing — this is why "complete" and "trusted for
deletion inference" are tracked as two separate things, not one.**
Chicago's own open-data team publicly documented a real incident
(February 4, 2016) where a bug in their publishing pipeline reduced
their entire crimes dataset to 722 rows — and a client fetching the
API that morning would have received a fully successful, error-free
response reflecting that near-empty state (see
[`sources/chicago.md`](./sources/chicago.md#5-deletion-behavior)). A
technically-successful ("complete") fetch of a corrupted source
snapshot could, after enough consecutive runs, otherwise start
confirming legitimate historical records as removed.

A mass-disappearance plausibility safeguard now sits between "the
fetch completed" and "trust this as deletion evidence" for every
source, not just Chicago's — see
[`ingestion-framework.md`](./ingestion-framework.md#complete-vs-trusted-for-deletion-inference)
for the full explanation, and
[`sources/chicago-ingestion-design.md`](./sources/chicago-ingestion-design.md#5-deletion--mass-disappearance-safeguard-new)
for the Chicago-specific research that motivated it. Configuration
(absolute floor, maximum permitted drop, whether the check is even
enabled) lives per-`Source`, not hard-coded — no real thresholds are
set for any source yet, since no production source exists.

## Normalized Incident/Offense Model

As of Phase 3, this is implemented (schema in
`backend/app/models/`, migration in
`backend/alembic/versions/0001_initial_schema.py`) rather than only
proposed. Full field-by-field documentation lives in
[`ingestion-framework.md`](./ingestion-framework.md) — this section
covers only the shape and why it looks like this.

**Incidents and offenses are separate tables, not one flat row per
record.** A source may report multiple offenses under a single
incident case number — confirmed for Denver's PD open data via a full
census (not a sample): 94.2% of incidents have exactly one offense,
but the remaining 5.8% range up to 8 offenses on a single incident
(see
[`sources/denver-ingestion-design.md`](./sources/denver-ingestion-design.md#2-incident-to-offense-relationship)).
That's common enough that flattening to one row per offense with no
incident-level identity would either double-count incidents or
silently drop real offenses. Each `Offense` traces back to the
`SourceRecord`/raw version that produced it; not every city provides
every field, so most incident/offense fields beyond the identity and
provenance columns are nullable.

**Coordinates are never assumed to be exact.** `incidents.location`
(a PostGIS geography point) is always paired with
`location_precision` (`exact` / `approximate` / `block` /
`intersection` / `suppressed` / `unknown`) — confirmed necessary by
Denver's own data, where published coordinates can correspond to a
block or intersection rather than the true incident location (see
[`sources/denver.md`](./sources/denver.md#3-geographic-precision)).

**The normalized crime taxonomy does not exist yet** (see
[`product.md`](./product.md)) — `offenses.normalized_category` and
`normalized_subcategory` stay nullable until that phase;
`source_category`/`source_subcategory` preserve the source's own
labels in the meantime.

This model is expected to evolve as additional cities are integrated
and gaps in the normalization mapping are discovered. Changes to it
should be reviewed against the separation-of-concerns principle above:
normalization logic changes, raw versions do not.

## Component Map (this repository)

- `frontend/` — React + TypeScript dashboard.
- `backend/` — FastAPI application exposing the incidents API.
  - `app/models/` — SQLAlchemy ORM models (sources, ingestion runs,
    source records, raw versions, incidents, offenses).
  - `app/adapters/` — the `SourceAdapter` interface and
    `FixtureSourceAdapter` (tests/development only).
  - `app/repositories/`, `app/services/` — persistence and
    reconciliation logic; see [`ingestion-framework.md`](./ingestion-framework.md).
  - `alembic/` — database migrations.
- `docker-compose.yml` — local PostgreSQL/PostGIS for development and
  tests.
- `scripts/ingest/` — per-source *production* ingestion scripts (none
  yet; city integration remains a future phase — see
  [`sources/denver.md`](./sources/denver.md)).
- `scripts/research/` — small, non-production exploratory scripts used
  to investigate a source's API before building real ingestion.
- `data/raw/` — raw source snapshots (gitignored by default; see
  root `.gitignore`). Distinct from `raw_source_record_versions` in
  the database (see [`ingestion-framework.md`](./ingestion-framework.md)),
  which is the actual append-only raw store as of Phase 3.
- `data/processed/` — normalized output, if/when normalization is run
  as a batch step outside the database (gitignored by default).
- `docs/` — product, architecture, and data-source documentation.
- `tests/` — cross-cutting/integration tests; unit tests live
  alongside their respective app (`backend/tests/`,
  `frontend/src/**/*.test.tsx`).
