# Denver, CO — Crime-Incident Source Investigation

**Status of this document:** source-discovery and data-quality investigation
only. No ingestion has been built. No data has been committed to this
repository. This is a research artifact to inform the *decision* of
whether Denver is a suitable V1 city, per
[`docs/product.md`](../product.md).

**Phase 2 update:** the open questions in §8 below (OFFENSE_ID
uniqueness, timezone semantics, change-detection capabilities, schema/
taxonomy drift) were investigated in depth and are now resolved or
sharply characterized — see
[`denver-ingestion-design.md`](./denver-ingestion-design.md), which
also proposes (but does not implement) an ingestion architecture.

**Method:** All findings below marked "confirmed" were obtained by
directly querying Denver's live API and metadata endpoints (see
"Queries used" at the bottom) on 2026-09-05/06, or by fetching Denver's
published pages. No third-party crime-data aggregator was used as a
source. A handful of small, ad hoc queries (5–25 rows, and aggregate
`GROUP BY` counts) were run to inspect schema and data quality; no bulk
extract was downloaded and nothing was written to `data/raw/`.

---

## 1. Candidate sources found

| Source | Publisher | First-party? | Registration/API key? | Notes |
|---|---|---|---|---|
| **Denver Open Data Catalog — "Crime" dataset** | City and County of Denver (ArcGIS Online account `The_City_and_County_of_Denver`, org "geospatialDENVER") | **Confirmed first-party.** Owner account, org name, and layer `copyrightText` ("City and County of Denver, Denver Police Department / Data Analysis Unit") all agree. | No — public, anonymous HTTPS access confirmed by direct query. | **Strongest candidate; see below.** |
| Denver Open Data Catalog — "Crime - Sex Related Crimes (aggregated)" | Same (City and County of Denver) | Confirmed first-party (same org/owner) | No | Companion dataset; carries the `sexual-assault` category the main dataset excludes, with identifiers and location stripped. See §3. |
| Denver Open Data Catalog — "Summary Crime Report" | Same (City and County of Denver) | Confirmed first-party | No | A published report/document distribution, not a structured dataset/API — deprioritized per instructions to prefer structured data. Not investigated further. |
| `denvergov.org/Government/Data-and-Maps` | City and County of Denver | First-party | No | General landing page; links out to the ArcGIS Hub catalog above. Not a distinct data source. |
| `data.opencolorado.org` (OpenColorado) | Regional multi-jurisdiction portal (Colorado) | **Not first-party** — a third-party regional aggregator that historically mirrored a Denver crime dataset. Currency/maintenance status not verified in this phase. | Unknown | Not used as a source per instructions ("do not use third-party aggregators as the primary data source"). Flagged only for awareness. |
| `opendatadenver.com` | Unknown/unverified | **Not verified as official.** Domain is not `denvergov.org` or an Esri/ArcGIS Denver-government-owned property. | Unknown | Not investigated further; do not treat as authoritative without separate verification. |

**Chosen candidate for deep investigation:** the ArcGIS Hub **"Crime"**
dataset.

- Landing page: <https://opendata-geospatialdenver.hub.arcgis.com/datasets/geospatialDenver::crime>
- Platform/vendor: Esri ArcGIS Online / ArcGIS Hub (hosted feature service), branded as "geospatialDENVER"
- REST API (Esri GeoServices, the strongest integration path): `https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/arcgis/rest/services/ODC_CRIME_OFFENSES_P/FeatureServer/324`
- Bulk export endpoints (no 2,000-row cap; see §4/§9): CSV, GeoJSON, Shapefile, KML — each at `https://opendata-geospatialdenver.hub.arcgis.com/api/download/v1/items/16d9c82bb36c4475bf87189cfaed653c/<format>?layers=324`

---

## 2. Schema — fields actually available

Confirmed via the FeatureServer's own layer metadata (`?f=json`) and
live sample rows. Table below only lists fields that exist; anything
in the requested checklist that does **not** exist is called out
underneath.

