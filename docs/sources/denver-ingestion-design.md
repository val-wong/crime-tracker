# Denver Ingestion — Technical Design Investigation (Phase 2)

**Status:** design document only. No production ingestion code exists.
No database is wired up. This document resolves or characterizes the
open questions Phase 1 ([`denver.md`](./denver.md) §8) flagged as
blocking a real ingestion design, and proposes (but does not build)
the flow that a future implementation phase should follow.

**Method:** findings below were obtained by querying Denver's live
ArcGIS REST API and its own layer metadata (`?f=json`), by downloading
the dataset once via Denver's official bulk CSV export endpoint for
offline aggregate analysis, and by reading Denver's own executive
orders and site content directly. The bulk CSV was analyzed locally
and then deleted; it was never committed to this repository (see
`.gitignore` and §6 below). A small, reusable research script
(`scripts/research/denver_arcgis_explore.py`) captures the
repeatable, cheap queries (metadata, distinct counts, cardinality
histogram) for anyone who wants to re-verify these findings.

Throughout, findings are labeled:

- **CONFIRMED FACT** — verified directly against Denver's own
  metadata, documentation, or a full census of the live data.
- **EMPIRICAL OBSERVATION** — measured from the data as it exists
  today; true of the current dataset, not a guarantee about the
  future or the past.
- **RECOMMENDATION** — a design choice this investigation supports,
  not yet implemented.
- **UNRESOLVED** — could not be established from available evidence.

---

## 1. `OFFENSE_ID` uniqueness and stability

**CONFIRMED FACT (full census, not a sample):** as of this
investigation, the layer contains 378,097 rows. A server-side
distinct-value count (the layer advertises
`supportsCountDistinct`/`supportsDistinct`) over the *entire* published
dataset returns exactly 378,097 distinct `OFFENSE_ID` values, 0 nulls,
and 0 empty strings. This was cross-checked two independent ways —
once via the API's own distinct-count aggregation, and once by
downloading the full dataset and counting duplicates locally — and
both agree exactly.

**EMPIRICAL OBSERVATION — why it's unique:** `OFFENSE_ID` is
constructed as `INCIDENT_ID` + `OFFENSE_CODE` + `OFFENSE_CODE_EXTENSION`.
A full census confirms that **no `INCIDENT_ID` ever logs the same
`(OFFENSE_CODE, OFFENSE_CODE_EXTENSION)` pair twice** — 0 such
collisions found among 353,891 distinct incidents. That's *why* the
construction is currently collision-free, not just an assertion that
it happens to be.

**What this is *not*:** Denver's own documentation does not state a
uniqueness guarantee for `OFFENSE_ID` anywhere we found. The only
field Esri's own layer metadata contractually treats as a unique key
is `OBJECTID` (`uniqueIdField: {"name": "OBJECTID",
"isSystemMaintained": true}`) — a platform-managed surrogate, not a
Denver business key, and one Esri documentation generally warns is not
guaranteed stable across a full data reload/truncate-and-append cycle
(a general Esri/hosted-feature-layer characteristic; not separately
confirmed as a documented policy for this specific service).

**RECOMMENDATION:** use `OFFENSE_ID` as our `source_incident_id` /
external key, since it is empirically unique across a full census and
structurally explained, but treat it as an **empirical fact we
continuously verify, not a contract**:

- Enforce a uniqueness constraint on `source_incident_id` in our own
  schema so a future collision (if Denver's code ever repeats within
  an incident) fails loudly instead of silently overwriting a row.
- Re-run the uniqueness check (`offense_id_uniqueness()` in the
  research script) as part of each full reconciliation pass (§4),
  not just once.
- Do not rely on `OBJECTID` for anything beyond a same-request
  pagination cursor — it does not carry meaning across independent
  requests over time.

---

## 2. Incident-to-offense relationship

**CONFIRMED FACT (full census):**

| Offenses per incident | Incidents | Share of incidents | Offense rows contributed |
|---|---|---|---|
| 1 | 333,286 | 94.18% | 333,286 |
| 2 | 17,605 | 4.97% | 35,210 |
| 3 | 2,503 | 0.71% | 7,509 |
| 4 | 414 | 0.12% | 1,656 |
| 5 | 64 | 0.02% | 320 |
| 6 | 18 | 0.005% | 108 |
| 8 | 1 | <0.001% | 8 |
| **Total** | **353,891** | 100% | **378,097** |

