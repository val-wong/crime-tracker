# Crime Tracker

Crime Tracker is a public-interest platform for exploring **aggregate,
area-level crime patterns** using official public crime-incident data
published by cities.

**This project analyzes aggregate public crime data. It does not
predict individual criminal behavior, profile individuals, or claim to
know where or when a specific crime will occur.** See
[`docs/product.md`](docs/product.md) for the full set of safety and
ethics constraints that bound this project in every phase.

## Current Phase: Chicago V1 Map & Dashboard

**Chicago is the initial V1 city.** This repository has a working
production `ChicagoSourceAdapter` (see
[`docs/sources/chicago-ingestion-design.md`](docs/sources/chicago-ingestion-design.md)
and [`docs/operations/chicago-ingestion.md`](docs/operations/chicago-ingestion.md))
ingesting real records from Chicago's official "Crimes - 2001 to
Present" open dataset into PostgreSQL/PostGIS, and a real map/dashboard
frontend (React + MapLibre GL JS) serving that data — see
[`docs/map-aggregation.md`](docs/map-aggregation.md) for the map query
strategy. No forecasting, risk scoring, or predictive-policing features
exist or are planned. The API now also exposes dataset status
(`GET /api/status`), bounding-box/aggregate map queries
(`GET /api/incidents`, `GET /api/incidents/aggregate`), and a
summary/trend endpoint (`GET /api/summary`) — all reported-incident
counts, never risk or danger measures (see
[`docs/product.md`](docs/product.md) "Safety & Ethics Constraints").

Denver's official crime dataset was researched in depth (see
[`docs/sources/denver.md`](docs/sources/denver.md) and
[`docs/sources/denver-ingestion-design.md`](docs/sources/denver-ingestion-design.md))
and is technically well understood, but **production Denver ingestion
remains disabled** pending resolution of its commercial-reuse/
licensing status.

## Architecture Summary

```
Public source -> ingestion -> raw snapshot -> normalization
             -> validation -> PostgreSQL/PostGIS -> API -> frontend
             -> trend/pattern analysis
```

Raw source data and normalized data are kept strictly separate so that
every normalized incident remains traceable back to an unmodified
source record. See [`docs/architecture.md`](docs/architecture.md) for
the full explanation and the normalized incident/offense model.

- **Frontend**: React + TypeScript (Vite), MapLibre GL JS for the map
  (see [`docs/map-aggregation.md`](docs/map-aggregation.md) for the
  tile source and its terms)
- **Backend**: Python + FastAPI, SQLAlchemy, Alembic
- **Database**: PostgreSQL + PostGIS (via `docker-compose.yml` for
  local development) — holds real, production Chicago crime data (see
  [`docs/operations/chicago-ingestion.md`](docs/operations/chicago-ingestion.md)
  for current population status)
- **Testing**: pytest, run against a real local PostgreSQL/PostGIS
  database (backend); Vitest + React Testing Library (frontend)

## Repository Structure

```
frontend/            React + TypeScript dashboard
backend/              FastAPI application, SQLAlchemy models, Alembic migrations
docker-compose.yml    Local PostgreSQL/PostGIS
scripts/ingest/       Per-source production ingestion scripts (none yet)
scripts/research/     Non-production exploratory tooling
docs/                 Product, architecture, and data-source docs
data/raw/             Immutable raw source snapshots (gitignored)
data/processed/       Normalized batch output, if used (gitignored)
tests/                Cross-cutting/integration tests (none yet)
```

## Local Development

### Database (PostgreSQL + PostGIS)

```bash
docker compose up -d
```

Starts Postgres/PostGIS on **host port 5433** (not the default 5432,
to avoid colliding with any other local Postgres instance). The
database and user (`crime_tracker`/`crime_tracker`) are created
automatically by the container; running the test suite (below)
creates the separate `crime_tracker_test` database itself the first
time it runs.

### Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload
```

The API serves at `http://localhost:8000`. Check `GET /health` and
`GET /api/incidents` (returns an empty collection until Chicago data is
actually ingested — nothing is seeded automatically; see
[`docs/operations/chicago-ingestion.md`](docs/operations/chicago-ingestion.md)
for the ingestion commands).

Run backend tests and checks (needs the database running — see
above; the test suite creates and migrates its own `crime_tracker_test`
database automatically):

```bash
cd backend
source .venv/bin/activate
pytest
ruff check .
black --check .
```