| Source field | Meaning | Example | Candidate normalized field | Notes |
|---|---|---|---|---|
| `OBJECTID` | Esri-managed row surrogate key | `250276973` | *(none — internal to the service)* | Not stable across re-exports; do not use as `source_incident_id`. |
| `INCIDENT_ID` | Denver's incident-level case number | `"DP2026493289"` | — (see `OFFENSE_ID`) | **Not unique per row.** One incident can produce multiple offense rows (confirmed up to 8; see §9). |
| `OFFENSE_ID` | Offense-level identifier = `INCIDENT_ID` + `OFFENSE_CODE` + `OFFENSE_CODE_EXTENSION` | `"DP2026493289360500"` | `source_incident_id` | The real per-row key candidate. Uniqueness of the *construction* wasn't exhaustively verified (see §9). |
| `OFFENSE_CODE` | Denver's local numeric offense code | `"3605"` | `raw_offense_code` | 4-digit local RMS code, not a standard NIBRS letter code. |
| `OFFENSE_CODE_EXTENSION` | Sub-code / variant of `OFFENSE_CODE` | `0`, `1`, `2` | *(fold into `raw_offense_code`)* | |
| `OFFENSE_TYPE_ID` | Fine-grained offense type, human-readable slug | `"theft-shoplift"` | `subcategory` | 173 distinct values observed. |
| `OFFENSE_CATEGORY_ID` | Broad offense category, human-readable slug | `"larceny"` | `category` | 13 distinct values in the main layer (14 incl. `sexual-assault`, only in the companion table). |
| `FIRST_OCCURRENCE_DATE` | Offense start/occurred date-time | `2026-09-03 23:27` | `occurred_at` | Epoch ms in the API; see §9 for timezone caveat. |
| `LAST_OCCURRENCE_DATE` | End of an occurrence *window*, when exact time is unknown | often `null` | *(no direct match; possible future `occurred_at_end`)* | Null for most rows — only used for range/discovery-window offenses (e.g. a burglary discovered days later). |
| `REPORTED_DATE` | When the report was recorded | `2026-09-04 00:17` | `reported_at` | |
| `INCIDENT_ADDRESS` | Location, at varying precision | `"1612 WAZEE ST"` / `"1400 BLK N JASMINE ST"` / `"E COLFAX AVE / N TAMARAC ST"` | *(informs `location_type`/precision, not a direct field)* | Three distinct formats confirmed: exact address, block range, and intersection. See §3. |
| `GEO_X` / `GEO_Y` | Projected coordinates, State Plane Colorado Central (EPSG:2877), feet | `3170068` / `1695048` | *(not used — prefer lon/lat)* | |
| `GEO_LON` / `GEO_LAT` | WGS84 decimal degrees | `-104.895...` / `39.740...` | `longitude` / `latitude` | Present on all but 1 of 378,097 main-layer rows — but see §3 for what "present" does *not* guarantee. |
| `DISTRICT_ID` | Denver PD patrol district | `"2"` | `district` | |
| `PRECINCT_ID` | Precinct/beat sub-code within a district | `"223"` | *(sub-field of `district`, or its own field)* | Not in the originally proposed model; worth adding. |
| `NEIGHBORHOOD_ID` | Official statistical-neighborhood slug | `"east-colfax"` | `neighborhood` | Null in 374 of 378,097 rows (~0.1%). |
| `IS_CRIME` | 0/1 flag | `1` | *(filter, not a normalized field)* | |
| `IS_TRAFFIC` | 0/1 flag | `0` | *(filter, not a normalized field)* | |
| `VICTIM_COUNT` | Aggregate count of victims on the offense | `1` | *(new field — aggregate only)* | A **count**, not victim identity/demographics. |

**Confirmed absent from this dataset** (checked against the layer's
full field list, not assumed):

- No victim information beyond the aggregate `VICTIM_COUNT` — no
  names, demographics, or narrative.
- No suspect information of any kind.
- No arrest information of any kind.
- No disposition/case-status field (e.g. cleared, open, unfounded).
- No explicit `location_type` field (e.g. "residence", "street",
  "commercial") — would have to be inferred, if at all, from
  `INCIDENT_ADDRESS` text or omitted.
- No per-row "last updated" timestamp — only a layer-level
  `editingInfo.dataLastEditDate`, which tells you when *any* row in
  the whole layer last changed, not which rows.

