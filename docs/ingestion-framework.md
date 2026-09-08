# Ingestion Framework

**Chicago is the first city with a real, production `SourceAdapter`**
(`app/adapters/chicago.py`) exercised against Chicago's live API and
ingested into the application database — see
[`sources/chicago-ingestion-design.md`](./sources/chicago-ingestion-design.md)
and [`operations/chicago-ingestion.md`](./operations/chicago-ingestion.md).
Denver production ingestion remains intentionally disabled pending
resolution of the commercial-reuse/licensing question documented in
[`sources/denver.md`](./sources/denver.md) §5 and
[`sources/denver-ingestion-design.md`](./sources/denver-ingestion-design.md)
§10 — nothing in this phase changes that status.

This document explains the pieces this framework is built from and
how they fit together, generically across any source. For the
research and reasoning behind Chicago's specific design choices, see
[`sources/chicago-ingestion-design.md`](./sources/chicago-ingestion-design.md);
for Denver's (not yet built) case, see
[`sources/denver-ingestion-design.md`](./sources/denver-ingestion-design.md).

## Why this shape

Every source this platform will ever integrate shares the same hard
problem: **how do you know what changed** in a dataset that has no
per-row "last updated" field and that its publisher can silently add
to, edit, or remove from at any time? Denver's own data confirms this
is a real publisher behavior, not a hypothetical (see
`sources/denver.md` §4). The entities below exist specifically to
answer that question generically, once, rather than reinventing it per
city.

## External sources

`sources` — one row per external data source (e.g., a city's crime
open-data catalog). Holds identity and a few facts discovered during
source research (`timezone_convention`, publisher, URL) — never
source-specific *behavior*. Behavior belongs to a `SourceAdapter` (see
below), so adding a new city never means changing this table's shape.

## Ingestion runs

`ingestion_runs` — one row per attempted synchronization against a
source. The field that matters most is `is_complete`: it is **only**
true when a run fetched the source's entire current dataset
successfully, end-to-end. Everything downstream that infers a record
was *removed* by the source depends on this flag, because a run that
merely failed or was cut short must never be mistaken for evidence
that something is gone — Denver's own data showed removed-looking
gaps are exactly as likely to be an incomplete pull as a real
deletion, and there is no way to tell the two apart from outside
except by trusting whether the run itself succeeded.

`status` is a richer, human-facing summary (`running`, `completed`,
`partial`, `failed`); reconciliation logic itself only ever branches
on `is_complete` — and, as of the mass-disappearance safeguard below,
on `deletion_evidence_allowed`.

## Complete vs. trusted for deletion inference