To reset your local development database (drops all local data —
safe because it only ever targets the docker-compose-managed
container/volume for this project):

```bash
docker compose down -v
docker compose up -d
cd backend && source .venv/bin/activate && alembic upgrade head
```

### Chicago ingestion

Real production commands (never invoked automatically by app startup)
— see [`docs/operations/chicago-ingestion.md`](docs/operations/chicago-ingestion.md)
for full detail, environment variables, and recovery guidance:

```bash
cd backend && source .venv/bin/activate
python -m app.cli bootstrap-chicago         # one-time: create/configure the Source row
python -m app.cli refresh-iucr              # refresh the IUCR reference table
python -m app.cli smoke-ingest-chicago       # small bounded ingest to verify things work
python -m app.cli incremental-ingest-chicago # fetch only records changed since last watermark
python -m app.cli full-reconcile-chicago --confirm  # full census (long-running; see docs)
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The dashboard serves at `http://localhost:5173` and expects the
backend at `http://127.0.0.1:8000` (see `.env.example` /
`VITE_API_BASE_URL`).

Run frontend tests and checks:

```bash
cd frontend
npm test
npm run lint
npm run format
npm run build
```

If `node`/`npm` aren't installed locally, run the same commands via the
`node:20-slim` Docker image (works well; used to develop and validate
this phase):

```bash
cd frontend
docker run --rm -v "$(pwd)":/app -w /app node:20-slim npm install
docker run --rm -v "$(pwd)":/app -w /app node:20-slim npm test
```

### Environment Variables

Copy the root `.env.example` to `.env` and adjust as needed:

```bash
cp .env.example .env
```

`DATABASE_URL` points at the docker-compose Postgres/PostGIS instance
by default (port 5433). `TEST_DATABASE_URL` and
`REMOVAL_CONFIRMATION_RUNS` are also read by the backend, as is the
optional `CHICAGO_APP_TOKEN` (anonymous access works without one; see
[`docs/operations/chicago-ingestion.md`](docs/operations/chicago-ingestion.md))
— see the comments in `.env.example`.

## Roadmap

1. **Bootstrap** — scaffolding, docs, minimal skeleton. *Done.*
2. **Source research** — investigate a candidate city's official data
   source in depth before building anything against it. *Done for
   Denver* — see [`docs/sources/denver.md`](docs/sources/denver.md).
3. **Ingestion foundation** — source-agnostic persistence,
   reconciliation, and versioning, proven against synthetic fixtures
   only. *Done* — see [`docs/ingestion-framework.md`](docs/ingestion-framework.md).
4. **Single-city ingestion (V1)** — a real production adapter
   (Chicago), IUCR reference data, incremental + full-reconciliation
   ingestion, real filtering/pagination via the API. *Done* — see
   [`docs/sources/chicago-ingestion-design.md`](docs/sources/chicago-ingestion-design.md)
   and [`docs/operations/chicago-ingestion.md`](docs/operations/chicago-ingestion.md).
5. **Map & dashboard (V1, this phase)** — a real map/dashboard frontend
   (MapLibre GL JS), bounding-box and aggregate map queries, dataset
   status/freshness, summary/trend cards, data-quality caveat UX,
   URL-persisted filters. *Done* — see
   [`docs/map-aggregation.md`](docs/map-aggregation.md). Normalized
   cross-city taxonomy, additional cities, and any risk/danger framing
   are explicitly not part of this phase (see
   [`docs/product.md`](docs/product.md) "Design restraint").
6. **Multi-source normalization** — extend to additional cities (e.g.
   Denver, once its licensing status is resolved).
7. **Trend detection** — area/category-level historical trend
   analysis with documented methodology.
8. **Explainable, aggregate risk indicators** — area-level and
   category-level statistical indicators derived from historical
   patterns only, never resolving to an individual.

See [`docs/product.md`](docs/product.md) for full scope and non-goals
per phase.

## Safety Principles

- No individual profiling, offender identification, or person-level
  risk scores — ever.
- No claims that a specific crime will occur at a specific place or
  time.
- All analysis operates on aggregated, area-level and category-level
  historical data.
- Any future risk or trend indicator must be explainable and
  traceable to its underlying data and methodology — no opaque
  machine-learning predictions.
- Raw source data is preserved unmodified alongside normalized data so
  every statistic can be traced back to its origin.

Full detail: [`docs/product.md`](docs/product.md).