This is consistent with the platform's stated safety posture (see
[`product.md`](../product.md)): this source simply does not carry
person-level suspect/arrest data for us to (mis)use, which removes an
entire category of risk by construction rather than by our own
filtering discipline.

---

## 3. Geographic precision

**Confirmed:** point geometry (`GEO_LAT`/`GEO_LON`) is present on all
but 1 of 378,097 main-layer rows. That near-universal presence is
**not** the same as "exact incident location," and Denver's own data
shows why:

- `INCIDENT_ADDRESS` takes three distinct forms, confirmed by sampling:
  - an **exact street address** (e.g. `"1612 WAZEE ST"`, `"1505 S COLORADO BLVD"`),
  - a **block-range address** (e.g. `"1400 BLK N JASMINE ST"`, `"00 BLK N ZENOBIA ST"`),
  - an **intersection** (e.g. `"E COLFAX AVE / N TAMARAC ST"`).
- Measuring the block+intersection ("generalized") share by category
  (via `GROUP BY`/`LIKE` counts against the live API) shows it is
  **not evenly distributed** — person-crime categories are generalized
  noticeably more often than property-crime categories:

  | Category | Block-range | Intersection | Generalized total |
  |---|---|---|---|
  | `larceny` | 31% | 1% | 32% |
  | `robbery` | 9% | 16% | 25% |
  | `other-crimes-against-persons` | 25% | 11% | 36% |
  | `murder` | 29% | 17% | 46% |
  | `aggravated-assault` | 34% | 16% | 50% |

  This is an **empirical pattern observed in a live sample**, not a
  documented Denver policy — no published methodology describing a
  formal rule (e.g. "always generalize category X") was found. Treat
  the *existence* of category-dependent generalization as confirmed,
  and the exact rule governing it as unconfirmed.
- When `INCIDENT_ADDRESS` is a block range or intersection, it was
  **not separately confirmed** whether `GEO_LAT`/`GEO_LON` is (a) the
  true incident coordinate with only the *displayed text* generalized,
  or (b) a coordinate snapped to the block centroid / intersection
  point. The parallel structure (an intersection-looking address next
  to a single lat/lon pair) suggests (b) for at least some rows, but
  this needs direct confirmation before any "exact address proximity"
  feature relies on it. **Do not assume `GEO_LAT`/`GEO_LON` is exact
  just because it is numeric.**
- **Sexual assault is not merely generalized — it is fully suppressed
  in the primary layer.** The `sexual-assault` category does not
  appear in the main `Crime` dataset at all. It only appears in the
  companion **"Crime - Sex Related Crimes (aggregated)"** dataset,
  where, confirmed by direct sampling:
  - `INCIDENT_ADDRESS`, `GEO_X`, `GEO_Y`, `GEO_LON`, `GEO_LAT` are all
    `null`, and
  - `incident_id` / `offense_id` are zeroed out (`0`),
  - but `NEIGHBORHOOD_ID` and `DISTRICT_ID`/`PRECINCT_ID` **are still
    populated**.

  This matches the dataset's own description text: *"Certain
  information is omitted, in accordance with legal requirements and
  as described more fully in this Disclaimer."* This is exactly the
  kind of area-level-only, privacy-preserving pattern this platform's
  own safety principles call for (see
  [`product.md`](../product.md)) — Denver has effectively already done
  this suppression for us for this one category, which is a point in
  its favor.

**Suitability by intended use** (reasoned from the above, not
independently load-tested):

| Use case | Assessment |
|---|---|
| Neighborhood-level trends | **Good** — `NEIGHBORHOOD_ID` present on ~99.9% of rows. |
| Hex/grid aggregation at a coarse resolution (e.g. quarter-mile+ cells) | **Good** — block/intersection generalization is small relative to a coarse cell. |
| Half-mile radius search | **Moderate** — usable, but for person-crime categories a meaningful fraction (25–50% in the samples above) of points may be positioned at a block or intersection rather than the true location, which can shift a result across a half-mile boundary. |
| Exact address-proximity search | **Poor for a meaningful subset of rows** — not appropriate to present as ground-truth proximity for generalized rows, and not possible at all for `sexual-assault` (no coordinates published). |

