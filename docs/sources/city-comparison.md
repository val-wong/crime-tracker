# Alternate V1 City Comparison

**Status of this document:** source-discovery and comparative research
only. No production adapter has been built for any city in this
document. No data from any city here has been ingested into the
application database. This phase exists to identify the strongest
fallback(s) for V1 in case Denver's licensing status
([`denver.md`](./denver.md) §5) does not resolve — it does not change
Denver's status, and it does not authorize building anything yet.

**Method:** every finding below was obtained by querying each city's
own official open-data platform directly (Socrata's SODA API and view
metadata endpoint) and by reading each city's own terms-of-use pages,
executive orders, or codified law where findable — the same standard
applied to Denver in Phases 1–2. No third-party crime-data aggregator
was used as a source. A handful of aggregate SODA queries
(`count`, `min`/`max`, `group by`) were run per city to verify
coverage, record counts, and taxonomy; no bulk data was downloaded and
nothing was written to `data/raw/`. All five cities requested were
investigated; no substitute city was added, since none surfaced with
unusually stronger terms than what was already found.

Confirmed facts are distinguished from empirical observations and
reasoned inferences throughout, following the same convention as
[`denver.md`](./denver.md) and
[`denver-ingestion-design.md`](./denver-ingestion-design.md).

---

## 1. Chicago

### Source

- **Dataset:** "Crimes - 2001 to Present"
- **Publisher:** Chicago Police Department (CPD), via its CLEAR
  records-management system
- **Landing page:** <https://data.cityofchicago.org/Public-Safety/Crimes-2001-to-Present/ijzp-q8t2>
- **Platform:** Socrata (SODA API + bulk CSV/JSON/GeoJSON export)
- **Credentials:** none required for read access (confirmed via
  anonymous SODA queries)

### Coverage

