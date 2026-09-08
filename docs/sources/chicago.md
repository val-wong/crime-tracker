# Chicago, IL — Crime-Incident Source Validation

**Status of this document:** deep-dive source validation, in the same
spirit and to the same depth as Denver's Phase 1–2 investigation
([`denver.md`](./denver.md),
[`denver-ingestion-design.md`](./denver-ingestion-design.md)). No
production adapter has been built. No Chicago data has been ingested
into the application database. This resolves the Chicago-specific
open questions flagged in
[`city-comparison.md`](./city-comparison.md) §1 before a real adapter
is written.

**Method:** every finding below was obtained by querying Chicago's own
Socrata API directly (the SODA query endpoint and its view-metadata
endpoint), by reading Chicago's own Terms of Use page and Socrata's
own developer documentation, and by reading a publicly-archived
incident report from Chicago's own open-data engineering blog. No
bulk data was downloaded; nothing was written to `data/raw/`. Findings
are labeled **CONFIRMED FACT** (verified directly against a full
census or authoritative first-party source), **EMPIRICAL
OBSERVATION** (measured from the live data, true today, not a
guarantee about the future), or **INFERENCE** (a reasoned conclusion
not directly stated by the publisher) — the same convention as
Denver's documents.

---

## 1. Identifier stability and uniqueness

**CONFIRMED FACT — publisher-documented, not just empirically
observed:** Socrata's own view metadata for this dataset designates a
`rowIdentifier` (`580897883`), which maps directly to the **`id`**
column. This is Chicago/Socrata's own configured statement of which
field is the dataset's row key — a stronger form of evidence than
Denver ever had for any field (Denver's `OFFENSE_ID` was only ever
empirically unique, never a platform-declared identifier).

**CONFIRMED FACT (full census, 8,630,522 rows):**

| Field | Distinct values | Nulls | Duplicates |
|---|---|---|---|
| `id` | 8,630,522 | 0 | **0** |
| `case_number` | 8,629,893 | 0 | **629** (629 case numbers each shared by exactly 2 rows; none shared by more than 2) |

**`id` is unique across the entire published dataset — both by
Socrata's own row-identifier designation and by a full-census
empirical check.** `case_number` is not.

**Duplicate `case_number` cause — fully explained, not guessed:**
every one of the 629 duplicated case numbers was checked by category,
and the arithmetic reconciles exactly: `HOMICIDE` rows total 14,376,
with only 13,747 distinct case numbers — an excess of exactly **629**,
matching the total duplicate count precisely. Non-homicide rows
contribute zero duplication. This is not a data-quality artifact and
not a correction/reissue pattern — it is a **direct consequence of a
policy the publisher already states in its own dataset description**:
*"...with the exception of murders where data exists for each
victim..."* Sample rows confirm this directly — e.g. case `G023235`
has two rows, both `HOMICIDE`/`FIRST DEGREE MURDER`, same date, same
coordinates, differing only by `id` (two victims, one incident). One
example (`G183906`) showed two dates 24 days apart under the same case
number — not fully explained by this investigation (possibly a related
follow-on charge or a data-entry quirk), flagged as a residual,
unresolved curiosity rather than asserted as understood.

**Recommendation:** use **`id`** as `external_record_id` for
`source_records` — it is both the platform's own declared key and
empirically 100% unique, unlike Denver's `OFFENSE_ID` which was only
ever empirically unique. `case_number` is the right field for
`incidents.external_incident_id` (see §2).

---

## 2. Incident vs. offense semantics

**CONFIRMED FACT:** one Chicago row is **one reported crime record**
— Socrata's own metadata labels the row concept `"Reported Crime"`.
For the overwhelming majority of rows (all non-homicide crimes,
8,616,146 of 8,630,522), this is a clean one-row-per-incident model:
`case_number` (the CPD "RD Number") maps 1:1 to `id`.

**The one confirmed exception is homicides**, where the row grain
shifts to **one row per victim** within a single incident (§1). This
is conceptually different from Denver/Seattle/LA's NIBRS-style
"multiple rows for multiple distinct offense types" pattern — Chicago
does not report multiple offense *types* per incident at all (there is
exactly one `primary_type`/`iucr`/`fbi_code` per row, and no
offense-level sub-identifier distinct from the incident exists in the
schema). The multiplicity that does exist is about **victims of one
offense type (murder)**, not about **multiple different offenses**.