---

## 4. Historical coverage and freshness

- **Window:** Denver's own description states the dataset covers *"the
  previous five calendar years plus the current year to date."*
  Confirmed empirically: as queried on 2026-09-06, the earliest
  `FIRST_OCCURRENCE_DATE` across all 378,097 rows is exactly
  `2021-01-01T00:00:00Z` and the latest is `2026-09-03T23:27:00Z`.
- **This is a rolling window, not a fixed archive.** Every January 1st
  the oldest calendar year drops off. **Confirmed implication:** if we
  want a durable multi-year history, our own ingestion must capture
  and retain snapshots over time — Denver's API itself will not retain
  them. This directly supports the "immutable raw snapshot" principle
  already in [`architecture.md`](../architecture.md).
- No official archive of pre-2021 Denver crime data was found via this
  source. (A pre-2021 archive may exist elsewhere — e.g. via a public
  records request, or on OpenColorado — but this was not verified in
  this phase and is not treated as part of this source.)
- **Record count:** 378,097 offense-level rows in the current window
  (a live count, will keep growing/rolling).
- **Update frequency:** Confirmed "updated Monday through Friday" (no
  weekend updates) both from the published description and from the
  latest-record timestamp being consistent with recent weekday
  activity.
- **Reporting delay:** Denver's own description states: *"Crimes that
  occurred at least 30 days ago tend to be the most accurate, although
  records are returned for incidents that happened yesterday... content
  provided here today will probably differ from content provided a
  week from now."* One sampled `sexual-assault` record had a
  `first_occurrence_date` of `3/17/2023` and a `reported_date` of
  `9/9/2024` — an 18-month reporting delay, plausible for this offense
  type and directly illustrating the point.
- **Records can change, and can be deleted:** Denver's own description
  states the data *"is dynamic, which allows for additions, deletions
  and/or modifications at any time."* This is a direct, confirmed
  statement from the publisher — not an inference. **Architectural
  implication (flagged, not solved):** since there is no per-row
  "last updated" timestamp, a future ingestion pipeline cannot cheaply
  detect *which* rows changed since the last pull; it would need to
  diff full pulls (by `OFFENSE_ID`, or a row hash) to detect
  updates and, notably, **deletions** (a row present in a prior pull
  but absent from the current one).

---

## 5. Terms, licensing, and commercial use

**`UNRESOLVED — requires further legal/terms review`**

Two different, seemingly independent statements of terms were found,
and neither one squarely answers the reuse/commercial-use question:

1. **Dataset-level "USE CONSTRAINTS"** (from the ArcGIS item's own
   `licenseInfo`, which is what's surfaced as `"license"` in the
   DCAT feed for this dataset):

   > "USE CONSTRAINTS — The City and County of Denver is not
   > responsible and shall not be liable to any user or recipient for
   > damages of any kind arising out of the use of data or information
   > provided by the City and County of Denver... ANY DATA OR
   > INFORMATION PROVIDED... IS PROVIDED AS IS WITHOUT WARRANTY OF ANY
   > KIND... NOT FOR ENGINEERING PURPOSES."

   This is a warranty/liability disclaimer. It does **not** explicitly
   grant or forbid reuse, redistribution, or commercial use, and does
   not mention attribution.

2. **Denver's general site Terms of Use** (`denvergov.org/Terms-of-Use`,
   which is *not* dataset-specific):

   > "\[Users may not] (1) distribute the text or graphics to others
   > without the express written permission of the City and County of
   > Denver; (2) mirror or copy this information to another server
   > without permission; or (3) modify or re-use the text or graphics
   > on this system." ... "Users may print copies of the information
   > for personal use and reference for their own documents."

   This reads as restrictive and is written for the website's general
   text/graphics, not clearly for the Open Data Catalog's structured
   datasets. It does not reference the Open Data Catalog, and it is
   unclear whether it is meant to govern dataset downloads at all.

