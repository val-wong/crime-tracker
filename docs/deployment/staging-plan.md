# Staging Deployment Plan

**Status of this document:** a plan only. **Nothing has been deployed.**
No platform account has been created, no infrastructure provisioned, no
data transferred. This is the Staging Deployment Planning phase output
— see the root `README.md` roadmap for where this sits relative to the
rest of the project.

**Method, matching this repo's own documentation convention** (see
`docs/sources/chicago.md`): claims are labeled **CONFIRMED** (verified
directly by reading this repo's code/config/docs), **ESTIMATE**
(a reasoned number from measured evidence in this repo, not a
guarantee), or **ASSUMPTION — VERIFY BEFORE COMMITTING** (a claim about
an external hosting platform's current capabilities/pricing, which
this document cannot directly verify and which changes over time).

---

## 1. What was inspected

Read directly, not guessed: `README.md`, `docker-compose.yml`,
`backend/app/config.py`, `backend/app/main.py`, `backend/app/cli.py`,
`backend/requirements.txt` / `requirements-dev.txt`,
`backend/pyproject.toml`, `backend/alembic.ini` / `alembic/env.py`,
`frontend/package.json`, `frontend/vite.config.ts`, `frontend/index.html`,
`.env.example`, `.gitignore`, `backend/app/db/session.py`,
`docs/operations/chicago-ingestion.md`, `docs/architecture.md`,
`docs/rollup-design.md`, `docs/reconciliation-transactions.md`.

Key **CONFIRMED** facts that shape every recommendation below:

- No `Dockerfile`, `Procfile`, or any platform-specific deploy config
  exists anywhere in the repo yet.
- Backend: Python (target `py39` per `pyproject.toml`'s black/ruff
  config; locally run under Python 3.9.6), FastAPI `>=0.115,<0.116`,
  `uvicorn[standard] >=0.34,<0.35`, SQLAlchemy `>=2.0,<2.1`,
  Alembic `>=1.13,<2`, `psycopg[binary] >=3.2,<4`,
  `geoalchemy2 >=0.15,<0.16`, `httpx >=0.27,<0.28`.
- `backend/app/main.py` never runs migrations, ingestion, or the rollup
  refresh at startup — the only things it does are mount routers and
  define `GET /health` (returns a static `{"status": "ok"}`; **does
  not touch the database**).
- CORS (`app/main.py`) is already implemented correctly for a
  no-wildcard staging deploy: `allow_origins=settings.cors_origins`
  (an exact-match list from `BACKEND_CORS_ORIGINS`), `allow_credentials
  =False`, `allow_methods=["GET"]`. **No code change is needed here —
  only the right environment variable value in staging** (§7).
- All configuration is read via `pydantic-settings` from real
  environment variables (`backend/app/config.py`); `env_file="../.env"`
  is a local-dev convenience only — real OS/platform environment
  variables always take precedence, so staging needs no `.env` file at
  all, only real env vars set by the host.
- `alembic/env.py` resolves the DB URL from `os.environ["DATABASE_URL"]`
  first, falling back to app settings — migrations can be pointed at
  any database purely via environment variable, no code change needed.
- Every ingestion/rollup operation is an explicit `python -m app.cli
  <command>` invocation (`backend/app/cli.py`) — **nothing runs
  automatically**, ever, by design (`docs/operations/chicago-ingestion.md`:
  "Nothing here runs automatically. `app.main`... never imports or
  triggers any of this.").
- `full-reconcile-chicago --confirm` (unbounded) took a **measured
  3h44m** against the real ~8.6M-row Chicago dataset in one earlier run
  (`docs/reconciliation-transactions.md`), now checkpointed every 5
  batches (~10,000 records) by default so a crash loses at most a few
  minutes of progress — but the *documented, safe* recovery is still
  "re-run the whole command from scratch," not a resume. This is the
  single most important constraint on hosting choice (§3).
- `docs/reconciliation-transactions.md` also documents a real, measured
  **57m48s** unbatched single-transaction migration backfill against
  the full `offenses` table (migration `0009`), and a follow-up
  observation that `GET /api/status` stayed slow (21-34s) until a
  manual `VACUUM ANALYZE offenses` (38s) was run — **any migration that
  bulk-rewrites a large table should be followed by an explicit
  `VACUUM ANALYZE`** on staging too.
- `docs/operations/chicago-ingestion.md` §6 explicitly warns: **DDL
  against `incidents`/`offenses` and an in-progress unbounded full
  reconciliation must never overlap** — a migration will block for the
  reconciliation's entire duration, and a model change deployed while
  an old-schema reconciliation is still running will 500 on the new
  column. This directly drives the "explicit, not automatic, migration
  step" recommendation in §8.
- `docker-compose.yml` runs `postgis/postgis:16-3.4` (**Postgres 16 +
  PostGIS 3.4**) with `shm_size: 1gb` and `shared_buffers=1GB` —
  comments in that file record a **real, confirmed failure**
  ("could not resize shared memory segment... No space left on
  device") against Postgres's *default* 64MB `/dev/shm` and 128MB
  `shared_buffers` once the real ~8.6M-row dataset was loaded. **Staging
  Postgres needs comparable memory headroom, not a bottom-tier
  instance**, or the exact same class of failure will recur on large
  aggregate queries and `VACUUM ANALYZE`.
- `docs/rollup-design.md`: full DB was **17 GB** before the
  `incident_grid_rollup` materialized view existed, +191 MB (~1.1%)
  after (1,038,630 rows). The task states current local size **~19 GB**
  — consistent with continued growth since that measurement (more
  `raw_source_record_versions` entries, ongoing incremental ingestion).
  Refresh is `REFRESH MATERIALIZED VIEW CONCURRENTLY`, explicit-only
  (`python -m app.cli refresh-rollup`), auto-triggered after a
  *successful* `incremental-ingest-chicago` or `full-reconcile-chicago`
  run (best-effort; a refresh failure never fails the ingestion run).
- `backend/app/db/session.py`: one synchronous SQLAlchemy engine per
  process (`create_engine(url, pool_pre_ping=True, future=True)`), no
  explicit `pool_size`/`max_overflow` — so each backend **process**
  uses SQLAlchemy's defaults (pool_size=5 + max_overflow=10 = up to 15
  connections per process). This matters for sizing worker count
  against whatever staging Postgres's max-connections limit is (§2, §12).
- Frontend: React 18 + TypeScript + Vite 5, `npm run build` = `tsc &&
  vite build` (static output). `VITE_API_BASE_URL` (from
  `.env.example`) is read via `import.meta.env.VITE_API_BASE_URL` in
  `frontend/src/api/client.ts` — **Vite inlines this at *build* time**,
  not at container-start time, so it must be set correctly in the CI/
  build environment *before* `npm run build` runs, not just as a
  runtime env var on whatever serves the static files.
- `frontend/index.html` / `frontend/src/App.tsx`: a single route (`/`),
  no `react-router` or any client-side router in `package.json` — all
  state lives in query-string params (`useFilterState.ts`). **No SPA
  history-mode fallback routing is required** (nothing to fall back
  from); a plain static file host serving `index.html` at `/` is
  sufficient.
- No secrets are committed: `git grep` for AWS-key-shaped strings,
  PEM private-key headers, and embedded `user:pass@host` Postgres URLs
  found only the well-known, publicly-documented local-dev default in
  `backend/app/config.py` (`crime_tracker:crime_tracker@localhost:5433`)
  — not a real secret. `.gitignore` excludes `.env`/`.env.*` except
  `.env.example`. ✅

---

## 2. Deployment requirements

### Backend

- **Runtime:** Python 3.9+ (repo targets `py39`; a newer 3.11/3.12
  runtime would also work since nothing in `requirements.txt` pins an
  upper Python bound, but matching what's already tested — 3.9 — is the
  lower-risk choice for a first staging deploy).
- **Process:** `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
  (honor `BACKEND_HOST`/`BACKEND_PORT` from `.env.example`, or the
  host's injected `$PORT`) — **without** `--reload` (that's a
  dev-only flag used in the README's local instructions; it adds
  file-watching overhead and has no place in a deployed process). A
  single worker is enough for staging's expected traffic; if more are
  ever added, connection-pool sizing (§2 DB, §12) must scale with it.
  `gunicorn -k uvicorn.workers.UvicornWorker` is an option if the host
  expects a Gunicorn-shaped process manager, but isn't required —
  plain `uvicorn` is simpler and this app has no need yet for
  Gunicorn's worker-management features.
- **Env vars:** see §6.
- **Health endpoint:** `GET /health` exists today and returns
  `{"status": "ok"}` unconditionally — it does **not** check DB
  connectivity. Good enough as a process-liveness probe for a
  deploy-promotion gate; **not** sufficient on its own as proof the API
  can actually serve real requests (staging smoke tests, §8/§10, must
  also hit a DB-touching endpoint like `/api/status`).
- **CORS:** already correctly implemented (§1) — just needs
  `BACKEND_CORS_ORIGINS` set to the real staging frontend origin (§7).
- **Persistent network access to Postgres:** required continuously (not
  just at startup) — every request that isn't `/health` touches the DB.
- **Memory/CPU:** the API's own per-request query load is modest
  (existing perf work — `docs/performance-validation.md`,
  `docs/rollup-design.md` — got most hot paths under ~100ms via
  indexes/rollup), so the *backend process* itself doesn't need much
  (512MB-1GB RAM is plenty for staging traffic). The **database**, not
  the API process, is what needs real memory headroom (§2 DB, above).
- **Command execution for migrations/ingestion:** the host must support
  running one-off commands (`alembic upgrade head`,
  `python -m app.cli ...`) against the *same* codebase/image and the
  *same* `DATABASE_URL` as the running service, on demand, for
  durations up to several hours without being killed by a
  request-oriented timeout (§3).

### Frontend

- **Static build hosting**, HTTPS by default.
- **`VITE_API_BASE_URL`** set at *build* time (§1) to the staging
  backend's public HTTPS URL.
- **No SPA fallback routing needed** (§1) — a plain static file serve
  of the `dist/` output is sufficient.

### Database

- **PostgreSQL 16 + PostGIS 3.4** to match what's actually been tested
  (`docker-compose.yml`). A managed Postgres offering must support
  installing/enabling the `postgis` extension — this is a hard
  requirement, not a nice-to-have (the schema uses `geoalchemy2`
  geography columns and a GIST index for bbox queries).
- **Minimum reasonable storage:** ≥30 GB to hold the current ~19 GB
  with real headroom for growth, WAL, and index rebuilds during
  `VACUUM`/reindex — not sized exactly to today's usage.
- **Expected growth (ESTIMATE):** Chicago's dataset grows by ordinary
  daily new incidents plus occasional reclassification-driven raw
  versions (`raw_source_record_versions` is append-only per changed
  record, not per poll — `docs/architecture.md`). This is slow relative
  to the 8.6M-row base; a reasonable planning estimate is low
  single-digit GB/year, not a rate that requires re-provisioning
  storage every few months. Re-check `pg_database_size()` after the
  first month of real staging incremental ingestion rather than
  trusting this estimate indefinitely.
- **Connection requirements:** at least the backend service's own pool
  (§1: up to 15 connections per uvicorn process by SQLAlchemy defaults)
  plus headroom for an operator's interactive `psql` session and any
  one-off CLI job running concurrently (migrations, ingestion) — plan
  for a Postgres tier whose `max_connections` comfortably exceeds ~25,
  not a bare-minimum tier.
- **Backup/snapshot support:** required — this becomes the only copy of
  a dataset that's expensive to reproduce (§5) once staging has
  diverged from local (new incremental ingests staging has that local
  doesn't, or vice versa). A managed offering with automatic daily
  snapshots + point-in-time recovery is strongly preferred over a
  self-managed Postgres with no backup story.
- **Maintenance/VACUUM:** autovacuum should stay enabled (it does by
  default); per §1's migration-0009 lesson, **any future staging
  migration that bulk-rewrites `incidents` or `offenses` should be
  followed by an explicit `VACUUM ANALYZE` on that table** rather than
  waiting for autovacuum to get to it on its own schedule.

### Background/operational jobs

All four of these are **existing, real CLI commands** (§1) — staging
needs a way to *run* them against staging's `DATABASE_URL`, not to
build anything new:

- `incremental-ingest-chicago` — short-running (fetches only changed
  records since the watermark); fine as a periodic job once scheduling
  is set up later (explicitly out of scope for this phase, per the
  task).
- `refresh-rollup` — fast (~76ms-order per `docs/performance-validation.md`
  §8, though that's the rollup *query* cost, not the full `REFRESH`
  duration; either way it's not a multi-hour job). Already
  auto-triggered after successful ingestion runs; can also be run
  standalone.
- `full-reconcile-chicago --confirm` — the multi-hour (measured
  3h44m) job that most constrains hosting choice (§3). Needs to run to
  completion without a platform-imposed timeout, and its `--confirm`
  safety gate (already in the CLI) should never be bypassed by
  automation.
- `alembic upgrade head` — normally fast (seconds), but §1's migration-
  0009 precedent shows a bulk-rewrite migration can take the better
  part of an hour against the full table — must not be assumed
  instantaneous when planning a deploy window.

---

## 3. Hosting options compared

Evaluated against: PostGIS support, 20-30+ GB DB affordability, ability
to run the multi-hour reconciliation job, scheduled-job capability,
persistent disks, private networking, private-GitHub deploy, secrets
management, operational complexity, rough monthly cost, scaling path.

**ASSUMPTION — VERIFY BEFORE COMMITTING** applies to every specific
price and platform-limit number below; PaaS pricing and quota pages
change often. Treat these as directionally reliable comparisons, not
quotes.

| | **Render** | **Railway** | **Fly.io** | **VPS + managed Postgres** (e.g. DigitalOcean droplet + DO Managed Postgres, or Hetzner) |
|---|---|---|---|---|
| PostGIS support | Managed Postgres supports enabling the `postgis` extension | Postgres template generally supports common extensions incl. PostGIS | No first-party *managed* Postgres with guaranteed PostGIS — realistically means running your own `postgis/postgis` image on a Fly Machine + Fly Volume, i.e. **self-managed** Postgres on their infra | Full control — pick a managed Postgres product (e.g. DigitalOcean Managed Databases) that explicitly documents PostGIS support, or install PostGIS yourself on a VPS-hosted Postgres |
| 20-30+ GB DB affordable | Yes, on a mid/standard tier with enough RAM to avoid the shared-memory failure already seen locally (§1) | Yes, but usage-metered (compute+RAM+disk billed by consumption) — cost less predictable at this data size than a flat tier | Yes via Volumes, priced per GB; compute priced per machine-hour | Yes, often the cheapest per-GB/per-RAM of the four, since you're paying infra cost with less PaaS margin |
| Multi-hour reconciliation job | Yes — Render's Background Worker / Job primitives are plain containers, not bound by the HTTP request/response timeout that applies only to Web Services | Yes — long-running processes and `railway run` one-offs aren't request-timeout-bound | Yes, and arguably the *best* fit — Fly Machines are just VMs with no platform timeout at all | Yes, trivially — it's just a process on a box you control (screen/tmux/systemd), zero platform-imposed duration limit |
| Scheduled jobs/commands | Native Cron Jobs | Native Cron Jobs | No fully native cron UI historically — needs a scheduled Machine trigger or external cron caller | Plain `cron(1)` — simplest, most standard, zero platform lock-in |
| Persistent disks | Not needed by this app's backend service (no local-filesystem state — raw data lives in Postgres, not `data/raw/`, in production); Postgres itself is Render's own persistent managed disk | Volumes available if ever needed; Postgres has its own persistent storage | Fly Volumes, per-machine, solid | VPS local disk persistent by default; managed Postgres has its own |
| Private networking (DB not public) | Yes — private service networking within an account/region | Yes — private networking between services in a project | Yes — 6PN/WireGuard mesh, arguably the most mature private-networking story of the four | Yes — cloud VPC (DigitalOcean VPC, Hetzner private networks) between the droplet and the managed DB |
| Deploy from private GitHub repo | Native GitHub App integration, private repos supported out of the box | Native GitHub integration, private repos supported | Supported, but via `flyctl deploy` + a GitHub Actions workflow you wire yourself — no built-in "connect repo, auto-deploy" UI | Fully manual to wire (a GitHub Actions job that SSHes in and redeploys) — standard, but not "native" |
| Env var/secrets management | Native dashboard secrets + shared "Environment Groups" | Native per-service and shared variables | `fly secrets set`, per-app | Self-managed (a restricted-permission `.env` on the box, or a secrets manager you add) |
| Operational complexity | **Low** — one platform bundles compute, managed Postgres, static hosting, jobs, and cron | **Low-moderate** — similar bundle, but consumption billing needs closer cost-watching, especially around a multi-hour job | **Higher** — most powerful/flexible, but PostGIS means self-managing Postgres (backups, upgrades, tuning become your job), plus your own CI wiring | **Highest** — you own OS patching, process supervision, TLS renewal, firewall, backup verification (even with a *managed* DB add-on, the VPS/app side is still all yours) |
| Rough monthly cost (staging-sized) | **ESTIMATE:** ~$65-150 total (DB tier is the dominant cost) | **ESTIMATE:** ~$50-120, wider variance due to usage billing, plus real risk of a cost spike during a multi-hour reconciliation job pulling sustained CPU | **ESTIMATE:** ~$40-100 if self-tuned carefully, but add real time cost for the extra Postgres ops work | **ESTIMATE:** ~$30-120 — often the cheapest per unit of compute/storage, at the cost of operator time |
| Scaling path | Vertical tier bumps; straightforward | Vertical + usage auto-scales (cost scales with it) | Most flexible long-term (closest to raw infra); steepest learning curve | Most flexible of all (add read replicas, move to k8s, etc.) but requires the most hands-on work to get there |

**Do-not-optimize-for-free-tier note:** every free/hobby tier among
these four is sized for toy apps (typically ≤1GB Postgres, often with
aggressive sleep/idle behavior) — none of them can hold ~19-30 GB or
run a multi-hour job without upgrading past the free tier first. This
plan prices the tier that's actually adequate, not the cheapest one.

---

## 4. Recommendation

### Primary: **Render**

Best fit, in order of the task's own criteria:

- **Current dataset size:** Render's managed Postgres, sized to a tier
  with enough RAM (avoiding the exact shared-memory failure class
  already hit locally at default settings, §1) and enough disk for
  ~19 GB + headroom, handles this directly — no self-managed Postgres
  tuning required, unlike Fly.
- **FastAPI:** a plain Python web service — Render runs this natively
  from a `requirements.txt`-based build with a start command, no
  container image required to be hand-built (though one can be, if
  ever wanted for reproducibility).
- **PostGIS:** supported as an extension on Render's managed Postgres —
  avoids the self-managed-Postgres operational burden that Fly.io's
  path would add.
- **Multi-hour full reconciliation:** Render's Background
  Worker/Job primitives are ordinary long-running containers, not
  bound by the Web Service HTTP request/response timeout — a 3h44m
  job is not a special case here (**ASSUMPTION — VERIFY BEFORE
  COMMITTING** the exact current job-duration ceiling on whatever
  Render plan is chosen, before relying on this for the real,
  unbounded `--confirm` run).
- **Vite frontend:** Render's static site hosting builds `npm run
  build` output directly from the repo, HTTPS by default, no separate
  CDN/config needed for a project this size.
- **Low operational overhead:** one platform account covers compute,
  managed Postgres, static hosting, one-off jobs, cron, secrets, and
  private networking — the fewest moving parts of any option that
  still meets every hard requirement (PostGIS + long jobs + private
  repo deploy).

### Fallback: **Small VPS (DigitalOcean droplet) + DigitalOcean Managed Postgres**

If Render's job-duration ceiling, memory tier pricing, or PostGIS
extension policy turns out (on actual verification) to be a real
blocker, this is the fallback: a plain droplet running the FastAPI
service under systemd, pointed at a separately-provisioned DigitalOcean
Managed Postgres instance (PostGIS-enabled) over their private VPC.
Strictly more operational work (patching, process supervision, TLS via
Caddy/Let's Encrypt, deploy wiring via GitHub Actions + SSH) in
exchange for the most direct control over the long-running
reconciliation job (it's just a process on a box, with zero
platform-imposed duration limit to verify) and, generally, the least
cost per unit of RAM/disk of the options compared.

---

## 5. Database migration strategy

**Do not** copy the local Docker volume/data directory — that ties
staging to the exact local Postgres binary/OS/on-disk format and isn't
how Postgres data is meant to move between environments (and is
explicitly out of scope per the task).

### Evaluated

**A. `pg_dump`/`pg_restore` from local:**
- Fast, complete, and exact — captures the current ~19 GB / 8.6M-row
  state including full `raw_source_record_versions` provenance and
  `ingestion_runs`/`rollup_refresh_run` history byte-for-byte.
- The `incident_grid_rollup` materialized view's *stored data* is
  included in a normal `pg_dump` (materialized views aren't
  dump-excluded by default), so staging starts with a populated,
  immediately `REFRESH ... CONCURRENTLY`-able rollup — no separate
  rebuild step required before the first refresh.
- Network transfer (**ESTIMATE**): the raw-JSON-heavy
  `raw_source_record_versions` table should compress well under
  `pg_dump -Fc` (custom format, built-in compression); a rough
  estimate is the ~19 GB shrinking to something in the single-digit-GB
  range, but this has not been measured against the real data — run
  `pg_dump -Fc` locally first and check the actual output size before
  planning a transfer window around a guessed number.
- Downside: doesn't independently prove the ingestion *pipeline* itself
  works from the new environment (network egress to Socrata, adapter
  compatibility, credentials) — it only proves Postgres restore works.

**B. Run the Chicago ingestion pipeline from scratch in staging:**
- Proves the real production pipeline works end-to-end in the new
  environment — a genuine, valuable smoke test of connectivity/config.
- Costs a measured 3h44m of real load against Chicago's public API
  (§1) for what is, for staging purposes, just "get some realistic
  data into a new box" — an expensive, slow way to answer that
  specific question, and not something you want to repeat every time
  staging needs to be refreshed.
- Produces a *different* provenance history than local (fresh
  `ingestion_runs`, no accumulated raw-version history reflecting real
  past reclassifications) — less faithful to what local actually
  represents, if the goal is a realistic staging replica.

**C. Hybrid (recommended):**
1. `pg_dump -Fc` the local database; `pg_restore` into staging's
   Postgres (Alembic-migrated to the same schema version first — see
   §8's ordering). This is the primary population method: fast,
   complete, preserves real provenance/history, and doesn't put
   avoidable repeated load on Chicago's public API.
2. Run `smoke-ingest-chicago --limit 500` in staging immediately after
   — a small, bounded, always-safe (`is_complete=False`, never triggers
   deletion inference), idempotent real-network call that proves the
   adapter, schema validation, and outbound connectivity all work from
   the new environment, without paying the 3h44m cost.
3. Run `incremental-ingest-chicago` in staging (the restored
   `Source.incremental_watermark` from the dump makes this
   immediately usable) to catch staging up on anything new in Chicago's
   dataset since the local dump was taken. This is normally fast (only
   fetches changed records).
4. Run `refresh-rollup` once staging is caught up.
5. Reserve an actual unbounded `full-reconcile-chicago --confirm` run
   for later, as a genuine periodic operational exercise (and to prove
   the chosen host really can sustain a multi-hour job) — not as the
   initial bring-up method.

This is the **safest** approach: it gets staging populated quickly and
faithfully, still exercises the real pipeline (step 2-3) before
trusting it, and defers the expensive full-census run to when it's
actually needed rather than making it a prerequisite for having a
usable staging environment at all.

---

## 6. Staging configuration (environment variables)

Only variables that actually exist in the code today
(`backend/app/config.py`, `.env.example`,
`frontend/src/api/client.ts`) — nothing invented.

### Backend — non-secret

| Variable | Staging value (example) |
|---|---|
| `BACKEND_HOST` | `0.0.0.0` |
| `BACKEND_PORT` | host-injected `$PORT`, or `8000` |
| `BACKEND_CORS_ORIGINS` | the exact staging frontend origin(s) — see §7 |
| `REMOVAL_CONFIRMATION_RUNS` | `2` (matches the documented default; only needed if overriding it) |

### Backend — secret

| Variable | Notes |
|---|---|
| `DATABASE_URL` | Full connection string incl. credentials, pointed at staging Postgres. Store only in the host's secret manager. |
| `CHICAGO_APP_TOKEN` | Optional (anonymous access works), but tied to a developer account — treat as a secret, never commit. |

`TEST_DATABASE_URL` is **not** a staging runtime variable — it's only
read by the test suite (`backend/tests/conftest.py`), and only matters
if CI runs backend tests against a database reachable from wherever CI
executes; it should not be set on the deployed staging service itself.

### Frontend — build-time, non-secret

| Variable | Staging value (example) |
|---|---|
| `VITE_API_BASE_URL` | `https://api-staging.getcrimesignal.com` — must be set correctly **before** `npm run build` runs (§1); not a secret (it ends up visible in the built JS bundle regardless — it's just a public API URL). |

No frontend variable is a secret; there is nothing else to set.

---

## 7. CORS / domain design

**Current staging URLs** (custom domain already decided — this
supersedes the platform-subdomain placeholder this section previously
sketched; DNS/domain setup itself is outside this document's scope):

- Frontend: `https://staging.getcrimesignal.com`
- Backend: `https://api-staging.getcrimesignal.com`

**Exact CORS behavior needed** (no code change — configuration only,
per §1):

- `BACKEND_CORS_ORIGINS` must be set to the **exact scheme+host** of
  the staging frontend, i.e. `https://staging.getcrimesignal.com`
  — comma-separated if more than one origin needs access (e.g. a
  preview-branch subdomain), matching the existing comma-separated
  format already used for local dev (`http://localhost:5173,http://
  localhost:5174`).
- **No wildcard (`*`) origin, ever** — `app/main.py`'s
  `CORSMiddleware` already takes an explicit list from this variable;
  simply never put `*` in it.
- `allow_credentials=False` (already hardcoded, correct — the frontend
  only ever issues plain unauthenticated `fetch()` GET requests, no
  cookies) means the origin allowlist doesn't need to consider
  credentialed-request edge cases at all.
- `allow_methods=["GET"]` (already hardcoded, correct — the API is
  read-only) needs no change for staging.

---

## 8. Deployment workflow

Two distinct sequences — conflating them would mean re-running a
19 GB database restore on every code push, which is neither intended
nor safe.

### Day-0 bring-up (once)

```
provision staging Postgres (PostGIS-enabled, sized per §2)
  -> alembic upgrade head (against an EMPTY staging DB)
  -> pg_dump (local) / pg_restore (staging)   [see §5 hybrid steps 1]
  -> smoke-ingest-chicago --limit 500          [§5 step 2]
  -> incremental-ingest-chicago                [§5 step 3]
  -> refresh-rollup                            [§5 step 4]
  -> deploy backend service
  -> health check (/health, then /api/status)
  -> build + deploy frontend (VITE_API_BASE_URL set to staging backend)
  -> smoke tests (below)
```

Running `alembic upgrade head` against an **empty** database first
(before restoring data) means migration `0009`'s real 57m48s backfill
cost (§1) never recurs on staging — an empty `offenses` table has
nothing to backfill. The `pg_restore` that follows brings in
already-migrated-shape data.

### Ongoing deploy (every push to the staging branch)

```
GitHub push (staging branch)
  -> CI: backend pytest + ruff + black, frontend npm test + lint + format
  -> backend deploy (new revision built, not yet receiving traffic)
  -> migration (explicit release-phase command -- see below)
  -> health check (/health, then /api/status) gates promotion
  -> frontend build (VITE_API_BASE_URL=<staging backend URL>) + deploy
  -> smoke tests (below)
```

Database population and rollup refresh are **not** part of this
routine sequence — they're Day-0 (and later, deliberate/periodic)
operations, invoked manually via §9's commands.

### Should migrations run automatically or via an explicit release command?

**Explicit, not automatic — this matches the codebase's own existing
philosophy** (§1: nothing runs automatically today; ingestion is
always a deliberate CLI invocation) and is required by
`docs/operations/chicago-ingestion.md` §6's own warning: an
in-progress unbounded full reconciliation must never race a schema
migration. An automatic "migrate on every boot" step has no way to
know whether a multi-hour reconciliation job happens to be running
against the same database right now. A separate, explicit
release-phase step (run by a human or a gated CI step, checked against
"is a full reconciliation currently running?" via the `ingestion_runs`
table before proceeding) is the safe behavior the task asks for.

### Smoke tests (run after any deploy)

- `GET /health` → `{"status": "ok"}`
- `GET /api/status` → 200, confirms real DB connectivity (unlike
  `/health` alone, §2)
- `GET /api/summary`, `GET /api/categories` → 200, sanity-check real
  data is present and queryable
- Load the deployed frontend URL, confirm the dashboard fetches
  successfully from the staging backend (end-to-end proof the CORS
  configuration in §7 is actually correct, not just theoretically
  correct)

---

## 9. Operational commands (staging)

All commands below are **existing, real CLI commands** (§1) — nothing
new. Run them via whatever the chosen host calls its "run a one-off
command against this service" feature (Render Job/Shell, `railway run`,
`fly ssh console` / `fly machine run`, or plain SSH on a VPS), with
`DATABASE_URL` (and `CHICAGO_APP_TOKEN` if set) pointed at staging.
**Nothing here is scheduled yet** — that's explicitly out of scope for
this phase.

```bash
# Migrations
alembic upgrade head

# One-time setup
python -m app.cli bootstrap-chicago
python -m app.cli refresh-iucr

# Initial/partial population (bounded, safe to re-run)
python -m app.cli full-reconcile-chicago --limit 100000

# Small connectivity/adapter smoke test (always safe, repeatable)
python -m app.cli smoke-ingest-chicago --limit 500

# Routine incremental catch-up
python -m app.cli incremental-ingest-chicago

# Rollup refresh (also auto-triggered after a successful ingestion run above)
python -m app.cli refresh-rollup

# Full reconciliation (multi-hour; requires --confirm; see §5/§8 for when to run this)
python -m app.cli full-reconcile-chicago --confirm
```

---

## 10. Observability (minimum, lightweight)

- **Application logs:** rely on the host's built-in log capture of
  stdout/stderr (uvicorn logs there by default) — no code change
  needed. Ensure the host's log retention window is enabled/sufficient
  for staging debugging.
- **Ingestion-run logs:** already durable and structured, with **zero
  new code required** — the `ingestion_runs` and `rollup_refresh_run`
  tables (existing models) record status, counts, timing, and
  `error_message` for every run, queryable directly via `psql`
  independent of ephemeral platform log retention. Treat these tables
  as the primary source of truth; the CLI's own `logger.info`/
  `logger.error` console output (captured by whatever ran the job) is
  the secondary, ephemeral trace.
- **DB health:** the managed Postgres provider's own dashboard
  (CPU/memory/disk/connections) — enable whatever alerting the host
  offers natively rather than building anything custom.
- **API health endpoint:** wire the host's health-check feature to
  `GET /health` for deploy-promotion gating, but remember it doesn't
  check DB connectivity (§2) — the smoke tests (§8) are what actually
  validate the DB path after a deploy.
- **Error visibility:** no APM/error-tracking integration exists in the
  codebase today. Minimum viable for staging: FastAPI's default error
  responses plus platform log capture of any uncaught-exception
  traceback (printed to stderr, captured by the host). A dedicated
  error tracker (e.g. Sentry) would be a reasonable future addition,
  not a requirement to begin staging.
- **Disk/storage monitoring:** enable the managed Postgres provider's
  disk-usage alert if it offers one (most do) — important given the
  dataset's size and growth trajectory (§2).
- **Ingestion failure alerting:** no notification integration exists in
  the app today (the CLI just logs and exits non-zero). Minimum viable:
  configure whatever job runner executes these commands (§9) to notify
  on non-zero exit — every one of the four hosting candidates' native
  job/cron features, and `cron` itself via `MAILTO`, support this
  without any app code change.

---

## 11. Security

| Check | Status |
|---|---|
| No secrets in repo | ✅ **Confirmed** via `git grep` for key/token/PEM/embedded-credential patterns across tracked files (§1) — none found beyond the well-known local-dev default. |
| Production/staging CORS (no wildcard) | Already correctly implemented in code (§1, §7) — staging just needs the right `BACKEND_CORS_ORIGINS` value. |
| Database not publicly exposed if avoidable | **Recommended:** place staging Postgres on the chosen host's private network, with no public connection string; if a platform can't fully isolate it, restrict by IP allowlist (backend's egress + operator IPs only) and require `sslmode=require` at minimum. |
| HTTPS | All four hosting candidates in §3 provide automatic TLS for both static frontend and backend service — require HTTPS-only for both staging URLs; `VITE_API_BASE_URL` must be the `https://` URL. |
| Least-privilege DB user | **Gap today:** the app connects as a single role with full rights over its own schema (fine for local dev). **Recommended hardening for staging:** either accept the single-role model for now (reasonable to not block Day-0 bring-up on this), or split into a narrower "runtime" role (DML only, no DDL) used by the API process and a separate "migrator" role (DDL) used only for the explicit migration step. Worth doing before staging is used for anything beyond internal testing. |
| GitHub private repo access | Repo is already private (per the task). Grant the hosting platform's GitHub integration access to **only this repository**, not org-wide. |
| Secret/environment management | Use the host's native encrypted secret store (§3) for `DATABASE_URL`/`CHICAGO_APP_TOKEN` — never in a committed file, never in `render.yaml`/equivalent. |
| Debug mode disabled | ✅ **Confirmed** — `FastAPI(...)` in `app/main.py` is constructed with no `debug=True` anywhere, so it's already off by default. Ensure the staging start command omits uvicorn's `--reload` (a dev-only flag used in the README's local instructions, §2). |

---

## 12. Cost estimate (Render, recommended option)

**ASSUMPTION — VERIFY BEFORE COMMITTING** on every figure; re-check
current pricing before provisioning anything.

| Item | Estimate (monthly) | Assumptions |
|---|---|---|
| Backend web service (API) | $7–25 | Low staging traffic; 512MB-1GB RAM tier; single worker |
| Background job/worker compute (ingestion runs) | $5–15 | Amortized — full reconciliation run only occasionally (e.g. monthly), incremental ingest is short; billed by actual job duration |
| Managed Postgres (PostGIS-enabled) | $50–100 | Sized for ≥30GB disk + enough RAM (2-4GB) to avoid the shared-memory failure class already seen locally (§1); this is the dominant line item |
| Frontend static hosting | $0–5 | Small SPA build, low traffic |
| Scheduled jobs | $0 | None scheduled yet, per task scope |
| **Total** | **≈ $65–150/month** | Staging-only traffic, no HA/multi-region, no autoscaling, full reconciliation run infrequently |

---

## 13. Deliverables

- Created: `docs/deployment/staging-plan.md` (this document).
- No platform-specific deployment config (`Dockerfile`, `render.yaml`,
  `fly.toml`, etc.) was created — per the task, nothing beyond what's
  needed purely to illustrate the recommendation, and no illustration
  required actual committed config to make its point.
- README: not changed as part of this phase — the existing README's
  Roadmap section already reflects "Map & dashboard (V1)" as the
  current *done* phase; a future PR that actually begins staging
  deployment is the more appropriate place to add a top-level "Staging"
  pointer/link, once there's a real URL to link to.