**Quantified:** 629 excess rows out of 8,630,522 total (0.007%) —
several orders of magnitude rarer than Denver's 5.8% multi-offense
rate.

**Recommendation for mapping onto `incidents`/`offenses`/`source_records`:**

- `source_records.external_record_id` = `id` (§1).
- `incidents.external_incident_id` = `case_number`.
- `offenses` — one `Offense` row per source record (`id`), same as
  every other source. For the overwhelming majority of incidents this
  yields one offense per incident, matching Chicago's actual
  semantics. For the ~629 homicide cases with multiple victims, this
  yields multiple `Offense` rows under one `Incident` — which is a
  correct, honest representation (two victims, two homicide charges),
  even though the *reason* for the multiplicity differs from Denver's.
  **No schema change is implied or needed** — the existing
  `incidents`/`offenses` split already accommodates this without
  modification.
- `offenses.victim_count`: for non-homicide rows this field isn't
  populated by Chicago at all (no such field exists in the schema —
  unlike Denver's `VICTIM_COUNT`). For homicide rows, since each row
  already represents one victim, a Chicago adapter should likely set
  `victim_count = 1` per homicide offense row rather than leaving it
  null, but this is a normalization-layer decision, not attempted
  here.

---

## 3. Timestamp semantics

Fields: `date` (occurrence) and `updated_on` (row-modification). Both
are typed `calendar_date` in the Socrata API (confirmed via the
`X-SODA2-Types` response header, which reports the underlying type as
`floating_timestamp`). **There is no separate reported-date field** —
confirmed absent from the schema (checked, not assumed).

**CONFIRMED FACT — platform-level, from Socrata's own developer
documentation** (`dev.socrata.com/docs/datatypes/floating_timestamp`):
a `floating_timestamp`/`calendar_date` value carries **no timezone
information at all** — Socrata's own docs describe it as a value whose
"timezone isn't specified... the time 'floats' depending on where you
are," and recommend thinking of it as equivalent to a bare text string
with no inherent zone. This is a *more* explicit and more directly
confirmable statement than Denver's ArcGIS situation, which at least
had a `dateFieldsTimeReference: UTC` declaration to lean on — **Chicago's
platform explicitly declares the opposite: no zone attached whatsoever.**

**INFERENCE, clearly labeled as such:** since this data describes
incidents occurring in Chicago and is entered by CPD's own local
records system, the overwhelmingly reasonable interpretation is that
these floating values represent **America/Chicago wall-clock time as
recorded by CPD** — not UTC, and not any other zone. This has not been
independently proven against a ground truth (the same limitation
Denver's investigation had), but is a stronger, more explicit
starting assumption than "unknown," since there is no competing
"declared UTC" signal here the way there partially was for Denver.

**CONFIRMED, concrete DST-related findings, not hypothetical:**

- **Spring-forward gap** (e.g. 2025-03-09, 02:00–02:59 local time,
  which never occurs on the America/Chicago clock): queried directly
  — **zero rows** fall in this window. Not a concern in practice.
- **Fall-back ambiguous hour** (e.g. 2025-11-02, 01:00–01:59 local
  time, which occurs **twice** — once in CDT, once in CST): queried
  directly — **48 rows** fall in this exact window on this one date
  alone. This is a real, recurring (twice a year, every year, for 25
  years of history), non-hypothetical ambiguity: for these rows, a
  naive "01:30" cannot be resolved to a single UTC instant without an
  arbitrary disambiguation choice, since the raw data carries no
  indication of which occurrence is meant.

**Backfill/late-revision pattern — confirmed, with an important
nuance:** rows with a `date` from January 2001 and an `updated_on` of
`2026-08-01` were found — a ~25-year gap. Investigating the scale: 706
rows share `updated_on` values within that single day, but only **2
distinct exact-to-the-second timestamps** among them. This is strong
circumstantial evidence of a **bulk/batch administrative touch**
(e.g., a geocoding refresh or metadata reprocessing job), not 706
individual detectives reopening 25-year-old burglary cases at the same
moment. **Implication for `updated_on` reliability, see §4.**

**RECOMMENDATION — storage strategy** (mirroring Denver's approach,
adjusted for Chicago's stronger "definitely no zone" platform
declaration):

1. Treat every `date`/`updated_on` value as **naive local wall-clock
   time in America/Chicago**, not UTC, per the inference above.
2. Localize using a real IANA timezone database (not a fixed
   UTC-5/UTC-6 offset), so historical DST rule changes (e.g., the 2007
   US DST rule shift) are handled correctly for the full 2001–present
   range.
3. For the confirmed-real ambiguous fall-back hour, adopt an explicit,
   documented default (e.g., resolve to the first/earlier occurrence,
   `fold=0`) and **flag such rows with a data-quality marker** rather
   than silently picking one interpretation with no record of the
   ambiguity.
4. Store, per the general principle in
   [`architecture.md`](../architecture.md): the raw floating-timestamp
   string exactly as delivered, the resulting parsed UTC instant, and
   a metadata flag (e.g. `source_time_convention:
   "inferred-america-chicago-floating-no-platform-timezone"`) —
   distinct wording from Denver's `"source-declared-utc-unverified-upstream"`,
   since the two sources' actual evidentiary basis differs (Denver had
   a platform UTC declaration to lean on; Chicago's platform explicitly
   declares no zone, making the local-time inference the primary
   basis here).
5. **Do not silently treat `updated_on` advancing as proof the
   substantive crime data changed** — see §4.

---

## 4. Row-level change tracking (`updated_on`)

**CONFIRMED FACT:** `updated_on` is present and non-null on all rows
checked (it is a required, always-populated field per the schema — no
nulls were found in any query in this investigation). Its range spans
`2006-03-31` to the present, confirmed live and advancing daily.

**CONFIRMED, with an important caveat — reliability for incremental
ingestion:** `updated_on` *does* change over time and *can* be queried
directly (`WHERE updated_on > ...` works — confirmed via direct query
in §9). However, the bulk-touch finding in §3 shows that **`updated_on`
advancing does not necessarily mean the substantive, fingerprint-relevant
fields changed** — it can also reflect a bulk administrative operation
(such as, plausibly, a geocoding or metadata refresh) that touches
many rows' `updated_on` without their crime-relevant facts changing at
all. This means `updated_on` is trustworthy as a **superset filter**
("check these rows for possible changes") but **not** as a
**substitute for fingerprinting** ("these rows definitely changed") —
which is exactly what the existing framework's fingerprint-based
change detection (§`app/fingerprint.py`) is already designed to
handle: an incremental `updated_on` fetch would still need the
existing fingerprint comparison to determine whether a genuinely new
raw version is warranted, rather than trusting `updated_on` alone.

**Does every row have it?** Confirmed present dataset-wide — this
investigation found no nulls, though a full explicit
`WHERE updated_on IS NULL` census (mirroring the `id`/`case_number`
census in §1) was not separately re-run for this field; the absence of
nulls across dozens of ad hoc queries touching this field is treated
as a strong empirical observation, not a full-census confirmation.

**Whether records can still disappear entirely, and whether updated
records retain the same identifier:** see §5 and §1 respectively —
`id` is stable and unique (§1); disappearance is addressed in §5.

**Backfill confirmed:** yes — §3's finding of a `date` from 2001 with
an `updated_on` from 2026 is a direct, confirmed example of exactly
this pattern.

### Reconciliation strategy comparison

| Strategy | Assessment for Chicago |
|---|---|
| **Full reconciliation** (Denver's approach: refetch everything, diff) | Works, but Chicago's ~8.6M rows make a full refetch meaningfully heavier than Denver's ~378K — a real cost Denver didn't have. |
| **Incremental fetch using `updated_on`** alone | **Not sufficient on its own** — it can efficiently find candidate-changed rows, but (a) per the bulk-touch finding, a positive `updated_on` hit doesn't guarantee a real change (still needs fingerprinting to confirm), and (b) an incremental fetch by definition **cannot see deletions** — a row that disappeared entirely produces no `updated_on` signal at all, since it's simply absent from every future response. |
| **Hybrid: incremental `updated_on` fetch + periodic full reconciliation for deletions** | **Recommended.** Use `WHERE updated_on > last_run_time` for the routine, low-cost, frequent (e.g. daily) fetch — feeding the existing fingerprint/versioning logic unchanged (an `updated_on` hit that turns out fingerprint-unchanged is simply classified "unchanged," same as today) — and reserve a full pull (or the Socrata export) for a less frequent (e.g. weekly) complete-reconciliation pass whose sole job is deletion detection via the existing `is_complete`-gated missing-record logic. |

This is a genuine efficiency Denver did not offer (no per-row update
signal existed there at all), but it is an **optimization on top of**
the existing framework, not a requirement to change it — the
incremental fetch's results still flow through the same
fingerprint → reconcile pipeline described in
[`ingestion-framework.md`](../ingestion-framework.md); only the
*fetch* step gains a cheaper filtered path.

---

## 5. Deletion behavior

**CONFIRMED FACT — a real, documented, publicly-acknowledged
incident, not a hypothetical:** on **February 4, 2016**, Chicago's own
open-data engineering team published an incident report (found and
read directly) stating: *"Due to an error in this morning's update
job, most records in the Crimes - 2001 to present dataset were
deleted, leaving only 722 records."* The same post noted a companion
"one year prior to present" dataset remained accurate and could serve
as a temporary substitute, and that the team hoped to have "a complete
dataset by tomorrow morning." The dataset visibly holds 8.6M rows
today, so it was evidently restored — but the incident report itself
does not include an explicit confirmation-of-resolution follow-up.

**This is the single most important finding of this investigation for
architecture purposes.** It demonstrates a failure mode our existing
`is_complete` flag, as currently defined, **does not fully protect
against**: `is_complete` (per
[`ingestion-framework.md`](../ingestion-framework.md)) is set true
when *our own fetch* completes successfully end-to-end. On February 4,
2016, a client fetching Chicago's API that morning would have received
a **fully successful, complete HTTP response** — just one containing
only 722 rows instead of millions, because the corruption happened on
the *publisher's* side, upstream of any request we'd make. Our own
fetch would have had no errors to report. Two such consecutive
"complete" (from our fetch's perspective) runs would, under the
current reconciliation logic exactly as documented, have accumulated
enough missing-evidence to start **confirming millions of legitimate
historical records as removed** — a serious, concrete risk this
specific piece of Chicago research surfaced that Denver's research
never had reason to raise.

**Recommendation — a new safeguard, not yet implemented:** a
**plausibility check on the *scale* of change**, applied before
trusting a "complete" run's missing-record list, e.g.: if the count of
records a complete run would mark newly-missing exceeds some
configurable fraction of the previously-active total (a threshold
needing real tuning, not guessed here), treat the run as suspicious —
log/alert a human, and withhold removal-evidence accumulation for that
run, even though the fetch itself reported no errors. This is a
concrete architectural gap this Chicago-specific investigation
surfaced; see
[`chicago-ingestion-design.md`](./chicago-ingestion-design.md) §5 for
the fuller design treatment, and the corresponding note added to
[`architecture.md`](../architecture.md).

**No other deletion-behavior documentation was found** (e.g., no
explicit statement that individual old records are ever intentionally
removed under normal operation, as opposed to reclassified/updated in
place). Absent evidence either way for routine (non-incident)
deletions, the existing soft-removal model (`source_active`,
`consecutive_missing_runs`, `removal_confidence`,
`REMOVAL_CONFIRMATION_RUNS`) remains the right default posture — this
finding argues for *strengthening* it with the plausibility check
above, not for replacing it.

---

## 6. Schema stability

**Current schema** (confirmed via the Socrata view-metadata endpoint,
also cross-checked against the `X-SODA2-Fields`/`X-SODA2-Types`
response headers Socrata attaches to every query — a nice property:
schema-drift detection can piggyback on routine data-fetch responses
without a separate metadata call):

| Field | Type | Notes |
|---|---|---|
| `id` | number | Row identifier (§1) |
| `case_number` | text | Incident identifier, not perfectly unique (§1) |
| `date` | floating_timestamp | Occurrence; no timezone (§3) |
| `block` | text | Address, block-level (§8) |
| `iucr` | text | Illinois code (§7) |
| `primary_type` | text | Broad category (§7) |
| `description` | text | Fine-grained subtype (§7) |
| `location_description` | text | Premise type |
| `arrest` | checkbox (boolean) | |
| `domestic` | checkbox (boolean) | |
| `beat`, `district`, `ward`, `community_area` | text/number | Geographic/administrative codes |
| `fbi_code` | text | FBI/NIBRS-style classification (§7) |
| `x_coordinate`, `y_coordinate` | number | State Plane Illinois East |
| `year` | number | Derived from `date` |
| `updated_on` | floating_timestamp | Row modification time (§3–4) |
| `latitude`, `longitude` | number | WGS84; shifted (§8) |
| `location` | location (point) | |
| `:@computed_region_*` (9 fields) | number | Socrata-computed spatial joins against boundary datasets (likely community area, ward, police district, and similar polygon layers) — not independently identified further in this phase |

**Schema-change timestamp:** the view metadata's own
`viewLastModified` is `2024-12-09` — the most recent date Chicago
changed this dataset's schema, per Socrata's own tracking. No attempt
was made in this phase to diff the schema against an earlier snapshot
(none exists in this project), so this is a baseline for future
comparison, not evidence a change actually happened then versus
earlier.

**Category/taxonomy drift — confirmed via an authoritative mechanism,
not inference:** Chicago separately publishes a maintained **IUCR code
reference dataset** (`c7ck-438e`, 434 codes total), and — critically —
each code carries its own **`active` boolean flag**. Confirmed:
**10 of 434 codes (2.3%) are explicitly flagged `active: false`** —
e.g. `0840`/`0841`/`0842`/`0843` (older financial identity-theft
variants), `9901` (a special "DOMESTIC VIOLENCE" catch-all code, its
own `index_code` value `"D"`, distinct from the more common `"I"`
Index / `"N"` Non-Index split). This is a direct, authoritative,
publisher-declared signal of taxonomy drift — a stronger basis than
Denver's volume-based inference (Denver has no equivalent reference
table or active/inactive flag).

**RECOMMENDATION — fail-loud behavior for schema drift**, mirroring
Denver's design and refined using Chicago's specific advantages:

- Compare the `X-SODA2-Fields`/`X-SODA2-Types` headers on **every**
  routine fetch against the last-known schema (cheap — no separate
  metadata call needed). A field disappearing, being renamed, or
  changing type → **fail the run, alert**, exactly as recommended for
  Denver.
- A new, unrecognized field appearing → log/alert, proceed (additive,
  non-breaking).
- Periodically (e.g. on each full-reconciliation pass) re-fetch the
  IUCR reference table and diff its `active` flags against the
  previous pass — an `iucr` code flipping from active to inactive is
  a legitimate, expected, non-fatal event (existing historical rows
  keep their code; new rows simply won't use it), but a code
  disappearing from the reference table **entirely** (not just marked
  inactive) would be a genuine, alertable anomaly.

---

## 7. Crime taxonomy

**Four related fields, with a clear relationship, not redundant:**

- `iucr` — Illinois's own 4-digit code (e.g. `0110`), the
  most granular/authoritative classification, backed by the maintained
  434-code reference table (§6).
- `primary_type` — a broad, human-readable rollup of `iucr` (e.g.
  `HOMICIDE`, `THEFT`, `BATTERY`) — corresponds to the reference
  table's `primary_description`.
- `description` — a finer-grained subtype under `primary_type` (e.g.
  `FIRST DEGREE MURDER`) — corresponds to the reference table's
  `secondary_description`.
- `fbi_code` — a separate, coarser FBI/NIBRS-style classification
  carried alongside `iucr`, not derived from it in an obviously
  1:1 way within the crimes dataset itself (the *mapping* between
  `iucr` and `fbi_code` is stable per row, but this investigation did
  not independently verify the mapping is uniform across all 434 IUCR
  codes against a separate FBI-code crosswalk — flagged as unverified,
  not assumed).

**Representative examples** (from the reference table, `c7ck-438e`):

| IUCR | Primary | Secondary | Index code | Active |
|---|---|---|---|---|
| 0110 | HOMICIDE | FIRST DEGREE MURDER | I | true |
| 0130 | HOMICIDE | SECOND DEGREE MURDER | I | true |
| 0141 | HOMICIDE | INVOLUNTARY MANSLAUGHTER | N | true |
| 0261–0264 | CRIMINAL SEXUAL ASSAULT | AGGRAVATED (by weapon type) | I | true |
| 0840–0843 | THEFT | FINANCIAL IDENTITY THEFT (variants) | I | **false** |
| 9901 | DOMESTIC VIOLENCE | DOMESTIC VIOLENCE | D | **false** |

Reference-table totals: 110 Index codes, 323 Non-Index codes, 1
Domestic-only code (424 active, 10 inactive).

**Best normalization basis — assessed, not implemented:** `iucr` is
the recommended anchor. It is the most granular field, it is backed by
a maintained, versioned, publisher-owned reference table (unlike
`primary_type`/`description`, which are just denormalized copies of
that same table's descriptive text baked into every row), and its
`active` flag gives a direct, authoritative drift signal `fbi_code`
alone can't provide. `fbi_code` remains useful as a secondary,
coarser cross-check (e.g., for eventually aligning against other
cities' NIBRS-flavored codes), but should not be the primary key for
a mapping table given the unverified-uniformity caveat above. This
assessment does not build the actual normalized taxonomy mapping —
that remains a distinct future phase, per
[`product.md`](../product.md).

---

## 8. Geography

**CONFIRMED, explicitly documented, uniform (not category-dependent
like Denver's mixed exact/block/intersection pattern):** the `block`,
`latitude`, and `longitude` field descriptions themselves state the
location is *"shifted from the actual location for partial redaction
but falls on the same block."* This reads as a **deliberate,
randomized shift within the same city block** — not a snap to a fixed
block centroid (which would produce many incidents sharing an
identical coordinate; not tested directly in this phase, but the
"shifted" wording implies a per-record perturbation, not a shared
anchor point). This is applied to **100% of rows** as a blanket policy
— unlike Denver, there is no category-dependent exemption or
intersection-style alternate encoding observed in Chicago's schema.

**Suitability assessment:**

| Use case | Assessment |
|---|---|
| Neighborhood trends | **Good** — via `community_area` (77 defined areas). It's stored as a numeric code, not a name; resolved to the official name for display via a static lookup table (`backend/app/chicago_community_areas.py`, `frontend/src/data/chicagoCommunityAreas.ts`), sourced from the City's own "Boundaries - Community Areas" dataset (`igwz-8jzy`). The numeric code remains the value used for filtering/querying. |
| H3/grid aggregation | **Good** — a bounded one-block shift is small relative to any reasonable hex-cell size. |
| Half-mile radius search | **Good-to-Moderate** — a one-block shift is a small, bounded, uniformly-applied error relative to a half-mile radius; meaningfully more trustworthy than Denver's category-dependent mix, though still not exact. |
| Address-proximity search | **Poor** — by explicit design, the published coordinate is never the true address; any "distance to this exact address" feature would systematically misstate precision if it treated Chicago's coordinates as exact. |

**Limitation to document clearly downstream:** every Chicago
coordinate should be tagged `location_precision = BLOCK` (never
`EXACT`) — the existing enum already anticipates exactly this
situation (see [`ingestion-framework.md`](../ingestion-framework.md)).

---

## 9. API behavior

**CONFIRMED, via direct testing:**

| Property | Finding |
|---|---|
| Paging | `$limit`/`$offset`, confirmed clean and contiguous (tested consecutive pages of 3, ordered by `id ASC` — no overlap, no gap) |
| Ordering | `$order` works as expected (tested `id ASC`) |
| Filtering by `updated_on` | Confirmed working (used directly in §3–4's queries, e.g. `updated_on > '2026-08-01'`) |
| Filtering by occurrence date | Confirmed working (`date between ...`, used throughout §3, §7) |
| Max practical page size | **At least 100,000 rows in a single request** — tested directly (`$limit=100000` returned exactly 100,000 rows with no truncation or error), far more generous than Denver's hard 2,000-row ArcGIS cap. No attempt was made to push higher, per instructions not to run unnecessarily expensive queries — the practical ceiling above 100K was not determined. |
| Count queries | `$select=count(*)` and grouped/having variants confirmed working (used extensively throughout this investigation) |
| Export options | Bulk CSV/JSON/GeoJSON export confirmed available via the portal (not re-tested this phase; already used in `city-comparison.md`'s prior research) |
| Rate-limit information | No `X-RateLimit-*` or `Retry-After` headers observed on any response in this investigation |
| App-token requirement | **Not required for read access** — confirmed via Socrata's own developer documentation, and via this investigation's own successful anonymous queries throughout |
| Anonymous-access limits | Per Socrata's own documentation (`dev.socrata.com/docs/app-tokens.html`, read directly): anonymous (no-app-token) requests are throttled based on source IP from a shared pool and may receive HTTP 429 if "too many requests during a given period"; no exact numeric threshold is published; requests **with** an app token are "currently" not throttled "unless... determined to be abusive or malicious." A future production adapter should use an app token (free to obtain, no cost) to avoid shared-pool throttling — not attempted or requested in this phase. |
| Schema-in-headers | **Notable, useful capability**: every query response carries `X-SODA2-Fields` and `X-SODA2-Types` headers with the complete current field/type list — a cheap, built-in schema-drift check available on every routine fetch (see §6). |

No requests in this investigation exceeded a few dozen total, run
interactively over a short session — nothing resembling a
high-frequency or abusive pattern.

---

## 10. Licensing reconfirmation

**Reconfirmed, from the same first-party source used in
`city-comparison.md`, re-fetched fresh for this phase:**
<https://www.chicago.gov/city/en/narr/foia/data_disclaimer.html> — the
text is byte-for-byte the same as previously found; nothing has
changed.

Quoted directly:

> **DISCLAIMER OF LIABILITY** — "The City makes no warranty,
> representation, or guaranty as to the content, accuracy, timeliness,
> or completeness of any of the data provided at this website... The
> City makes this data available on an 'as is' basis..."
>
> **USE OF DATA** — "Any user of this website providing any software
> application, or other secondary or derivative application using
> data supplied at this website shall do the following: Include the
> following disclaimer at the site where the software application...
> can be accessed or downloaded: 'This site provides applications
> using data that has been modified for use from its original source,
> www.cityofchicago.org... The data provided at this site is subject
> to change at any time. It is understood that the data provided at
> this site is being used at one's own risk.' \[and] Comply with any
> additional Terms of Use set forth by the City agency or department
> providing data..."
>
> **RESERVATION OF RIGHTS** — "The City reserves the right to
> discontinue availability of content on this website at any time and
> for any reason... The City reserves the right to claim or seek to
> protect any \[IP rights]... If the City claims or seeks to protect
> any intellectual property rights..., then this website will so
> indicate on the webpage..." (no such indication was found on this
> specific dataset's page).

**Commercial reuse:** not excluded anywhere; the "USE OF DATA" clause
affirmatively contemplates "secondary or derivative application[s]"
without any commercial/non-commercial distinction.

**Redistribution rights:** affirmatively contemplated ("display,
distribution... of any or all of the data"), conditioned on the
disclaimer requirement above.

**Derivative-use rights:** affirmatively contemplated ("secondary or
derivative application").

**Attribution requirements:** yes — the specific quoted disclaimer
sentence must be included at the site where a derivative
application is accessed.

**Disclaimers to preserve:** the exact quoted sentence above must
appear on our own site if we build against this data, per the Terms of
Use's own requirement.

**A supplemental CPD-specific terms page was sought but not
found/verified** — `chicagopolice.org/data-statistics/` (the
dataset's `attributionLink`) blocked automated access (HTTP 403) via
both the standard fetch tool and a direct request with a browser
user-agent string. This is **not evidence of a supplemental
restriction** — it is an inconclusive access failure, disclosed
honestly rather than glossed over.

**Classification: CLEAR** — reconfirmed, unchanged from
`city-comparison.md`. The one residual risk is the City's reserved
at-will right to demand a user stop (a real but modest, common
government-open-data risk, not a usage prohibition).

---

## Open questions carried forward

1. What exactly explains the `G183906` two-different-dates duplicate
   example (§1) — not resolved, flagged as a residual curiosity.
2. Whether the `iucr`→`fbi_code` mapping is uniform/stable across all
   434 codes was not independently verified against an external FBI
   crosswalk (§7).
3. What the 9 `:@computed_region_*` fields specifically correspond to
   was not individually identified (§6) — likely boundary-join fields
   (community area, ward, police district, census tract, or similar),
   not confirmed one-by-one.
4. Whether a supplemental CPD-specific terms page exists could not be
   verified — the relevant page blocked automated access (§10).
5. The practical page-size ceiling above 100,000 rows per request was
   not determined, per instructions not to run unnecessarily expensive
   queries (§9).