**What we did *not* find:** no CC0, Open Database License, Open
Government Licence, or public-domain declaration anywhere in the
researched pages; no explicit commercial-use permission or
prohibition tied to the dataset; no stated attribution requirement for
the dataset itself; no published rate limit; no stated bulk-download
restriction (and Denver *does* provide first-party bulk CSV/GeoJSON/
Shapefile/KML export buttons for this exact dataset, which is at least
a practical signal that bulk use is anticipated, though not a legal
permission).

**Per instructions, we are not inferring permission from public
accessibility.** Before this dataset is used for anything beyond this
kind of research/schema-inspection, we should either (a) get direct,
written confirmation from Denver's Open Data/GIS team on
reuse/commercial-use/redistribution/attribution, or (b) get a legal
review of the two texts above and how they interact. Note: the DCAT
feed's `contactPoint.hasEmail` field for this dataset was an
unpopulated template placeholder (`"{{orgContactEmail}}"`), so a real
contact channel still needs to be found (e.g. via `denvergov.org`)
rather than assumed.

**API-specific / access notes** (factual, not legal):

- No registration or API key required; confirmed by successful
  anonymous HTTPS requests throughout this investigation.
- The REST query endpoint enforces `maxRecordCount: 2000` rows per
  request (a technical constraint — see §9).
- The bulk CSV/GeoJSON/Shapefile/KML download endpoints are not
  subject to that 2,000-row cap.

---

## 6. Crime taxonomy

- **Not raw NIBRS or UCR codes.** Denver publishes its own local
  Records Management System offense coding: a 4-digit `OFFENSE_CODE`
  plus a numeric `OFFENSE_CODE_EXTENSION`, layered under two
  human-readable normalization levels Denver itself maintains:
  `OFFENSE_TYPE_ID` (173 distinct values observed) rolling up into
  `OFFENSE_CATEGORY_ID` (13 values in the main layer; 14 including
  `sexual-assault`, which only appears in the companion table).
- Denver's own description says the data is *"based on"* NIBRS in the
  sense that it reports **all victims and all offenses within an
  incident** (a NIBRS concept — the older UCR "hierarchy rule" would
  have counted only the single most serious offense per incident).
  This is consistent with, and explains, the confirmed one-incident-
  many-offenses pattern in §9. It is **not** the same as saying the
  published `OFFENSE_CODE` values are standard NIBRS codes — they are
  not.
- **Representative categories/types** (from a live `GROUP BY` count,
  most frequent first — 25 shown, well above the requested 10–20):

  | Offense code | Category | Type | Row count |
  |---|---|---|---|
  | 2404 | auto-theft | theft-of-motor-vehicle | 55,401 |
  | 2305 | theft-from-motor-vehicle | theft-items-from-vehicle | 38,324 |
  | 2999 | public-disorder | criminal-mischief-mtr-veh | 28,738 |
  | 2399 | larceny | theft-other | 24,921 |
  | 2304 | theft-from-motor-vehicle | theft-parts-from-vehicle | 24,149 |
  | 2999 | public-disorder | criminal-mischief-other | 16,263 |
  | 2303 | larceny | theft-shoplift | 15,008 |
  | 1313 | other-crimes-against-persons | assault-simple | 14,787 |
  | 5707 | all-other-crimes | criminal-trespassing | 14,153 |
  | 2308 | larceny | theft-from-bldg | 9,579 |
  | 2203 | burglary | burglary-business-by-force | 8,631 |
  | 2399 | larceny | theft-bicycle | 8,107 |
  | 5213 | all-other-crimes | weapon-unlawful-discharge-of | 7,886 |
  | 3550 | drug-alcohol | drug-poss-paraphernalia | 7,592 |
  | 2204 | burglary | burglary-residence-no-force | 7,361 |
  | 1315 | aggravated-assault | aggravated-assault | 7,122 |
  | 1316 | public-disorder | threats-to-injure | 5,972 |
  | 3599 | drug-alcohol | drug-pcs-other-drug | 5,641 |
  | 2202 | burglary | burglary-residence-by-force | 5,315 |
  | 1315 | aggravated-assault | menacing-felony-w-weap | 5,315 |
  | 5016 | all-other-crimes | violation-of-court-order | 3,966 |
  | 7399 | all-other-crimes | public-order-crimes-other | 3,213 |
  | 5016 | all-other-crimes | violation-of-restraining-order | 3,197 |
  | 5312 | public-disorder | disturbing-the-peace | 3,161 |
  | 1205 | robbery | robbery-street | 2,883 |
  | *(companion table only)* | sexual-assault | sex-aslt-rape / sex-aslt-non-rape | 4,066 total |

  Category totals across all 378,097 main-layer rows: `theft-from-
  motor-vehicle` 62,473 · `larceny` 60,793 · `public-disorder` 59,620 ·
  `auto-theft` 56,376 · `all-other-crimes` 46,183 · `burglary` 27,046 ·
  `drug-alcohol` 19,776 · `other-crimes-against-persons` 16,323 ·
  `aggravated-assault` 14,931 · `white-collar-crime` 6,967 ·
  `robbery` 6,391 · `arson` 861 · `murder` 357.