- **Historical start:** confirmed `2001-01-01` — a genuine, **non-rolling** full archive back to 2001 (25 years), unlike Denver's rolling ~5-year window.
- **Latest data:** confirmed current to `2026-08-29` (minus the "most recent seven days," per the dataset's own description, held back pending initial review).
- **Record volume:** confirmed **8,630,522 rows** (full census via SODA `count(*)`).
- **Update frequency:** publisher states "updated daily"; consistent with the live `updated_on` max of `2026-09-05`.
- **Reporting delay:** publisher states the most recent 7 days are withheld, and that "preliminary crime classifications may be changed at a later date based upon additional investigation" — records are explicitly revisable.

### Schema

| Concept | Field(s) | Notes |
|---|---|---|
| Row identifier | `id` | Socrata-managed; safer external-id choice than `case_number` |
| Incident identifier | `case_number` (CPD "RD Number") | **Not perfectly unique** — confirmed 8,629,893 distinct values across 8,630,522 rows (629 duplicates); Chicago is overwhelmingly, but not exactly, one-row-per-case |
| Offense date/time | `date` | "sometimes a best estimate," per its own description |
| Reported date/time | — | **Not present as a separate field** — only `date` (occurrence) is exposed |
| Offense category/code | `iucr` (Illinois Uniform Crime Reporting code), `primary_type`, `description`, `fbi_code` | Four-layer taxonomy — see Taxonomy below |
| Lat/long | `latitude`, `longitude` | Present; see Geographic quality |
| Address/block | `block` | Always block-level (see below) |
| Neighborhood/district | `community_area`, `district`, `ward`, `beat` | No direct "neighborhood name" field — `community_area` is a numbered code |
| Arrest/status | `arrest`, `domestic` | Boolean flags — arrest **is** tracked here, unlike Denver |
| Row-level update timestamp | **`updated_on`** | **Confirmed present** — the only one of the five cities with a genuine per-row "last modified" field |

### Geographic quality

**Confirmed, explicitly documented, uniform policy** (from the field
descriptions themselves): *"The \[latitude/longitude] is shifted from
the actual location for partial redaction but falls on the same
block"*; `block` is *"the partially redacted address... placing it on
the same block as the actual address."* This is **always applied**
(not category-dependent like Denver's mixed exact/block/intersection
pattern) — a simpler, more consistently documented generalization
policy than Denver's.

- Neighborhood trends: **Good** (via `community_area`, though it's a
  numeric code needing a lookup table, not a name).
- Map display / hex-grid aggregation: **Good** — block-level precision
  is more than sufficient.
- Proximity search: **Moderate** — a uniform one-block shift is a
  known, bounded error, arguably easier to reason about than Denver's
  category-dependent mix, but still not exact.

### Mutability

**Confirmed** via the dataset's own `updated_on` field: min
`2006-03-31`, max `2026-09-05` — rows are actively revised, and *when*
each row was last touched is directly queryable. This is a genuine
capability none of Denver, Seattle, LA, or (as far as exposed in
schema) NYC provide — it would let a future Chicago adapter do
incremental `WHERE updated_on > last_run_time` fetches instead of
always doing a full reconciliation pull, an efficiency our existing
framework doesn't require but could take advantage of.

### Taxonomy

Four layers: `iucr` (Illinois's own code) → `primary_type` (broad,
e.g. `THEFT`, `BATTERY`, `CRIMINAL DAMAGE`, `NARCOTICS`, `ASSAULT`) →
`description` (fine-grained subtype) → `fbi_code` (an NIBRS/UCR-style
classification carried alongside). Top categories by volume (full
census): THEFT (1,833,355) · BATTERY (1,572,122) · CRIMINAL DAMAGE
(980,567) · NARCOTICS (769,466) · ASSAULT (582,538) · OTHER OFFENSE
(539,310) · BURGLARY (457,100) · MOTOR VEHICLE THEFT (446,903) ·
DECEPTIVE PRACTICE (401,809) · ROBBERY (318,741). Having both a local
code (`iucr`) and an FBI-classification field side by side is a
genuine normalization aid — likely the easiest of the five to map
onto a standardized category set.

### Licensing — **CLEAR**

The Socrata metadata's `licenseId` is literally `SEE_TERMS_OF_USE` —
ambiguous on its own, so (per instructions) the actual Terms of Use
page was read directly:
<https://www.chicago.gov/city/en/narr/foia/data_disclaimer.html>
(fetched directly; verbatim quotes below).

Unlike Denver's or LA's terms (liability disclaimer only, no
affirmative use grant), Chicago's has an explicit **"USE OF DATA"**
section that affirmatively contemplates and authorizes exactly our
use case:

> "Any user of this website providing any software application, or
> other secondary or derivative application using data supplied at
> this website shall \[include a specified disclaimer sentence on
> their own site] \[and] comply with any additional Terms of Use set
> forth by the City agency or department providing data..."

This is a real, satisfiable condition (include one specific sentence;
no department-specific supplemental terms were found for this
dataset), not a prohibition — it presumes redistribution/derivative
use is normal and permitted. No commercial-use exclusion is stated
anywhere. The one real caveat: a **"RESERVATION OF RIGHTS"** clause
lets the City "require a user... to terminate any and all display,
distribution or other use... for any reason" — an at-will revocation
right, which is a real but modest risk common to government open-data
terms generally (not evidence the use itself is prohibited).

**Classification: CLEAR**, conditioned on including the specified
disclaimer text in any derivative product and retaining awareness that
the City could revoke permission going forward.

---

## 2. New York City

### Source

- **Datasets (two, covering different windows of the same underlying system):**
  - "NYPD Complaint Data Historic" — <https://data.cityofnewyork.us/Public-Safety/NYPD-Complaint-Data-Historic/qgea-i56i>
  - "NYPD Complaint Data Current (Year To Date)" — <https://data.cityofnewyork.us/Public-Safety/NYPD-Complaint-Data-Current-Year-To-Date-/5uac-w243>
- **Publisher:** NYPD, via NYC Open Data
- **Platform:** Socrata
- **Credentials:** none required

### Coverage

- **Historical start:** dataset description says "2006"; **confirmed data-quality caveat:** a `min(cmplnt_fr_dt)` query returned `1010-05-20` (Historic) and `1016-04-30` (Current) — obvious data-entry sentinel/placeholder dates, not real history. The genuinely usable start is 2006 per the publisher's own description; the extreme outliers need filtering by any future adapter.
- **Latest data:** Historic's `max(cmplnt_fr_dt)` is `2025-12-31` (i.e., through the last complete year); Current's is `2026-06-30`.
- **Record volume:** confirmed **10,071,507 rows** (Historic) + **279,513 rows** (Current YTD) — by far the largest volume of the five.
- **Update frequency:** Current YTD's `rpt_dt` range (`2026-01-01` to `2026-06-30`) shows data only through the end of Q2 — consistent with the dataset's own (stale-looking, but structurally accurate) description: *"for all complete quarters so far this year."* This is **quarterly**, not daily — the slowest update cadence of the five cities.
- **Documentation quality caveat:** both datasets' own description text is stale — it still reads "...from 2006 to the end of last year (2019)" and "...this year (2019)" despite `rowsUpdatedAt` showing the underlying data was actually refreshed in 2026. The data appears current; the publisher's own descriptive metadata is not well maintained.

### Schema

| Concept | Field(s) | Notes |
|---|---|---|
| Row/complaint identifier | `cmplnt_num` | "Randomly generated persistent ID for each complaint" |
| Offense identifier (separate from complaint) | **not found** | No distinct offense-level sub-ID field was found in the schema — see Taxonomy |
| Offense date/time | `cmplnt_fr_dt`/`cmplnt_fr_tm`, `cmplnt_to_dt`/`cmplnt_to_tm` | Start and (optional) end of an occurrence window |
| Reported date/time | `rpt_dt` | Present |
| Offense category/code | `ky_cd`/`ofns_desc` (broad), `pd_cd`/`pd_desc` (fine-grained) | Two-level, NYPD-internal codes |
| Lat/long | `latitude`, `longitude`, `lat_lon` | Present; described as "Midblock" coordinates (see below) |
| Address/block | `loc_of_occur_desc`, `prem_typ_desc` | Premise-type description, not a street address |
| Neighborhood/district | `boro_nm` (borough), `addr_pct_cd` (precinct), `patrol_boro` | No finer "neighborhood" field than borough |
| Arrest/status | `crm_atpt_cptd_cd` (completed/attempted) | No explicit arrest flag found |
| Row-level update timestamp | **not found** | Same limitation as Denver — full-pull reconciliation would be required |

Notably, both datasets also carry **victim and suspect demographic
category fields** (`susp_age_group`, `susp_race`, `susp_sex`,
`vic_age_group`, `vic_race`, `vic_sex`) — categories only, no
identifying information, consistent with this platform's aggregate-only
safety principle, but worth deliberate handling by whoever builds a
NYC adapter (simply not exposing/ingesting these fields would be the
straightforward, safety-aligned choice, matching how Denver's
`VICTIM_COUNT`-only field was treated).

### Geographic quality

Latitude/longitude are explicitly labeled **"Midblock"** coordinates —
i.e., generalized to the middle of the reporting block, applied
uniformly (not category-dependent, similar in spirit to Chicago's
policy, though NYC's field description doesn't spell out the privacy
rationale as explicitly as Chicago's does).

- Neighborhood trends: **Moderate** — only borough-level granularity
  natively; a real "neighborhood" layer would need a separate
  geospatial join (e.g., against NYC's neighborhood tabulation area
  boundaries), which is buildable but is extra adapter work the other
  cities don't require.
- Map display / hex-grid aggregation: **Good**.
- Proximity search: **Moderate**, same reasoning as Chicago.

### Mutability

Not empirically verified via a full duplicate/version-drift query in
this phase — a `count(distinct cmplnt_num)` against the 10M-row
Historic table did not complete within a reasonable query budget (the
query was abandoned rather than left running indefinitely; this is
flagged as **unresolved**, not assumed). No row-level update timestamp
was found in the schema, so — like Denver — a future adapter would
need full-pull reconciliation to detect changes and deletions.

### Taxonomy

`ky_cd`/`pd_cd` are NYPD's own internal codes; **no explicit NIBRS or
UCR labeling was found anywhere in the schema** (unlike Chicago,
Seattle, and LA, which all expose an explicit FBI/NIBRS-coded field
alongside their local code). This makes NYC's taxonomy the most
"legacy/idiosyncratic" of the five and likely the hardest to
normalize confidently without a maintained, NYPD-published code
dictionary (one is referenced in the dataset's "About" section as a
data dictionary attachment, but its currency wasn't verified in this
phase).

### Licensing — **CLEAR**

Unlike the ambiguous dataset-level license fields (both NYC datasets
show `licenseId: None`), NYC's reuse terms are set by **codified New
York City law**, not just a discretionary portal policy — Administrative
Code Chapter 5 ("Accessibility to Public Data Sets"), read directly
(via a mirror, since the primary `codelibrary.amlegal.com` host
blocked automated fetches with a 403):

> §23-502(d): "Public data sets shall be made available without any
> registration requirement, license requirement or restrictions on
> their use," except that a department may require a republishing
> third party to "explicitly identify the source and version of the
> public data set, and a description of any modifications."
>
> §23-504: "The city does not warranty the completeness, accuracy,
> content or fitness for any particular purpose or use of any public
> data set" and "this chapter shall not be construed to create a
> private right of action."

This is a direct, legally-binding statement that **no license
restrictions may be imposed** on these datasets, with only a light
attribution/versioning condition for republishers — no commercial-use
exclusion anywhere. This is arguably the single clearest, most durable
legal foundation of any city investigated (a law, not a webpage a
department could quietly edit).

**Classification: CLEAR.**

---

## 3. Los Angeles

### Source

- **Dataset:** "LAPD NIBRS Offenses Dataset" (the current, live one —
  supersedes the now-frozen "Crime Data from 2020 to Present," which
  the publisher's own description says "will no longer be updated"
  following LAPD's move to a NIBRS-aligned records system)
- **Landing page:** <https://data.lacity.org/Public-Safety/LAPD-NIBRS-Offenses-Dataset/k7nn-b2ep>
- **Publisher:** Los Angeles Police Department (LAPD)
- **Platform:** Socrata
- **Credentials:** none required
- **Companion dataset (not investigated in depth):** "LAPD NIBRS
  Victims Dataset" — the same NIBRS-transition pattern as Denver's
  separate sex-crime table, i.e., a second table our framework would
  need its own adapter handling for, not a schema change.

### Coverage

- **Historical start:** the publisher's own description says the new
  records-management system (RMS) went live **March 7, 2024**, and the
  dataset "includes offense information from any date, provided the
  data was entered into" that system. **Confirmed via query:** the raw
  `min(date_occ)` is `1800-01-06` — an obvious sentinel/placeholder,
  not real. Filtering that out, **98.8% of rows (465,576 of 471,285)
  have `date_occ >= 2024-01-01`**, and only 1,972 rows (0.4%) predate
  2020 entirely. **Real, usable historical depth is roughly 2 years** —
  by far the shallowest of the five cities.
- **Latest data:** confirmed current to `2026-08-22`.
- **Record volume:** confirmed **471,285 rows**.
- **Update frequency:** publisher states "refreshed on a bi-weekly
  schedule" — the least frequent of the five.

### Schema

| Concept | Field(s) | Notes |
|---|---|---|
| Incident identifier | `caseno` | Confirmed **not** unique per row: 423,245 distinct values across 471,285 rows |
| Offense identifier | `uniquenibrno` | = CaseNo + NIBR code + offense sequence — structurally identical to Denver's `OFFENSE_ID` pattern |
| Offense date/time | `date_occ`, `time_occ` | |
| Reported date/time | `date_rptd` | |
| Offense category/code | `group` (NIBRS Group A/B), `nibr_code`, `nibr_description`, `crime_against` | See Taxonomy |
| Lat/long | `hndrdth_lat`, `hndrdth_lon` | Explicitly rounded — see Geographic quality |
| Address/block | `hndrdth_loc_chk` | "rounded to the nearest hundred block" |
| Neighborhood/district | `area`, `area_name`, `rpt_dist_no` | Numbered LAPD geographic areas, not neighborhood names |
| Arrest/status | `status`, `status_desc` | Present |
| Row-level update timestamp | **not found** | |

LA's schema is also the richest in explicit contextual flags:
`domestic_violence_crime`, `hate_crime`, `gang_related_crime`,
`transit_related_crime`, `homeless_victim_crime`,
`homeless_suspect_crime`, `homeless_arrestee_crime` (all Yes/No) —
categorical only, consistent with aggregate-safe handling, but a real
design decision for a future adapter about which of these, if any, to
carry into the normalized model.

### Geographic quality

**Confirmed, explicitly documented:** `hndrdth_loc_chk`/`hndrdth_lat`/
`hndrdth_lon` are literally named for "hundredth \[block]" rounding —
the field names themselves encode the privacy transformation. Applied
uniformly (not category-dependent).

- Neighborhood trends / hex-grid aggregation: **Good**.
- Proximity search: **Moderate**, same class of caveat as Chicago/NYC.

### Mutability

Not deeply tested (a companion table exists, similar to Denver's
sex-crime split, but wasn't investigated further in this phase). No
row-level update timestamp found — full-pull reconciliation would be
required, same as Denver.

### Taxonomy

**Confirmed the most standards-compliant of the five.** `nibr_code`
and `nibr_description` carry genuine, recognizable NIBRS offense codes
(e.g. `13B`, `23F`, `23H`, `220`, `240`, `120`, `26F` all appeared in
a live top-15 query) embedded alongside the California Penal Code
section and degree — e.g. *"487(D)(1) - PC - F - Grand Theft Auto -
GTA - 240"*. Top categories by volume: Grand Theft Auto (29,674) ·
Vandalism ≥$400 (28,809) · Simple Battery (24,906) · Burglary from
Motor Vehicle (18,364) · Petty Theft (18,104) · Robbery (14,856).
This is the cleanest mapping to a standardized taxonomy of any city
here — genuinely closer to "NIBRS" than Denver's "NIBRS-influenced
local codes."

### Licensing — **UNRESOLVED**

Read directly, same as Denver's investigation pattern:

- The portal's own Terms of Use (<https://data.lacity.org/terms-of-use>)
  contains **no explicit license grant for the City's own published
  datasets** — only a liability disclaimer (*"the City does not
  warranty the completeness, accuracy, content or fitness of any
  public data set"*) and detailed terms governing *user-submitted*
  content on the site (a different thing entirely). No mention of
  commercial use, redistribution, or attribution for the datasets
  themselves.
- **Executive Directive No. 3** (Dec. 18, 2013, the city's open-data
  policy) was read directly (via a public mirror) and likewise
  contains **no licensing/reuse language** — it defines "Open Data"
  and directs departments to publish it, "subject only to valid
  privacy, confidentiality, security, and other legal restrictions,"
  but never addresses commercial use, redistribution, or attribution.

This is structurally the same gap Denver has — a disclaimer-only ToU
plus a policy document that mandates *publication* but never
addresses *reuse rights*. **Classification: UNRESOLVED**, same as
Denver. Given this **and** the shallow real historical depth (~2
years) **and** the least-frequent update cadence, LA is not
recommended as a fallback candidate despite having the cleanest
technical/taxonomy fit — per instructions, a licensing blocker is not
something to average away.

---

## 4. Seattle

### Source

- **Dataset:** "SPD Crime Data: 2008-Present"
- **Landing page:** <https://data.seattle.gov/Public-Safety/SPD-Crime-Data-2008-Present/tazs-3rd5>
- **Publisher:** Seattle Police Department (SPD)
- **Platform:** Socrata
- **Credentials:** none required

### Coverage

- **Historical start:** publisher states 2008. **Confirmed data-quality
  caveat:** `min(offense_date)` returns `1900-01-01` (a sentinel/
  placeholder); **888 of 1,559,035 rows (0.06%)** have an
  `offense_date` before 2005 — a small, filterable anomaly, not
  representative of real coverage.
- **Latest data:** confirmed current to `2026-09-05`.
- **Record volume:** confirmed **1,559,035 rows**.
- **Update frequency:** publisher states updated "once every 24 hours"
  (daily) — matches Chicago's cadence.
- **Reporting delay:** publisher states General Offense reports
  become available "within 8 hours after the event is closed."

### Schema

| Concept | Field(s) | Notes |
|---|---|---|
| Incident/report identifier | `report_number` | "Primary key/UID for the overall report. One report can contain multiple offenses." Confirmed **not** unique per row: 1,373,100 distinct values across 1,559,035 rows (~13.5% of rows are additional offenses on an already-seen report) |
| Offense identifier | `offense_id` | Explicit, separate field — same structural pattern as Denver's `OFFENSE_ID`/LA's `uniquenibrno` |
| Offense date/time | `offense_date` | "Offense start date... if null then Event Start Date" |
| Reported date/time | `report_date_time` | Present |
| Offense category/code | `nibrs_group_a_b`, `nibrs_crime_against_category`, `offense_category`, `offense_sub_category`, `nibrs_offense_code`, `nibrs_offense_code_description` | Genuinely NIBRS-native — see Taxonomy |
| Lat/long | `latitude`, `longitude` | **Typed as `text`, not a number** — needs casting; see Geographic quality |
| Address/block | `block_address` | |
| Neighborhood/district | `neighborhood`, `precinct`, `sector`, `beat`, `reporting_area` | Genuine named-neighborhood field (MCPP boundaries) — better than Chicago's numeric-only `community_area` |
| Arrest/status | **not found** | No arrest/disposition field identified in the schema |
| Row-level update timestamp | **not found** | |

### Geographic quality

**Confirmed, explicitly documented, uniform:** both `block_address` and
the lat/lon fields are described as *"blurred to the one hundred
block"* — applied consistently, not category-dependent, and unusually
candid about it being deliberate ("blurred," not just "shifted").

- Neighborhood trends: **Good** — genuine named-neighborhood field, an
  advantage over Chicago's numeric community areas.
- Map display / hex-grid aggregation: **Good**.
- Proximity search: **Moderate**, same caveat as the block-generalized
  cities above.

### Mutability

Not deeply tested for row-level revision behavior (no update
timestamp exists to query against). Given the daily-update cadence and
no stated policy on deletions, treat as **unresolved** for now — same
posture Denver started from before its own dedicated investigation.

### Taxonomy

The most NIBRS-native schema among the "clear-licensed" candidates:
explicit `nibrs_offense_code`, `nibrs_group_a_b`,
`nibrs_crime_against_category` fields. The top-level
`offense_category` field, however, is **very coarse — only 3 values**
(`ALL OTHER`: 764,368 · `PROPERTY CRIME`: 711,933 · `VIOLENT CRIME`:
82,734), so meaningful category work would lean on
`offense_sub_category`/`nibrs_offense_code_description` instead.

### Licensing — **CLEAR**

The dataset's own Socrata metadata states, unambiguously:
`license: {"name": "Public Domain"}`, `licenseId: PUBLIC_DOMAIN` — the
simplest, most explicit licensing story of any city investigated,
Denver included. Seattle's site-wide Open Data Policy (dated Feb. 2016,
found alongside the portal's Terms of Use) is consistent with this,
describing an open license as having "no restrictions on copying,
publishing, further distributing, modifying or using the data," with
only a light attribution/versioning condition for third parties
building on it — mirroring NYC's and SF's model. No conflict found
between the dataset-level license and the site-wide policy.

**Classification: CLEAR** — no caveats beyond the light attribution
condition common to all three "CLEAR" cities.

---

## 5. San Francisco

### Source

- **Dataset:** "Police Department Incident Reports: 2018 to Present"
- **Landing page:** <https://data.sfgov.org/Public-Safety/Police-Department-Incident-Reports-2018-to-Present/wg3w-h783>
- **Publisher:** San Francisco Police Department (SFPD), via DataSF
- **Platform:** Socrata
- **Credentials:** none required

### Coverage

- **Historical start:** confirmed `2018-01-01` — a genuine (if
  comparatively shallow) fixed start, not a rolling window. An older,
  separate "Historical 2003 to May 2018" dataset exists for the period
  before this one, not investigated in depth here (would need its own
  adapter handling if long-run history mattered for SF specifically).
- **Latest data:** confirmed current to `2026-09-05`; `rowsUpdatedAt`
  timestamp was `2026-09-06T10:58:18` — effectively live.
- **Record volume:** confirmed **1,061,730 rows**.
- **Update frequency:** the dataset's own description states new
  reports are added "once incident reports have been reviewed and
  approved by a supervising Sergeant or Lieutenant" — event-driven
  rather than a fixed daily/weekly cadence, but observed to be very
  current.

### Schema

| Concept | Field(s) | Notes |
|---|---|---|
| Row identifier | `row_id` | Generic Socrata row key |
| Incident-ish identifier | `incident_id` | Confirmed **not** unique per row: 879,852 distinct values across 1,061,730 rows |
| Case-level grouping | `incident_number` | Fewer distinct values still (758,513) than `incident_id` — see note below |
| Offense date/time | `incident_datetime`, `incident_date`, `incident_time` | |
| Reported date/time | `report_datetime` | Explicitly distinct from occurrence time |
| Offense category/code | `incident_code`, `incident_category`, `incident_subcategory`, `incident_description` | SFPD-internal codes with a mapped category layer |
| Lat/long | `latitude`, `longitude`, `point` | WGS84; see Geographic quality |
| Address/block | `intersection` | **Not** a street address — see Geographic quality |
| Neighborhood/district | `analysis_neighborhood`, `police_district`, `supervisor_district` | Genuine named-neighborhood field |
| Arrest/status | `resolution` (e.g. "Cite or Arrest Adult," open) | Present, and richer than a simple boolean |
| Row-level update timestamp | `data_loaded_at` | Timestamp of upload **to the portal**, not a true business "last edited" field — a partial, weaker substitute for Chicago's `updated_on` |

**Structural nuance worth flagging:** the relationship between
`incident_id` and `incident_number` isn't a clean "one incident, many
distinct offenses" split like Denver/Seattle/LA. The dataset's own
report-type field (`report_type_description`: Initial, Initial
Supplement, Coplogic, Vehicle, etc.) suggests `incident_number`
groups multiple **report-filing events** for the same real-world case
(e.g. an initial report plus a later supplemental or vehicle-recovery
report) as much as it groups distinct **offenses**. A future SF
adapter would need to decide carefully what "offense" means here
rather than assuming it maps 1:1 onto our `offenses` table the way
Denver's `OFFENSE_ID` does — not an architectural blocker, but a real
adapter-design judgment call.

### Geographic quality

**Confirmed, explicitly stated, and the most aggressive generalization
of the five:** *"All incident locations are mapped to nearby
intersections to ensure anonymity."* Empirically, **94.6% of rows
(1,004,161 of 1,061,730) have a non-null `intersection` value** —
consistent with near-universal intersection-level generalization
(not merely a category-dependent subset, unlike Denver). The
dataset's own description also flags a **known internal
inconsistency**: incidents reported before April 24, 2024 are "mapped
to a slightly different set of intersections" than those after,
creating "slight reporting irregularities for analyses that span this
date" — a confirmed, self-disclosed geocoding-methodology change
worth treating like a schema/taxonomy-drift risk, not just a precision
caveat.

- Neighborhood trends: **Good** (`analysis_neighborhood` is a genuine
  named field).
- Map display: **Moderate** — intersection snapping is coarser than
  block-level.
- Proximity search: **Poor-to-Moderate** — the most aggressive, most
  uniform generalization of the five means the least positional
  precision, by design.

### Mutability

`data_loaded_at` gives a portal-ingestion timestamp, but not
confirmation of whether existing rows are edited in place after
initial publication, or whether they can be removed. Not deeply
tested; treat as **unresolved**, same posture as Seattle above.

### Taxonomy

Two-level (`incident_code` → `incident_category`/`incident_subcategory`),
SFPD-internal, no explicit NIBRS/UCR label found. Top categories by
volume: Larceny Theft (305,784) · Other Miscellaneous (73,533) ·
Malicious Mischief (71,770) · Assault (69,441) · Burglary (58,411) ·
Motor Vehicle Theft (57,086). Reasonably clean and human-readable, but
— like NYC — without an explicit standards label to anchor a mapping
against.

### Licensing — **CLEAR**

The dataset's own Socrata metadata states:
`license: {"name": "Open Data Commons Public Domain Dedication and
License", "termsLink": "http://opendatacommons.org/licenses/pddl/1.0/"}`,
`licenseId: PDDL` — an explicit, named, widely-recognized open license
that squarely covers commercial use, redistribution, and derivative
works. San Francisco's Chapter 22D (its own codified open-data
ordinance) and DataSF's general Terms of Use were both consistent with
this (light attribution/versioning condition for republishers; the
same "if the City claims IP rights it will say so on the specific
page" conditional reservation language found in Chicago's and Denver's
terms, but with an explicit PDDL grant sitting on top of it here,
unlike either of those).

**Classification: CLEAR.**

---

## Scoring Table

Scored 1–5 per the requested dimensions (5 = best, except
"Taxonomy normalization difficulty" where 5 = easiest). Denver is
included as the existing baseline for reference; it is not being
re-evaluated here — see `denver.md`/`denver-ingestion-design.md` for
its full basis.

| City | Data availability | Geographic quality | Historical depth | Update frequency | Schema quality | Taxonomy normalization | Mutation handling | Licensing confidence | **Overall V1 suitability** |
|---|---|---|---|---|---|---|---|---|---|
| **Chicago** | 5 | 4 | 5 | 5 | 4 | 4 | 4 | 4 | **5 — Recommended** |
| **New York City** | 5 | 3 | 5 | 3 | 3 | 2 | 2 | 5 | **4 — Recommended** |
| Seattle | 4 | 4 | 4 | 5 | 4 | 4 | 2 | 5 | 4 — Recommended |
| San Francisco | 4 | 3 | 3 | 4 | 3 | 3 | 2 | 5 | 3 — Recommended with caveats |
| Los Angeles (NIBRS) | 3 | 4 | 1 | 2 | 4 | 5 | 2 | **1 (Unresolved)** | **1 — Not recommended** (licensing blocks it regardless of technical merit) |
| *Denver (reference, Phase 1–2)* | *5* | *3* | *3* | *5* | *4* | *4* | *3* | *1 (Unresolved)* | *3 — Recommended with caveats, blocked on licensing* |

**Scores are not mechanically averaged into "Overall V1 suitability."**
Los Angeles scores well technically (arguably the best taxonomy fit of
any city here) but is capped at 1 overall because its licensing
status is a hard blocker, structurally identical to Denver's — exactly
the situation this phase exists to route around, not repeat.

---

## Architecture Compatibility

Checked against every abstraction in
[`ingestion-framework.md`](../ingestion-framework.md):

| Abstraction | Chicago | NYC | Seattle | SF | LA |
|---|---|---|---|---|---|
| `source_records` keyed by generic `external_record_id` | Fits — use `id` (not `case_number`, which has rare duplicates) | Fits — use `cmplnt_num` | Fits — use `offense_id` | Fits — use `incident_id` | Fits — use `uniquenibrno` |
| Raw versions / fingerprinting | Fits; `updated_on` could *optimize* fetching but isn't required by the existing design | Fits (full-pull based, like Denver) | Fits (full-pull based) | Fits (full-pull based) | Fits (full-pull based) |
| Reconciliation state machine | Fits directly | Fits directly | Fits directly | Fits directly | Fits directly |
| `incidents`/`offenses` split | Fits, but usage is nearly 1:1 (Chicago rarely has >1 offense per case) | Fits, but usage is nearly 1:1 (no separate offense-sub-id found) | Fits well — genuine 1:many via `report_number`/`offense_id`, same shape as Denver | **Needs adapter-level judgment** — `incident_number` groups report-filing events, not cleanly "offenses"; the table shape still works, but what gets called an "offense" needs care | Fits very well — same shape as Denver (`caseno` + NIBR code + sequence) |
| PostGIS geography + `location_precision` enum | Fits — `LocationPrecision.BLOCK` for all rows | Fits — `LocationPrecision.BLOCK` (documented as "midblock") for all rows | Fits — `LocationPrecision.BLOCK` for all rows | Fits — `LocationPrecision.INTERSECTION` for ~94.6% of rows, `SUPPRESSED`/`UNKNOWN` for the rest | Fits — `LocationPrecision.BLOCK` for all rows |
| `SourceAdapter` interface | Fits; one dataset, one adapter | **Needs one adapter spanning two physical Socrata resources** (Historic + Current YTD) merged into one logical source — a real but bounded adapter-level detail, not a framework change | Fits; one dataset, one adapter | Fits; one dataset, one adapter (a second, older "Historical 2003–2018" dataset exists if deeper SF history is ever wanted — not required for V1) | Fits; one dataset, one adapter (a companion Victims dataset exists, same pattern as Denver's sex-crime table) |

**No city requires a change to the framework itself.** The one
recurring theme worth calling out for whoever builds a real adapter:
**every city's typed `latitude`/`longitude` fields need adapter-level
casting or interpretation** (Seattle's are `text`; Denver's precision
varies by row; SF's are always intersection-snapped) — already
anticipated by `location_precision` existing as its own field rather
than being inferred from the coordinate alone.

---

## Recommendation

### Primary fallback city: **Chicago**

Chicago is the strongest fallback by a clear margin. It has the
deepest genuine (non-rolling) history of any candidate — 25 years,
8.6M rows — with daily updates and, uniquely among all cities examined
including Denver, an actual row-level `updated_on` timestamp that
would let a future adapter detect changes far more cheaply than the
full-pull-and-fingerprint approach the framework currently requires
for every other source. Its geographic-privacy transformation
(uniform block-level shift) is the most consistently and explicitly
documented of any city. Its licensing terms contain an affirmative,
specific "USE OF DATA" authorization for derivative/redistributed
applications — a real, satisfiable condition, not a bare disclaimer —
which is a materially clearer permission structure than Denver's or
LA's. Nothing about Chicago requires a change to the ingestion
framework already built.

### Secondary fallback city: **New York City**

NYC has the single clearest *legal* foundation of any city
investigated: a codified law (NYC Admin Code §23-502(d)) that
explicitly bars license restrictions on these datasets, not merely a
portal policy a department could revise unilaterally. Its historical
depth and volume are the largest of the five. It's ranked second, not
first, because of three concrete technical costs Chicago doesn't have:
current-year data only updates quarterly (vs. Chicago's daily), the
schema shows no separate offense-level identifier (closer to one
offense per complaint, weaker taxonomy granularity, and the least
standards-anchored code system of the five), and a real adapter would
need to merge two physical datasets (Historic + Current YTD) into one
logical source. None of these are blockers — they're exactly the kind
of concrete, bounded work this framework is designed to absorb — but
they add real effort Chicago doesn't require.

**Honorable mention:** Seattle's licensing is arguably *simpler* than
either recommendation above (a bare, unqualified "Public Domain"
label) and its offense/incident structure is the cleanest 1:many fit
of the "clear" cities. It's not the top pick only because its
historical depth (2008) and record volume are smaller than Chicago's
or NYC's, and it lacks any row-level update signal. If licensing
simplicity is weighted above all else, Seattle is a very reasonable
alternative primary choice.

---

## What would still need to happen before building either adapter

Per the instructions for this phase, none of this is being done now —
listed so a future phase knows where to start, mirroring
`denver-ingestion-design.md`'s own open-questions list:

- Confirm whether Chicago's `case_number` duplicates (629 found) follow
  a knowable pattern (e.g., always a same-day amendment) before
  deciding whether `case_number` or `id` should anchor
  `incidents.external_incident_id`.
- Design the two-dataset merge for NYC (Historic + Current YTD) at the
  adapter level — likely fetch both, de-duplicate by `cmplnt_num`,
  treat Current YTD as authoritative for the overlap window.
- Verify NYC row mutability/deletion behavior directly (the full
  distinct-count query needed to check this was abandoned in this
  phase for cost reasons, not investigated to a conclusion).
- Decide, for SF specifically, what "offense" should mean given the
  `incident_id`/`incident_number`/`report_type_description` structure,
  before mapping it onto `offenses`.
- Whichever city is chosen, repeat Denver's Phase 2-style deep dive
  (`OFFENSE_ID`-equivalent uniqueness census, timezone-declaration
  check via platform metadata, native change-tracking capability
  check, taxonomy-drift sampling across years) before writing a real
  adapter — this phase was a comparative screen, not that same depth
  of investigation for any one city.