`is_complete` answers one question: did *our* fetch succeed
end-to-end? It does not answer a second, distinct question: is the
resulting snapshot *plausible* enough, compared to the source's own
prior trusted history, to actually act on? A real, publicly-documented
incident proved these are not the same thing — on February 4, 2016, a
bug in Chicago's own publishing pipeline reduced their entire crimes
dataset to 722 rows, and a client fetching that morning would have
received a **fully successful, complete response** reflecting that
near-empty state (see
[`sources/chicago.md`](./sources/chicago.md#5-deletion-behavior)). A
run can therefore be:

- `is_complete = true` (the fetch itself succeeded), **and**
- technically successful by every transport/parse/schema measure,
- but still **not trusted for deletion evidence**, because the
  snapshot it fetched doesn't look plausible next to what we last
  trusted.

`IngestionRun.deletion_evidence_allowed` is the field reconciliation
actually branches on for the "missing record" step — mirroring how
`is_complete`, not `status`, is what the state machine trusts
elsewhere. It is only ever `true` when `is_complete` is `true` **and**
the run passed the plausibility check below (or was manually
overridden). `IngestionRun.plausibility_status`
(`not_evaluated` / `plausible` / `suspicious` / `overridden_trusted`)
and `blocking_reason` (see the reason codes below) record why, as
structured state rather than a free-text message.

**The plausibility check** (`app/services/plausibility.py`), run only
when `is_complete`, compares the run's fetched record count against
`Source.last_trusted_active_count` — the count from the most recent
run that was itself trusted, not simply the most recently *attempted*
run. A suspicious run never becomes the new baseline, so a string of
suspicious runs cannot gradually drag the baseline down on their own —
only a plausible (or manually overridden) run ever moves it. Checks,
in order:

1. `blocking_reason = schema_validation_failed` — if the caller
   reports schema validation failed for this fetch (see "Schema
   drift" concerns in `sources/*-ingestion-design.md`), block
   regardless of counts.
2. `blocking_reason = manual_safety_hold` — if
   `Source.manual_deletion_hold` is set, block regardless of counts.
   An operator sets this proactively (see "Manual override" below);
   it is not derived from any automatic check.
3. If `Source.mass_disappearance_protection_enabled` is `false`,
   skip everything else and pass — an explicit per-source opt-out.
4. `blocking_reason = source_count_mismatch` — if the caller supplies
   an independently-reported total (e.g. a separate count query) that
   disagrees with how many records were actually fetched, block.
5. `blocking_reason = record_count_below_absolute_floor` — if
   `Source.min_expected_records` is set and the fetched count is
   below it, block. This doesn't need a prior baseline to be
   meaningful, so it's checked even on a source's very first run.
6. If there is no trusted baseline yet, pass — nothing is implausible
   about a first observation; it becomes the baseline going forward.
7. `blocking_reason = record_count_drop_exceeds_threshold` — if
   `Source.max_record_count_drop_percent` is set and the fetched
   count dropped by more than that percentage versus the trusted
   baseline, block.

None of these thresholds are hard-coded for any specific source — see
`sources/chicago-ingestion-design.md` §5 for why Chicago's own
threshold needs real tuning against its actual volume variance, not a
value invented in the abstract.

**When a run is suspicious:** it is recorded, not discarded — its
diagnostics (`baseline_count_at_run`, `percent_change_from_baseline`,
`blocking_reason`) are preserved for later investigation. Records that
*were* actually fetched in that run still go through the normal
new/unchanged/changed handling (that data is trustworthy for what it
did fetch); only the "what's missing, and is it removed" inference is
withheld. No `source_record` is marked missing or removed because of
a suspicious run, and the source's trusted baseline does not move.

**Manual override:** `app/repositories/sources.py`'s
`promote_run_to_trusted_baseline` lets an operator deliberately
promote a specific run — even one flagged suspicious — to become the
new trusted baseline, for exactly the case where a large drop turns
out to be a real, legitimate deletion at the source rather than a
publisher-side bug. This is a repository-level function, not
automatic and not exposed through any UI — calling it is meant to be a
deliberate, out-of-band action taken after investigating a specific
run, the same way you'd expect a human to intervene after Chicago's
2016 incident rather than an algorithm quietly deciding on its own.
It affects *future* reconciliation only (the next full run compares
against the newly-promoted baseline); it does not retroactively
reprocess the promoted run's own missing-evidence. `set_manual_deletion_hold`
similarly lets an operator proactively pause deletion evidence for a
source, independent of what the counts say, until explicitly cleared.

## Source records

`source_records` — one row per record the source has ever published,
keyed generically by `(source_id, external_record_id)` with a unique
constraint. `external_record_id` is deliberately generic — it is *not*
assumed to look like Denver's `OFFENSE_ID` (see
`sources/denver-ingestion-design.md` §1 for why Denver's own identifier
is empirically, not contractually, unique — the same caution applies
to any future source's identifier).

Each source record tracks:

- `source_active` — is it currently part of the source's live dataset?
- `first_seen_at` / `last_seen_at` — when we first and most recently
  observed it.
- `current_fingerprint` — the hash of its most recently observed raw
  fields (see Fingerprinting below).
- `consecutive_missing_runs` / `removal_confidence` — the evidence
  trail behind the deletion-handling policy (see below).
- `source_removed_at` — see "Reappearance," below — this is a
  historical marker, not simply "is it gone right now" (that's what
  `source_active` is for).

## Raw versions

`raw_source_record_versions` — **append-only at the application
level.** A new row is written only when a source record's fingerprint
differs from its previously stored version. An unchanged record
accumulates exactly one version, ever, no matter how many times it's
re-fetched; a record the source revises three times accumulates three
versions. This is what "immutable raw snapshot" means for a source
that mutates records without telling you which ones changed (see
[`architecture.md`](./architecture.md) "Raw versioning for sources
that mutate records without a per-row timestamp").

Enforcement is by convention, not a database trigger: the repository
module for this table (`app/repositories/raw_versions.py`) exposes
only a `create` function — no update or delete — so the only way
application code can violate the append-only rule is to bypass the
repository layer entirely.

## Incidents vs. offenses

Denver's own data (a full census, not a sample) showed that 94.2% of
incidents have exactly one offense, but the rest range up to 8 — a
NIBRS-style artifact (report every offense in an incident, not just
the most serious one) that shows up plainly in real numbers, not just
as a theoretical edge case. `incidents` and `offenses` are therefore
separate tables: one incident has many offenses, each offense traces
back to the source record/version that produced it via
`source_record_id`.

`incidents.location_precision` exists so a stored coordinate is never
silently presented as more precise than it is. Denver's own data shows
published coordinates can correspond to a block or intersection rather
than the true incident location, and that this happens at a
meaningfully higher rate for person-crime categories (see
`sources/denver.md` §3). The enum (`exact` / `approximate` / `block` /
`intersection` / `suppressed` / `unknown`) exists so a future adapter
can be honest about what it actually knows, per source and per record
— never defaulting to "exact."

The normalized crime taxonomy does not exist yet (see
[`product.md`](./product.md)), so `offenses.normalized_category` /
`normalized_subcategory` stay nullable; `source_category` /
`source_subcategory` preserve whatever the source called it.

## Fingerprinting

`app/fingerprint.py` computes a SHA-256 hash over a **canonical field
set the caller chooses** — not the entire raw payload. This is how
volatile fetch metadata (e.g., when a record was observed) stays out
of the comparison: it's simply not one of the fields passed in.
Canonicalization always emits every requested field, defaulting a
missing one to `null`, so "the source stopped sending this field" and
"the source sends it as null" don't masquerade as a data change.
Comparison is by exact JSON-serialized value — `1` and `"1"` are not
considered the same value; a real adapter is responsible for
consistent typing.

## Reconciliation

`app/services/reconciliation.py` implements the state machine, run
against any `SourceAdapter`. It processes records in bounded batches
(`DEFAULT_BATCH_SIZE = 2000`, tunable per call) rather than one record
at a time: each batch does one `SELECT` to fetch existing source
records (as lightweight rows, not full ORM objects) and bulk
`INSERT`/`UPDATE` statements for new/changed/unchanged rows, instead of
a round trip per record. This was a direct response to a measured
problem — a naive per-record ORM implementation ran at ~8ms/record,
which projects to ~19 hours across Chicago's ~8.6M-row dataset (see
`sources/chicago-ingestion-design.md` §7 for the real before/after
benchmark numbers). `app/services/ingestion.py`'s incident/offense sync
follows the same batched-bulk shape, streaming one batch at a time so a
full fetch never holds the entire dataset in memory at once. The state
machine's semantics (below) are unchanged by this — batching is a
performance detail, not a behavior change.

| Situation | Result |
|---|---|
| External ID never seen before | Create the source record, write its first raw version, mark active. |
| External ID seen before, fingerprint unchanged | Update `last_seen_at` only — no new version. |
| External ID seen before, fingerprint differs | Update `current_fingerprint`, append a new raw version, update `last_seen_at`. |
| Previously-active record absent from a **complete and trusted** run | Increment its missing-evidence counter; do **not** deactivate it yet. |
| Missing-evidence counter reaches the configured threshold | `source_active = false`, `source_removed_at` set. |
| A confirmed-removed (or still-missing-but-active) record reappears | Reactivate (`source_active = true`), reset missing-evidence to zero, leave `source_removed_at` as a historical marker (not cleared), and append a new version only if the data actually changed since it was last seen. |
| Record absent from a **partial/failed** run | Nothing — no missing-evidence is accumulated at all. |
| Record absent from a **complete but suspicious** run | Also nothing — see "Complete vs. trusted for deletion inference" above. Preserved for symmetry: incompleteness and implausibility are different reasons for the same outcome. |

Any *positive* sighting of a record (it was fetched, whether or not
the overall run was complete) is trusted immediately and resets its
missing-evidence counter — only the *negative* inference ("it wasn't
in this fetch, so maybe it's gone") is gated on the run having been
both complete **and** trusted (`deletion_evidence_allowed`).

## Deletion / removal handling

We never hard-delete a `source_record` (or its raw versions) just
because the source stopped publishing it — the record is still real
provenance for something that was once true, and this platform's
architecture already commits to preserving that (see
[`architecture.md`](./architecture.md) "Why Raw and Normalized Data
Stay Separate"). Instead:

- `consecutive_missing_runs` counts how many **consecutive complete
  and trusted** reconciliation runs missed the record — a suspicious
  run (see "Complete vs. trusted for deletion inference" above)
  contributes nothing here, same as a partial run.
- `removal_confidence` mirrors that count in human-readable form:
  `none` → `pending` (missed once) → `likely` (missed twice, still
  below the configured threshold) → `confirmed` (at or past the
  threshold).
- The threshold is configurable per source
  (`Source.removal_confirmation_runs`, falling back to
  `Settings.removal_confirmation_runs`, default `2` — see
  `.env.example`), not hard-coded to any policy Denver-specific
  reasoning happened to suggest in Phase 2. A different source, with
  a different publish cadence or reliability, might reasonably need a
  different threshold.
- Once confirmed, `source_active` flips to `false` and
  `source_removed_at` is set. If the record ever reappears, it's
  reactivated (see the reconciliation table above) — `source_removed_at`
  is deliberately left in place as a breadcrumb of the last removal
  event rather than cleared, so "this was once confirmed removed" isn't
  silently lost.
- A partial/failed run, or a complete-but-suspicious run, contributes
  zero missing-evidence, for any record, full stop — see the
  reconciliation table above and "Complete vs. trusted for deletion
  inference" above.

## Reference/taxonomy tables (IUCR)

Some sources publish a companion reference dataset describing their
own offense-code vocabulary — Chicago's IUCR codes
(`c7ck-438e`) are the first example. `app/models/iucr_code.py`'s
`IucrCode` stores the code, both descriptions, the FBI index code, and
whether the code is currently `active`. It is deliberately **not** a
hard foreign-key target from `Offense` — an offense referencing a code
that has since gone inactive (or hasn't been refreshed recently) must
never be blocked from ingesting; the reference table exists for
lookup/display, not as a gate. `app/services/iucr.py`'s
`refresh_iucr_codes` upserts the current set
(`app/repositories/iucr_codes.py`, `ON CONFLICT DO UPDATE` on the code)
and is run as its own explicit operation
(`python -m app.cli refresh-iucr`), independent of crime-record
ingestion.

## Source adapters

`app/adapters/base.py` defines `SourceAdapter`: fetch schema, fetch
current records, extract a record's external id, canonicalize one raw
record, and (optionally, since the taxonomy doesn't exist yet)
normalize a raw record into `IncidentDraft`/`OffenseDraft`. Fetching
and canonicalizing are separate on purpose — canonicalization and
normalization are then trivially unit-testable against fixed inputs,
with no network involved.

`app/adapters/chicago.py`'s `ChicagoSourceAdapter` is the first
production adapter, built against Chicago's real Socrata SODA API (see
`sources/chicago-ingestion-design.md`). It also adds one optional
method beyond the base contract, `validate_schema()`, which callers
(the CLI) run before every fetch and treat as a hard stop on failure —
see "Schema drift" reasoning there. No Denver adapter exists yet — see
"Future city adapters" below. `app/adapters/fixture.py` provides
`FixtureSourceAdapter`, used only by tests/development, serving a
fixed, hand-shaped list of synthetic records; it is not a real city
and is not presented as one.

## Repository / service layers

FastAPI routes never touch ORM models directly. `app/repositories/`
holds plain functions per entity (`sources.py`, `ingestion_runs.py`,
`source_records.py`, `raw_versions.py`, `incidents.py`) — no
repository *classes*, since a function per operation was enough here
without adding a framework layer nobody asked for.
`app/services/reconciliation.py` and `app/services/ingestion.py` hold
the actual business logic (the state machine, and the thin
fetch → reconcile → normalize orchestration), built on top of the
repository functions.

## Future city adapters

Adding a real city means writing one `SourceAdapter` implementation
for it — nothing else in this framework should need to change. That
adapter would need, at minimum: a way to fetch the source's current
records (bulk export, paginated API, whatever fits — see
`sources/denver-ingestion-design.md` §9 for what that investigation
looked like for Denver specifically), a stable `external_record_id`
extractor, a canonicalization function producing the fields that
should participate in fingerprinting, and — once the normalized
taxonomy exists — a `normalize_incident_offense` mapping.

**A Denver adapter is deliberately not built in this phase.** Building
one is blocked on resolving the licensing question in
`sources/denver.md` §5, not on any remaining technical unknown — Phase
2's investigation already answered or clearly characterized every
technical question a Denver adapter would need (identifier stability,
timezone handling, change-detection capability, schema/taxonomy
drift). When that's cleared, writing the adapter should be a
comparatively small, well-scoped task against this framework — Chicago's
adapter is a real, working example of what that task looks like end to
end, including the CLI commands, tests, and documentation that go with
it (see [`operations/chicago-ingestion.md`](./operations/chicago-ingestion.md)).