- **Mapping feasibility to our future normalized taxonomy** (assessed,
  not implemented — per instructions):
  - Clean, largely 1:1 category-level mapping: `murder`→homicide,
    `robbery`→robbery, `burglary`→burglary, `auto-theft`→motor vehicle
    theft, `theft-from-motor-vehicle`→vehicle break-in,
    `white-collar-crime`→fraud, `drug-alcohol`→drug offense (note: this
    Denver category also folds in alcohol offenses, which our
    taxonomy may want to separate).
  - Needs `OFFENSE_TYPE_ID`-level (not just category-level) mapping,
    because Denver's own categories don't line up 1:1 with ours:
    - Our "assault" bucket would need to draw from **both**
      `aggravated-assault` (a whole category) and specific types like
      `assault-simple` / `menacing-felony-w-weap` that Denver files
      under `other-crimes-against-persons`.
    - Our "vandalism" bucket would need to pull specific
      `criminal-mischief-*` types out of the broader `public-disorder`
      category, rather than mapping the whole category.
    - Our "weapons offense" bucket is scattered — e.g.
      `weapon-unlawful-discharge-of` lives under `all-other-crimes`.
    - "Public-order offense" would combine most of `public-disorder`
      with stray `all-other-crimes` types like
      `public-order-crimes-other`.
  - "Sexual offense" would require separately ingesting the
    location/identifier-suppressed companion dataset, with its own
    handling — it cannot be joined back to the main incident stream by
    `INCIDENT_ID`/`OFFENSE_ID` since those are zeroed there.
  - **Not verified in this phase:** whether Denver has retired,
    renamed, or redefined any `OFFENSE_CODE`/`OFFENSE_TYPE_ID` values
    within the current 5+ year window. A static mapping table built
    from today's snapshot could silently miss older or renamed codes.

---

## 7. Suitability assessment for V1

| Dimension | Rating | Why |
|---|---|---|
| Data availability | **Good** | First-party structured REST API + bulk CSV/GeoJSON/Shapefile/KML exports, no auth required, near-complete geometry and neighborhood coverage, near-daily updates. |
| Geographic usefulness | **Moderate** | Near-universal lat/lon presence supports neighborhood- and hex-level work well, but block/intersection address generalization on a meaningful share of person-crime rows (and full suppression for sexual assault) makes it unsuitable to present as exact-address precision, especially for radius/proximity search. |
| Historical usefulness | **Moderate** | Good depth for recent trend analysis (5+ years), but it is a rolling window with no confirmed older archive from this source — we would need to start capturing snapshots ourselves to build durable long-run history. |
| Update frequency | **Good** | Confirmed weekday updates, current data, and the publisher explicitly documents how/why data changes and becomes more accurate over time. |
| Taxonomy quality | **Good** | Clean two-level, human-readable taxonomy (173 types → 13–14 categories); mapping to our normalized categories is feasible but will require type-level (not just category-level) rules in a few places, and separate handling for the sexual-assault companion dataset. |
| Licensing confidence | **Unresolved** | No explicit reuse/commercial-use/redistribution/attribution terms found at either the dataset or general-site level; the general site's terms read restrictively but don't clearly address the Open Data Catalog. Requires direct outreach or legal review before relying on it beyond research. |
| **Overall V1 suitability** | **Recommended with caveats** | Strongest, clearly first-party, technically solid candidate found. The blocking item before real ingestion is licensing confirmation, not data quality — data quality is good enough for V1's stated scope (map, filters, basic trends) provided we're honest in the UI about generalized/suppressed locations and about which offense category (sexual assault) isn't present in the point-level feed at all. |