(No incident has exactly 7, or more than 8, offenses — the histogram
above is a complete accounting: it sums to exactly 353,891 incidents
and 378,097 rows, matching the independently-measured totals.)

So: **94.2% of incidents are single-offense**, and the long tail is
genuinely long-tailed — one incident currently has 8 distinct logged
offenses.

**Representative example** (from Phase 1 sampling; re-confirmed
structurally above): incident `DP2024329622` was the single incident
with the most offenses observed in this investigation window.

**Recommendation — relational model:**

Model `incidents` and `offenses` as **separate entities from V1**,
not an offense-centric flat table. Reasoning:

- The 94.2%/5.8% split is not close enough to "basically always 1:1"
  to justify pretending the relationship is flat — nearly 1 in 17
  incidents has more than one offense, and ignoring that would either
  (a) silently drop real offenses if we dedupe by incident, or (b)
  double-count incidents in any incident-level statistic (e.g. "how
  many separate incidents happened in this neighborhood this month")
  if we only ever count offense rows.
- Denver's own description ties this directly to NIBRS semantics
  (report all offenses within an incident, not just the most serious
  one — see [`denver.md`](./denver.md#6-crime-taxonomy)) — this is a
  structural property of the source, not an artifact we can normalize
  away.
- The alternative (offense-centric only, V1-simple) would still work
  for the stated V1 scope (map + filters + basic trends over
  *offenses*), and is less implementation work. The real cost shows up
  the first time we want an accurate incident count rather than an
  offense count (e.g. "how many distinct incidents" vs. "how many
  offenses" — these are different numbers for Denver, and conflating
  them would misstate crime volume).

**Tradeoff, stated plainly:** a separate `incidents`/`offenses` model
costs one extra join and one extra ingestion concern (deciding what
incident-level fields even mean — e.g. does an incident have its own
address, or only its offenses do?) in exchange for being able to
report incident counts correctly and for match the source's own
structure precisely, which keeps `raw_offense_code`-style provenance
per offense clean. Given this platform's stated emphasis on being
explainable and not overstating precision (see
[`product.md`](../product.md)), getting incident-vs-offense counting
right from the start is worth that cost. **Not implemented in this
phase** — this is a recommendation for whoever builds the schema.

---

## 3. Timestamp and timezone semantics

Fields: `FIRST_OCCURRENCE_DATE`, `LAST_OCCURRENCE_DATE`,
`REPORTED_DATE` (all `esriFieldTypeDate`).

**CONFIRMED FACT — service-declared semantics:** the layer's own
metadata (`GET .../FeatureServer/324?f=json`) includes:

```json
"dateFieldsTimeReference": {
  "timeZone": "UTC",
  "timeZoneIANA": "Etc/UTC",
  "respectsDaylightSaving": false
}
```

This is Esri's own, authoritative declaration that **every date field
on this layer is UTC, with no daylight-saving adjustment applied**.
This is a stronger answer than Phase 1 had — Phase 1 could only
corroborate via a platform-internal edit-tracking timestamp; this is
the layer's own explicit, documented contract for its business-date
fields.

**EMPIRICAL OBSERVATION — cross-checked independently:** the same
record, fetched two different ways — the raw epoch-millisecond value
from the REST query API, and the human-readable date string from
Denver's separately-generated bulk CSV export — render the **identical
clock time**. Example (`OFFENSE_ID DP2026493289360500`):

| Field | REST API (epoch, interpreted as UTC) | Bulk CSV export (string) |
|---|---|---|
| `FIRST_OCCURRENCE_DATE` | `2026-09-03T23:27:00Z` | `9/3/2026 11:27:00 PM` |
| `REPORTED_DATE` | `2026-09-04T00:17:00Z` | `9/4/2026 12:17:00 AM` |

Two independently-generated delivery pipelines agree, which is more
confirmation than trusting the metadata declaration alone — it rules
out, for example, the bulk-export pipeline silently applying a
different timezone conversion than the live query API.

**UNRESOLVED — and likely unresolvable from outside:** none of the
above proves that Denver Police Department's own records-management
system *originally captured* the wall-clock time correctly as UTC,
as opposed to recording Denver-local time and having some upstream ETL
step simply relabel it "UTC" without actually shifting it by the
correct 6-or-7-hour offset. This is a well-known failure pattern in
municipal open data generally. Both the "genuinely UTC" and "naive
local time mislabeled UTC" theories would produce exactly the
self-consistent picture observed above, since the inconsistency (if
any) lives further upstream than any public interface we can query.
Distinguishing them would require an independent, precisely-timed
reference (e.g. a specific incident whose real-world local
occurrence time is independently documented, such as in a news
report) — out of scope for this phase, and not attempted.

**RECOMMENDATION — safe storage strategy**, given the above:

1. **Trust the source's declared convention** (`UTC`, no DST) for
   parsing: treat every epoch value as a UTC instant unconditionally
   on ingest. Since `respectsDaylightSaving: false` is explicit, there
   is no ambiguous/nonexistent local-time edge case to special-case at
   parse time — this is good news operationally, even with the
   residual upstream-correctness question open.
2. Do **not** introduce a synthetic "Denver local time" reading during
   ingestion — Denver's own metadata says the field carries no DST/
   local-time semantics, so fabricating one would add an assumption
   the source itself doesn't make. If a "local time for display"
   value is ever needed, compute it at render time from the UTC
   instant using a proper IANA timezone (`America/Denver`), not by
   reinterpreting the raw field.
3. **Do** retain, per the general principle already in
   [`architecture.md`](../architecture.md) (raw/normalized
   separation): the raw source value exactly as delivered (epoch ms
   or ISO string, whichever the ingestion path used), the parsed UTC
   instant we derived from it, and a small metadata flag such as
   `source_time_convention: "source-declared-utc-unverified-upstream"`
   — so that if the upstream-mislabeling theory is ever confirmed
   later, correcting it is a single documented reprocessing pass
   against preserved raw values, not an undetectable silent error.
4. **Confirmed, separate data-quality finding to validate against,
   not to silently "fix":** in a full census, 24 of 378,097 rows
   (0.006%) have `REPORTED_DATE` earlier than `FIRST_OCCURRENCE_DATE`
   by a small margin (8–90 minutes in the examples found) — logically
   impossible (a report can't precede the event), almost certainly a
   data-entry rounding/ordering artifact rather than a timezone bug
   (the gaps are minutes, not hours). `LAST_OCCURRENCE_DATE` was never
   found earlier than `FIRST_OCCURRENCE_DATE` (0 violations in the
   full census). Validation should flag rows like this, not reject
   them outright — they're rare and appear to be genuine source noise.

---

## 4. Change detection: what the service actually supports

**CONFIRMED FACT, from the layer's own metadata** — checked
explicitly rather than assumed:

| Capability | Status |
|---|---|
| `capabilities` | `"Query,Extract"` only — no `Create`/`Update`/`Delete`/`Sync` exposed publicly (expected for read-only open data) |
| `supportsChangeTracking` | **not present** (absent = unsupported) |
| `changeTrackingInfo` | **not present** |
| `globalIdField` | `""` (empty — **no GlobalID field exists** on this layer) |
| `syncCapabilities` | **not present** |
| `isDataVersioned` | `false` |
| `hasStaticData` | `false` (the layer is explicitly marked as *not* static, consistent with Denver's own "dynamic" description) |
| Per-row "last updated" field | **none** — only a layer-level `editingInfo.dataLastEditDate`, which changes whenever *any* row changes, telling you nothing about *which* row |

**Conclusion: Esri's native change-tracking/sync mechanism (the
`queryChanges` operation) is not available for this layer.** It
requires both a GlobalID field and the `ChangeTracking` capability;
neither is present. This rules out **Strategy D** as anything more
than "not available" — confirmed by metadata, not assumed.

**CONFIRMED FACT — a genuinely useful lighter-weight mechanism does
exist:** `returnIdsOnly=true` returns **every currently-live
`OBJECTID`** in a single response, with no pagination and no
2,000-row cap (confirmed: 378,097 ids returned in one request, vs. a
plain attribute query which is hard-capped at exactly 2,000 rows per
request regardless of what `resultRecordCount` is requested).

**Reconciliation strategies compared:**

| Strategy | Feasibility here | Cost | Notes |
|---|---|---|---|
| **A — Full reconciliation** (periodically re-pull everything, diff against our stored snapshot) | **Feasible and cheap** via the bulk CSV/GeoJSON export (no pagination needed; one HTTP request + one async poll) | Low — one export job per run, ~378K rows, well within what a nightly batch job can parse and diff | Handles additions, modifications, *and* deletions correctly, since it's a true full comparison |
| **B — Sliding-window reconciliation** (re-fetch only recent dates frequently, full reconciliation occasionally) | Feasible via `WHERE FIRST_OCCURRENCE_DATE > ...` / `REPORTED_DATE > ...` (confirmed working; date filtering supported) | Very low per run, but **cannot see deletions or far-past record revisions** between full runs — Denver's own text says records become "more accurate" over 30+ days, meaning edits are not limited to a narrow recent window | Good as a *supplement* for freshness, not a substitute for (A) |
| **C — Source fingerprinting** (hash raw fields per record, compare hashes to detect changes) | Feasible, and **necessary regardless of which strategy above is chosen**, since there is no row-level updated-at to trust | Cheap (a hash comparison is far cheaper than a full field-by-field diff) | Complements (A): use the full pull to get current rows, hash each, compare against last-stored hash by `OFFENSE_ID` |
| **D — ArcGIS-native change mechanism** | **Not available** — confirmed above | N/A | |

**RECOMMENDATION:** **Strategy A (full reconciliation via bulk
export), fingerprinted per-record (Strategy C), run on a schedule that
matches the source's own update cadence (weekdays)**, optionally
supplemented by a cheap same-day `returnIdsOnly` check if faster
deletion/addition detection between full runs is ever needed. Given
~378K rows today and Denver's own Monday–Friday cadence, a full
export-and-diff comfortably fits a daily batch job; there is no
practical reason here to build the added complexity of Strategy B as
the primary mechanism, though its date-filtered queries are useful
building blocks regardless (e.g. for an intra-day "what's new today"
view before the next full reconciliation lands).

**Ingestion-burden note:** the bulk CSV/GeoJSON/Shapefile/KML export
endpoints are not subject to the 2,000-row query cap, so a full daily
pull is one export job, not ~190 paginated requests — a meaningfully
lighter API burden than repeatedly paging the query endpoint.

---

## 5. Deletion handling

Denver's own dataset description confirms deletions happen (*"is
dynamic, which allows for additions, deletions and/or modifications at
any time"*), and there is no row-level signal distinguishing "this
record was intentionally removed" from "this record temporarily
didn't appear in one incomplete pull."

**RECOMMENDATION — soft-removal model**, evidence-backed by the
fields Phase 1 and this phase have already established are reliable:

```
source_active        boolean   -- true while the record still appears in Denver's feed
first_seen_at         timestamp -- when OUR ingestion first observed this OFFENSE_ID
last_seen_at          timestamp -- most recent full reconciliation run that observed it
source_removed_at     timestamp, nullable -- set once we conclude Denver removed it
removal_confidence    enum      -- see below
```

**Avoiding false deletions from an incomplete run:** never mark a
record removed based on a single missing observation. Concretely:

- A record is only considered a *candidate* for removal when a full
  reconciliation run (Strategy A) completes successfully end-to-end
  (export job finished, file fully downloaded and parsed without
  error) and the record's `OFFENSE_ID` is absent from that complete
  pull.
- Require **N consecutive complete runs** (e.g. 2–3) in which the
  record is absent before setting `source_active = false` and
  `source_removed_at`. This absorbs a run that completed but pulled a
  transiently-stale export (the Hub export job is itself async and
  could, in principle, be served from a slightly-behind cache).
  `removal_confidence` can track this directly:
  `pending` (missing once) → `likely` (missing twice) →
  `confirmed` (missing N times / past a grace period).
- If a run itself fails or is only partial (e.g. the export job never
  reached "Completed", or the parse errored partway through), **do not
  treat any absence from that run as evidence of anything** — discard
  that run's "missing" signal entirely rather than counting it toward
  N.

We should never hard-delete our copy: historical provenance is a
stated platform requirement (see
[`architecture.md`](../architecture.md#why-raw-and-normalized-data-stay-separate)),
and a record Denver removes today is still a real thing that happened
and that we observed — normalized display logic can simply filter on
`source_active` without discarding the underlying row.

---

## 6. Raw-record preservation strategy

**Clarifying "immutable raw data" for a source that mutates records:**
immutability should mean *we never overwrite a previously-captured
observation of a source record*, not that the source itself is
frozen. Concretely, "the raw layer" becomes an append-only log of
**(source record, as observed at time T)**, not a single mutable copy
per `OFFENSE_ID`.

**Options considered:**

- **Periodic complete snapshot files** — simplest to reason about
  (one file per day, self-contained), but for a ~378K-row, slowly
  mutating dataset, storing a full duplicate snapshot every single
  day is wasteful: the vast majority of rows are unchanged day to day.
- **Event/version table** (store a new raw version only when a
  record's fingerprint changes) — much more storage-efficient, but
  needs *something* to compare against, which means we still need to
  fetch the full current state on each run to compute the current
  fingerprint — the storage savings come from what we *keep*, not
  what we *fetch*.
- **Object storage + metadata** — a reasonable future evolution
  (e.g. storing each day's raw export in cheap blob storage with a
  small metadata-DB pointer), but this introduces cloud infrastructure
  this phase is explicitly not to build yet.
- **Combination (recommended): periodic full snapshot of the fetch +
  per-record versioning of what's kept.** Every reconciliation run
  fetches the full current dataset (we have to, per §4 — there's no
  cheaper way to see deletions), but we only **persist** a new raw
  version row for an `OFFENSE_ID` when its fingerprint differs from
  the most recently stored version for that id. A record that never
  changes accumulates exactly one stored raw version, ever, no matter
  how many times we re-fetch it; a record Denver revises three times
  accumulates three.

**RECOMMENDATION for V1 (design, not implementation):**

```
raw_offense_version (
  id                  surrogate key
  source              e.g. "denver-pd-open-data"
  source_record_key    = OFFENSE_ID
  fingerprint          hash of the normalized raw field set (see §11 pseudocode)
  raw_payload          the record's raw fields, as delivered
  observed_at           timestamp of the reconciliation run that captured this version
  is_current           boolean, exactly one true row per source_record_key
)
```

This directly satisfies "preserve historical source states without
unnecessarily storing massive duplicate datasets" — duplication is
bounded by how often a record *actually* changes, not by how often we
poll it.

---

## 7. Schema drift

**CONFIRMED FACT — current schema** (from the layer's own metadata,
`?f=json`, captured in full in
[`denver.md`](./denver.md#2-schema--fields-actually-available)):
field names, Esri types, and aliases for all 20 attribute fields plus
geometry. No `nullable`/required flag is exposed per field in this
layer's metadata (Esri feature layers generally don't publish a
per-field required flag distinct from what's empirically observed) —
nullability here is necessarily an empirical, not a documented,
property (see the per-field null rates already recorded in
`denver.md` §2–§4).

**Schema-edit timestamp:** the layer exposes
`editingInfo.schemaLastEditDate` (confirmed present in Phase 1's
capture) — a single layer-wide timestamp for the most recent schema
change, with the same "tells you *that* something changed, not *what*"
limitation as `dataLastEditDate` for row edits. Worth recording on
each ingestion run so a change becomes at least detectable.

**RECOMMENDATION — fail safely on schema drift.** Before normalizing
any batch of fetched records, an ingestion job should re-fetch the
layer's own `?f=json` metadata and compare it field-by-field against
the schema it was built against:

| Drift type | Detection | Recommended behavior |
|---|---|---|
| A known field disappears | field name missing from the fresh metadata's `fields` list | **Fail the run, alert.** Never silently treat it as null going forward. |
| A field is renamed | old name gone, a new, unrecognized name appears | **Fail the run, alert.** A rename looks identical to "field removed + field added" from outside; a human needs to confirm the mapping. |
| A field's Esri type changes (e.g. string → integer) | `fields[].type` differs from what's recorded | **Fail the run, alert.** Silently coercing could corrupt values (e.g. an `OFFENSE_CODE` that gains leading zeros as a string vs. an integer). |
| A required field starts arriving null | a field we assumed always-populated (e.g. `OFFENSE_ID`, based on 0 nulls observed) starts returning nulls | **Fail validation for that record specifically** (don't drop the whole run) **and alert** — this is exactly the kind of silent-corruption risk the platform's engineering principles call out avoiding. |
| A new field appears | an unrecognized name in the fresh metadata | **Do not fail** — log/alert for human review, but proceed; a new field is additive and doesn't invalidate existing normalization. |

This is a "prefer alerting over silently corrupting normalized data"
policy, per instructions — not yet implemented, but concretely
specifiable now because we know exactly what today's schema looks
like to diff against.

---

## 8. Taxonomy drift

**CONFIRMED FACT — the code→type→category mapping itself is stable**
within the current published window: across a full census of 378,097
rows, **zero** `OFFENSE_TYPE_ID` values were found mapping to more
than one `OFFENSE_CATEGORY_ID`, and **zero** `(OFFENSE_CODE,
OFFENSE_CODE_EXTENSION)` pairs were found mapping to more than one
`OFFENSE_TYPE_ID`. In other words: no evidence of any code being
*recategorized* within the live window.

**CONFIRMED FACT — but the *set* of active codes does drift over
time.** The clearest example found: offense code `2299`
(`burglary-other`, category `burglary`) has **0 occurrences in 2021 or
2022**, a trickle of 10 in 2023, then jumps to 620 (2024), 867 (2025),
and 587 so far in 2026 YTD. This is not sampling noise — it's a real,
sustained volume shift consistent with a code that was introduced (or
came into active use) partway through the historical window.

**Caveat — most other "year-only" patterns are noise, not drift:** an
initial pass flagged 11 codes that appeared in 2021 but not in the
2026-YTD partial year, and 6 that appeared in 2026 YTD but not 2021.
Checking each one's full year-by-year counts showed nearly all of them
are simply very-low-frequency codes (1–20 occurrences across all 6
years, e.g. `bigamy`, `money-laundering`, `riot-incite`) that happened
not to occur in one particular year by chance — **not** evidence of
retirement or introduction. `2299` stands out precisely because its
volume shift is large and sustained, not a 1-in-6-years coincidence.
This distinction matters: a naive "codes present in year X but not
year Y" scan will produce a lot of false positives for rare codes and
should not be used alone to conclude a code was retired.

**RECOMMENDATION:** when the normalization/taxonomy-mapping phase is
built (not this phase), the `OFFENSE_TYPE_ID → normalized category`
mapping table should be validated against the *current* live set of
distinct types on a schedule (e.g. each full reconciliation run),
flagging any `OFFENSE_TYPE_ID` present in freshly-fetched data that
isn't in the mapping table — the same "new field appears" posture as
§7, applied to taxonomy values instead of schema fields. This
directly follows from `2299` being a real, demonstrated example of a
code that started mattering partway through the window; a
mapping table built once from a snapshot would have simply mis-handled
(or silently dropped) `burglary-other` for the ~2,084 rows in which it
now appears.

---

## 9. API behavior for ingestion design

**CONFIRMED**, via the layer's own metadata and direct testing:

| Property | Value |
|---|---|
| Max records per plain attribute query | **2,000**, hard-enforced (`maxRecordCount: 2000`; tested requesting 5,000 rows → got exactly 2,000 with `exceededTransferLimit: true`) |
| `standardMaxRecordCount` | 16,000 — present in metadata, but confirmed **not** honored by plain attribute queries (still capped at 2,000); likely applies to specific operation types not tested further here |
| Pagination mechanism | `resultOffset` + `resultRecordCount`, ordered by `orderByFields` — tested two consecutive pages (offset 0 and 5, ordered by `OBJECTID ASC`): contiguous, no overlap, no gap |
| Stable ordering | `orderByFields=OBJECTID ASC` (or any field) is supported and produced consistent, non-overlapping pages across two separate requests |
| IDs-only queries | `returnIdsOnly=true` — **confirmed to bypass the 2,000-row cap entirely**, returning all 378,097 ids in one response |
| Count-only queries | `returnCountOnly=true` — cheap, confirmed working, also used in combination with `groupByFieldsForStatistics` + `having` to count *groups* rather than rows (verified: `having count(OBJECTID)>1` + `returnCountOnly` returned the group count, not the underlying row count) |
| Filtering by date | Confirmed: `WHERE FIRST_OCCURRENCE_DATE > TIMESTAMP '2026-09-01 00:00:00'` works and returns a plausible, verifiable count |
| Filtering by OBJECTID | Confirmed: `WHERE OBJECTID > 250000000` works |
| Distinct / statistics support | `advancedQueryCapabilities` confirms `supportsCountDistinct`, `supportsStatistics`, `supportsHavingClause`, `supportsOrderBy`, `supportsPagination`, and `supportsPaginationOnAggregatedQueries` all `true` |
| Rate-limit signaling | **None found.** No `X-RateLimit-*`, `Retry-After`, or similar header present on any response observed in this investigation. No published numeric rate limit was found in Denver's or Esri's documentation for this specific item. |
| Caching signals | `Cache-Control`, `ETag`, and `Last-Modified` response headers are present — a well-behaved ingestion job should honor these (conditional `If-None-Match`/`If-Modified-Since` requests) to reduce unnecessary load, even though no rate limit is enforced that we found. |
| Bulk export (recommended for full pulls) | CSV/GeoJSON/Shapefile/KML via `opendata-geospatialdenver.hub.arcgis.com/api/download/v1/items/<id>/<format>?layers=324` — **async job**: the first request returns `{"status": "Pending", ...}`; the same URL must be polled until it starts returning the actual file body instead of JSON. Not subject to the 2,000-row cap. |

No requests in this investigation exceeded a few dozen total, spread
across several minutes of interactive work — nothing resembling a
high-frequency or abusive pattern was run against the service.

---

## 10. Licensing — Phase 2 follow-up

**Still `UNRESOLVED`.** This phase made a further, deliberate attempt
to resolve it from authoritative sources before accepting that
conclusion again:

- Re-examined the ArcGIS item's own `licenseInfo` (the dataset-level
  "USE CONSTRAINTS" disclaimer) and Denver's general
  `denvergov.org/Terms-of-Use` — both already quoted in
  [`denver.md`](./denver.md#5-terms-licensing-and-commercial-use);
  neither addresses commercial use, redistribution, or attribution for
  the Open Data Catalog specifically.
- Searched specifically for a City and County of Denver **open data
  policy or ordinance** (the mechanism many municipalities use to
  formally adopt an open license such as CC0 or ODbL) — none was
  found via web search.
- Found and **directly read** Denver **Executive Order No. 143**
  ("Information Governance Policy," April 2021) — the one Denver
  executive order that plausibly sounded relevant. Read in full: it
  establishes an internal Information Governance Committee and defines
  internal data-classification categories (Personally Identifiable
  Information, Regulated Data, Proprietary/Confidential Information).
  **It does not address open-data publication, licensing, reuse, or
  commercial use of already-public datasets at all.** Ruled out
  directly, not assumed irrelevant.
- Attempted to find a dataset-group-level "Terms of Use" page distinct
  from the individual item's license on the ArcGIS Hub site; found
  none that added anything beyond what Phase 1 already captured.

**Outcome: `UNRESOLVED — requires further legal/terms review`**,
unchanged from Phase 1, now with two additional authoritative sources
checked and ruled out (rather than merely un-checked).

**Contact channel identified for future clarification:** no dedicated
GIS/open-data team email or contact form was found on
`denvergov.org/Government/Data-and-Maps` or elsewhere searched. The
DCAT feed's own `contactPoint.hasEmail` for this dataset is an
unpopulated template placeholder (`"{{orgContactEmail}}"`) — a data
quality issue in Denver's own metadata, not a real address. The only
concrete, documented general contact channel found for the City and
County of Denver is **Denver 311** (`3-1-1`, or `(720) 913-1311` from
outside Denver), listed on the Data and Maps page. **No message has
been sent to anyone** — per instructions, this is identified as the
channel a future step would use, not something this phase acted on.

---

## 11. Proposed ingestion architecture (design only)

This elaborates the flow already summarized in
[`architecture.md`](../architecture.md), specific to what this
investigation learned about Denver.

```
Denver ArcGIS (bulk export: CSV/GeoJSON)
        │
        ▼
[1] Schema validation
        │  fetch current layer metadata (?f=json); diff fields/types
        │  against last-known schema (§7). Field missing/renamed/
        │  retyped => FAIL RUN + ALERT. New field => WARN + PROCEED.
        ▼
[2] Source fetch
        │  trigger bulk export job; poll until ready; download once.
        │  (fallback: resultOffset-paginated /query calls if the
        │  export endpoint is ever unavailable — same data, ~190
        │  requests instead of 1)
        ▼
[3] Raw record canonicalization
        │  parse each row into a stable, ordered field structure;
        │  do NOT drop unrecognized fields — carry them through so a
        │  schema-drift alert has full context.
        ▼
[4] Source-key validation
        │  confirm OFFENSE_ID present, non-empty, and unique WITHIN
        │  this fetch (re-derive the §1 check on every run, not just
        │  once). Any violation => FAIL RUN + ALERT (see §1 — this is
        │  an empirical property we depend on, not a contract).
        ▼
[5] Fingerprint
        │  hash the canonicalized raw fields per OFFENSE_ID (see
        │  pseudocode below).
        ▼
[6] Version comparison
        │  for each OFFENSE_ID: compare fresh fingerprint against the
        │  most recent stored raw_offense_version.fingerprint.
        │    unchanged => no new raw version written; update
        │      last_seen_at only.
        │    changed / new => write a new raw_offense_version row
        │      (append-only; see §6).
        ▼
[7] Source-record version storage
        │  raw_offense_version table, per §6.
        ▼
[8] Normalization
        │  map canonical raw fields -> normalized incident/offense
        │  model (architecture.md); apply the (not-yet-built)
        │  OFFENSE_TYPE_ID -> category mapping, validated against
        │  today's live distinct type set (§8).
        ▼
[9] incident / offense tables
        │  per §2: offenses keyed by OFFENSE_ID/source_incident_id,
        │  incidents keyed by INCIDENT_ID, one incident has many
        │  offenses.
        ▼
[10] PostGIS geometry
        │  store GEO_LON/GEO_LAT as a point geometry; carry forward
        │  the precision caveats from denver.md §3 as a per-record
        │  flag (e.g. address_precision: exact | block | intersection
        │  | suppressed) rather than presenting all points as equally
        │  precise.
        ▼
[11] Reconciliation
        │  after [6], any OFFENSE_ID present in our store's
        │  "currently active" set but ABSENT from this run's full
        │  fetch is a removal candidate (§5) — only if this run
        │  completed steps [1]-[10] successfully end-to-end.
        ▼
[12] Soft source-removal handling
             increment/confirm removal_confidence per §5; only flip
             source_active=false after N consecutive clean absences.
```

**Pseudocode — fingerprinting (step 5):**

```python
import hashlib
import json

# Only the fields that matter for detecting a real change — excludes
# platform-internal fields like OBJECTID which can shift without the
# underlying record having changed.
FINGERPRINT_FIELDS = [
    "OFFENSE_ID", "INCIDENT_ID", "OFFENSE_CODE", "OFFENSE_CODE_EXTENSION",
    "OFFENSE_TYPE_ID", "OFFENSE_CATEGORY_ID",
    "FIRST_OCCURRENCE_DATE", "LAST_OCCURRENCE_DATE", "REPORTED_DATE",
    "INCIDENT_ADDRESS", "GEO_LON", "GEO_LAT",
    "DISTRICT_ID", "PRECINCT_ID", "NEIGHBORHOOD_ID", "VICTIM_COUNT",
]

def fingerprint(raw_row: dict) -> str:
    canonical = {k: raw_row.get(k) for k in FINGERPRINT_FIELDS}
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
```

**Pseudocode — reconciliation pass (step 11/12):**

```python
def reconcile(current_fetch_offense_ids: set[str], run_completed_cleanly: bool):
    if not run_completed_cleanly:
        return  # never treat a partial/failed run as deletion evidence

    active_in_store = get_offense_ids_where(source_active=True)
    missing = active_in_store - current_fetch_offense_ids

    for offense_id in missing:
        bump_removal_confidence(offense_id)
        if removal_confidence(offense_id) >= CONFIRM_THRESHOLD:
            mark_source_removed(offense_id, at=run_timestamp)

    present_again = active_in_store & current_fetch_offense_ids
    for offense_id in present_again:
        reset_removal_confidence(offense_id)  # a record that reappears
                                                # was never actually gone
```

This is illustrative design pseudocode, not tested or productionized
code — the point is to make the §4/§5/§6 recommendations concrete
enough to implement directly in a future phase.

---

## 12. Summary of what changed in existing docs

See the diffs to `denver.md`, `data-sources.md`, and `architecture.md`
alongside this document. In short:

- `denver.md` §8's open questions are now either resolved (timezone
  convention, `OFFENSE_ID` uniqueness mechanism, native ArcGIS change
  tracking availability) or sharpened into a specific, evidence-backed
  unresolved state (licensing) rather than left as open questions.
- `data-sources.md`'s Denver entries gained a pointer to this document
  and an updated licensing note (still unresolved, now with the
  additional sources checked).
- `architecture.md` gained one short, concrete note (added in Phase 1)
  plus this phase confirms it was the right call: the incident/offense
  split recommended in §2 above validates treating
  `source_incident_id` as the *offense*-level key.