---

## 8. Open questions carried into Phase 2 — resolution status

See §9 of the original request for the full checklist. **Phase 2**
([`denver-ingestion-design.md`](./denver-ingestion-design.md))
investigated each of these; status updated here:

1. **Licensing** — still `UNRESOLVED` after a further, deliberate
   check (Denver's Executive Order No. 143 read directly and ruled
   out as irrelevant; no open-data ordinance/policy found). See
   `denver-ingestion-design.md` §10.
2. **Timezone** — **resolved at the service-declaration level.** The
   layer's own metadata states
   `dateFieldsTimeReference: {timeZone: "UTC", respectsDaylightSaving: false}`,
   and this was independently cross-checked by comparing the same
   record's timestamp via two separately-generated delivery paths
   (raw epoch query vs. bulk CSV export), which agree exactly. Whether
   DPD's *original* data entry was truly in UTC (vs. mislabeled local
   time) remains unresolvable from outside. See
   `denver-ingestion-design.md` §3 for the full treatment and the
   recommended storage strategy.
3. **Offense-vs-incident key** — **resolved empirically.** A full
   census (378,097 rows) found 378,097 distinct `OFFENSE_ID` values,
   0 nulls, 0 duplicates, and confirmed structurally: no incident ever
   logs the same offense code+extension twice. Still not a
   publisher-documented guarantee — see
   `denver-ingestion-design.md` §1 for the recommended safeguards.
4. **Row-level change detection** — **characterized in full.** Native
   ArcGIS change-tracking is confirmed unavailable (no `GlobalID`
   field, no `ChangeTracking` capability). A full-reconciliation +
   fingerprinting strategy is recommended instead. See
   `denver-ingestion-design.md` §4.
5. **Coordinate meaning for generalized addresses** — still
   unresolved; not re-tested in Phase 2.
6. **Historical schema/taxonomy drift** — **investigated.** The
   code→type→category mapping is stable within the live window (zero
   recategorizations found across a full census), but the *set* of
   active codes does drift — offense code `2299`
   (`burglary-other`) had 0 occurrences in 2021–2022 and now accounts
   for hundreds of rows per year since 2024. See
   `denver-ingestion-design.md` §8 for the full evidence, including
   why most other "year-only" code patterns turned out to be
   low-frequency noise rather than genuine drift.
7. **Pagination** — **confirmed and characterized.** Plain attribute
   queries are hard-capped at 2,000 rows/request regardless of what's
   requested; `returnIdsOnly` uniquely bypasses this and returns the
   full ~378K-id list in one request; the bulk export endpoints are
   likewise uncapped and are the recommended path for a full pull. See
   `denver-ingestion-design.md` §9.
8. **`sexual-assault` ingestion path** — still open; not re-investigated
   in Phase 2 (no ingestion was built).

---

## Queries used (for reproducibility)

All against public, unauthenticated endpoints, in September 2026:

- DCAT catalog: `GET https://opendata-geospatialdenver.hub.arcgis.com/api/feed/dcat-us/1.1.json`
- ArcGIS item metadata: `GET https://www.arcgis.com/sharing/rest/content/items/16d9c82bb36c4475bf87189cfaed653c?f=json`
- Layer schema: `GET https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/arcgis/rest/services/ODC_CRIME_OFFENSES_P/FeatureServer/324?f=json`
- Sample rows: `GET .../FeatureServer/324/query?where=1=1&outFields=*&resultRecordCount=5&orderByFields=REPORTED_DATE DESC&f=json`
- Aggregate counts: `GET .../FeatureServer/324/query?where=...&outStatistics=[{"statisticType":"count",...}]&groupByFieldsForStatistics=...&f=json`
- Companion (sex-related) layer schema/samples: same pattern against
  `https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/arcgis/rest/services/ODC_crimes_incl_sex/FeatureServer/42`
