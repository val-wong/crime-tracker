# Data Source Evaluation Template

This document is a **template** for evaluating candidate city data
sources before integrating them. It intentionally contains no
populated entries yet — no city's data source has been evaluated or
integrated as of this bootstrap phase. Do not fill this in with
unverified claims; every field should be backed by a link or document
that can be checked.

Copy the block below into a new section per city under consideration.

## Template

```
### <City Name>

- **City**:
- **Official source URL**:
- **Publisher**: (e.g., city police department, open-data office)
- **Format**: (CSV, JSON, GeoJSON, Socrata API, ArcGIS REST, etc.)
- **API availability**: (yes/no; auth required?; rate limits noted separately below)
- **Update frequency**: (e.g., daily, weekly, real-time — as documented by the source)
- **Geographic fields provided**: (lat/long, address, block-level, district, neighborhood, etc.)
- **Historical coverage**: (date range available)
- **License / terms of use**: (link + summary)
- **Commercial-use restrictions**: (any, per the license)
- **Rate limits**: (requests per minute/day, if an API)
- **Known caveats**: (e.g., address block-level rounding for privacy, delayed reporting, known data-quality issues documented by the source)
- **Schema notes**: (source field names and types, mapped later to the normalized model in architecture.md)
- **Status**: `not evaluated` | `under review` | `approved` | `integrated` | `rejected`
```

## Evaluation Notes

- Every claim in a populated entry should be traceable to the source's
  own documentation (link it) rather than assumed.
- "Status" should be updated as review progresses; a source should not
  move to `integrated` until ingestion has been reviewed against this
  platform's safety and provenance requirements (see
  [`product.md`](./product.md) and [`architecture.md`](./architecture.md)).

## Evaluated Sources

### Denver, CO

Full investigation: [`sources/denver.md`](./sources/denver.md).
Ingestion technical design (OFFENSE_ID uniqueness, timezone semantics,
change detection, schema/taxonomy drift, proposed architecture):
[`sources/denver-ingestion-design.md`](./sources/denver-ingestion-design.md).
This is a concise registry summary only — see those documents for
evidence, sampled data, and reasoning behind every claim below.

- **City**: Denver, CO
- **Official source URL**: <https://opendata-geospatialdenver.hub.arcgis.com/datasets/geospatialDenver::crime>
- **Publisher**: City and County of Denver — Denver Police Department / Data Analysis Unit (confirmed first-party; ArcGIS Online owner account `The_City_and_County_of_Denver`)
- **Format**: Esri GeoServices REST API (hosted feature service); bulk CSV, GeoJSON, Shapefile, and KML exports also provided
- **API availability**: Yes — `https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/arcgis/rest/services/ODC_CRIME_OFFENSES_P/FeatureServer/324`; public, no registration or API key required
- **Update frequency**: Confirmed "updated Monday through Friday" (publisher's own description); no weekend updates
- **Geographic fields provided**: `GEO_LAT`/`GEO_LON` (WGS84), `GEO_X`/`GEO_Y` (state-plane), `INCIDENT_ADDRESS` (exact / block-range / intersection — varies by row), `NEIGHBORHOOD_ID`, `DISTRICT_ID`, `PRECINCT_ID`. See `sources/denver.md` §3 for confirmed precision caveats — coordinates are not reliably exact for a meaningful share of person-crime rows, and are entirely absent for the `sexual-assault` category.
- **Historical coverage**: Rolling window — "previous five calendar years plus current year to date" per publisher; confirmed earliest record 2021-01-01, latest current as of query date. No older archive found via this source.
- **License / terms of use**: `UNRESOLVED — requires further legal/terms review`. Dataset-level text is a liability/warranty disclaimer only (no explicit reuse/commercial-use grant); the general `denvergov.org` site terms read restrictively but don't clearly address the Open Data Catalog. See `sources/denver.md` §5 for both quoted texts.
- **Commercial-use restrictions**: Not confirmed either way — see License above. Do not treat public accessibility as permission.
- **Rate limits**: None published; the REST query endpoint enforces a technical `maxRecordCount` of 2,000 rows per request (pagination, not a legal limit).
- **Known caveats**: One incident can produce multiple offense rows (`INCIDENT_ID` is not unique per row — confirmed 94.2% of incidents have exactly one offense, up to 8 observed on one incident); records can be added, modified, or deleted by the publisher at any time with no per-row "last updated" field, and native ArcGIS change-tracking is confirmed unavailable (no GlobalID field); date fields are declared UTC by the service's own metadata (DST-unaware), though upstream data-entry correctness can't be verified from outside; `OFFENSE_ID` is empirically 100% unique across a full census (378,097/378,097) but not a publisher-documented guarantee; `sexual-assault` offenses are excluded from this dataset entirely (see the companion dataset below). Full list in `sources/denver.md` §9 and `sources/denver-ingestion-design.md`.
- **Schema notes**: See `sources/denver.md` §2 for the full field table and confirmed gaps (no victim detail beyond an aggregate count, no suspect/arrest/disposition data, no `location_type` field).
- **Status**: `under review`

### Denver, CO — Sex-Related Crimes (companion dataset)

- **City**: Denver, CO
- **Official source URL**: <https://opendata-geospatialdenver.hub.arcgis.com/datasets/geospatialDenver::crime-sex-related-crimes-aggregated>
- **Publisher**: City and County of Denver (same org as above)
- **Format**: Esri GeoServices REST API (non-spatial table) — `https://services1.arcgis.com/zdB7qR0BtYrg0Xpl/arcgis/rest/services/ODC_crimes_incl_sex/FeatureServer/42`
- **API availability**: Yes; public, no registration required
- **Update frequency**: Same publisher description as the main Crime dataset (weekday updates)
- **Geographic fields provided**: `NEIGHBORHOOD_ID`, `DISTRICT_ID`, `PRECINCT_ID` only for `sexual-assault` rows — confirmed `INCIDENT_ADDRESS`, `GEO_LAT`/`GEO_LON`, `GEO_X`/`GEO_Y` are all null for that category (a deliberate, confirmed privacy suppression)
- **Historical coverage**: Same rolling window as the main dataset
- **License / terms of use**: `UNRESOLVED` — same as above
- **Commercial-use restrictions**: Not confirmed — see License above
- **Rate limits**: Same technical pagination cap as the main dataset
- **Known caveats**: `incident_id`/`offense_id` are zeroed for all rows in this table, so it cannot be joined back to the main dataset by identifier; would need its own ingestion/normalization path
- **Schema notes**: Same schema as the main dataset, minus point geometry, plus the `sexual-assault` category
- **Status**: `under review`

### Chicago, IL

Full investigation: [`sources/chicago.md`](./sources/chicago.md)
(deep-dive validation) and
[`sources/city-comparison.md`](./sources/city-comparison.md) §1
(initial screening). Ingestion design:
[`sources/chicago-ingestion-design.md`](./sources/chicago-ingestion-design.md).

- **City**: Chicago, IL
- **Official source URL**: <https://data.cityofchicago.org/Public-Safety/Crimes-2001-to-Present/ijzp-q8t2>
- **Publisher**: Chicago Police Department, via the Chicago Data Portal (Socrata)
- **Format**: Socrata SODA API + bulk CSV/JSON/GeoJSON export
- **API availability**: Yes; public, no registration or API key required (an app token is recommended but not required — see caveats)
- **Update frequency**: Daily (publisher's own description; confirmed via live `updated_on` values)
- **Geographic fields provided**: `latitude`/`longitude`, `block` — confirmed always shifted (deliberately, per the publisher's own field description) to block level, applied uniformly to 100% of rows, not category-dependent; `community_area` (numeric), `district`, `ward`, `beat`
- **Historical coverage**: Confirmed `2001-01-01` to date minus the most recent 7 days — a genuine ~25-year archive, not a rolling window
- **License / terms of use**: `CLEAR` — reconfirmed by re-fetching the portal's Terms of Use (chicago.gov) fresh in this phase; text unchanged. An explicit "USE OF DATA" clause authorizes derivative/redistributed applications (commercial use not excluded), conditioned on including a specified disclaimer sentence. The City retains an at-will right to demand a user stop.
- **Commercial-use restrictions**: None found; see caveat above
- **Rate limits**: None published; anonymous (no-app-token) requests are throttled from a shared IP pool per Socrata's own documentation, with no numeric guarantee — a production adapter should obtain a free app token
- **Known caveats**: `case_number` is not perfectly unique — confirmed 629 duplicates across a full census of 8.6M rows, fully explained by a publisher-documented policy (one row per victim for homicides, not corrections or offense multiplicity); Socrata's own metadata designates `id` (not `case_number`) as the dataset's official row identifier — use it as `external_record_id`. `updated_on` is a genuine row-level timestamp (unique among cities investigated) but a confirmed bulk-touch event (706 rows, 2 distinct timestamps, spanning a 25-year-old record) shows it can advance without the substantive data changing — fingerprinting is still required, `updated_on` is only a cheap candidate filter. **A real, publicly-documented incident (Feb. 4, 2016) shows the publisher's own pipeline can silently truncate this dataset to near-empty** while still returning a technically successful API response — this platform's `is_complete` flag alone does not protect against a source-side collapse like this; see `sources/chicago-ingestion-design.md` §5 for a proposed plausibility-check safeguard.
- **Schema notes**: See `sources/chicago.md` §1, §6 for the full field table and schema-drift findings, including a maintained IUCR reference table with an authoritative `active`/inactive flag (10 of 434 codes currently inactive)
- **Status**: `under review`

### New York City, NY

Full investigation: [`sources/city-comparison.md`](./sources/city-comparison.md) §2.

- **City**: New York City, NY
- **Official source URL**: <https://data.cityofnewyork.us/Public-Safety/NYPD-Complaint-Data-Historic/qgea-i56i> (historic) and <https://data.cityofnewyork.us/Public-Safety/NYPD-Complaint-Data-Current-Year-To-Date-/5uac-w243> (current YTD)
- **Publisher**: NYPD, via NYC Open Data
- **Format**: Socrata SODA API + bulk export
- **API availability**: Yes; public, no registration required
- **Update frequency**: Current-year dataset updates quarterly (confirmed via date range); historic dataset updates annually
- **Geographic fields provided**: `latitude`/`longitude` (explicitly labeled "Midblock" — always generalized), `boro_nm`, `addr_pct_cd`; no finer neighborhood field than borough
- **Historical coverage**: Confirmed 2006 to end of last complete year (historic, ~10M rows) + current year to date (~280K rows); raw min-date queries returned obvious placeholder/sentinel dates (e.g. year 1010) needing filtering
- **License / terms of use**: `CLEAR` — governed by codified NYC Administrative Code §23-502(d), read directly: "Public data sets shall be made available without any registration requirement, license requirement or restrictions on their use," with only a light source/version attribution condition for republishers. This is a law, not just a portal policy.
- **Commercial-use restrictions**: None — explicitly barred by the same codified provision
- **Rate limits**: None published
- **Known caveats**: No separate offense-level identifier found (closer to one offense per complaint than full NIBRS granularity); no row-level update timestamp; two physical datasets would need adapter-level merging; taxonomy uses NYPD-internal codes with no explicit NIBRS/UCR label found
- **Schema notes**: See `sources/city-comparison.md` §2 for the full field table
- **Status**: `under review`

### Seattle, WA

Full investigation: [`sources/city-comparison.md`](./sources/city-comparison.md) §4.

- **City**: Seattle, WA
- **Official source URL**: <https://data.seattle.gov/Public-Safety/SPD-Crime-Data-2008-Present/tazs-3rd5>
- **Publisher**: Seattle Police Department, via the Seattle Open Data portal
- **Format**: Socrata SODA API + bulk export
- **API availability**: Yes; public, no registration required
- **Update frequency**: Daily (publisher's own description)
- **Geographic fields provided**: `latitude`/`longitude` (typed as text; confirmed always "blurred to the one hundred block"), `block_address`, `neighborhood` (genuine named field), `precinct`, `sector`, `beat`
- **Historical coverage**: Confirmed 2008 to date (~1.56M rows); a small number of rows (888, ~0.06%) carry an obvious placeholder date (1900) needing filtering
- **License / terms of use**: `CLEAR` — dataset-level Socrata metadata states `license: Public Domain` explicitly; consistent with Seattle's site-wide Open Data Policy
- **Commercial-use restrictions**: None found
- **Rate limits**: None published
- **Known caveats**: Genuine incident/offense split via `report_number`/`offense_id` (same shape as Denver); no row-level update timestamp; top-level `offense_category` is very coarse (only 3 values) — finer taxonomy work needs `offense_sub_category`/NIBRS fields
- **Schema notes**: See `sources/city-comparison.md` §4 for the full field table
- **Status**: `under review`

### San Francisco, CA

Full investigation: [`sources/city-comparison.md`](./sources/city-comparison.md) §5.

- **City**: San Francisco, CA
- **Official source URL**: <https://data.sfgov.org/Public-Safety/Police-Department-Incident-Reports-2018-to-Present/wg3w-h783>
- **Publisher**: San Francisco Police Department, via DataSF
- **Format**: Socrata SODA API + bulk export
- **API availability**: Yes; public, no registration required
- **Update frequency**: Event-driven (added once reviewed/approved by a supervisor); observed to be effectively live
- **Geographic fields provided**: `latitude`/`longitude`/`point`, `intersection` (confirmed ~94.6% of rows generalized to the nearest intersection — the most aggressive generalization of the cities investigated); `analysis_neighborhood` (genuine named field), `police_district`
- **Historical coverage**: Confirmed 2018-01-01 to date (~1.06M rows); an older 2003–2018 dataset exists separately, not investigated in depth
- **License / terms of use**: `CLEAR` — dataset-level Socrata metadata names the Open Data Commons Public Domain Dedication and License (PDDL) explicitly, consistent with SF's Chapter 22D open-data ordinance
- **Commercial-use restrictions**: None found
- **Rate limits**: None published
- **Known caveats**: `incident_id`/`incident_number` relationship is not a clean incident/offense split — `incident_number` appears to group report-*filing events* (initial/supplemental/vehicle) as much as distinct offenses, needing adapter-level judgment; the publisher has disclosed a geocoding-methodology change around April 2024 affecting cross-date comparability; no true row-level edit timestamp (`data_loaded_at` is portal-ingestion time only)
- **Schema notes**: See `sources/city-comparison.md` §5 for the full field table
- **Status**: `under review`

### Los Angeles, CA

Full investigation: [`sources/city-comparison.md`](./sources/city-comparison.md) §3.

- **City**: Los Angeles, CA
- **Official source URL**: <https://data.lacity.org/Public-Safety/LAPD-NIBRS-Offenses-Dataset/k7nn-b2ep>
- **Publisher**: Los Angeles Police Department, via the LA Open Data Portal
- **Format**: Socrata SODA API + bulk export
- **API availability**: Yes; public, no registration required
- **Update frequency**: Bi-weekly (publisher's own description) — the least frequent of the cities investigated
- **Geographic fields provided**: `hndrdth_lat`/`hndrdth_lon`/`hndrdth_loc_chk` — field names themselves confirm rounding to the nearest hundred block, applied uniformly
- **Historical coverage**: Publisher states data "from any date" entered into the new records-management system (live since March 2024); confirmed empirically that 98.8% of rows post-date 2024-01-01, with a small fraction of placeholder/garbage dates (including an 1800 sentinel) — **real usable depth is only ~2 years**, the shallowest of the cities investigated
- **License / terms of use**: `UNRESOLVED` — the portal's Terms of Use and the city's 2013 Open Data Executive Directive No. 3 (both read directly) contain a liability disclaimer and a mandate to publish, but no explicit reuse/commercial-use/redistribution grant, structurally the same gap as Denver's
- **Commercial-use restrictions**: Not confirmed either way — see License above; do not treat public accessibility as permission
- **Rate limits**: None published
- **Known caveats**: `uniquenibrno` (CaseNo + NIBR code + sequence) is structurally identical to Denver's `OFFENSE_ID`; genuinely the most standards-compliant NIBRS taxonomy of any city investigated; a companion Victims dataset exists (same pattern as Denver's sex-crime table); no row-level update timestamp
- **Schema notes**: See `sources/city-comparison.md` §3 for the full field table
- **Status**: `under review` — **not recommended** as a V1 fallback due to unresolved licensing and shallow historical depth, despite the strongest technical/taxonomy fit of any city investigated

No city, including Denver, has been marked `integrated`. Integrating a
first city is explicitly a separate, future phase from this
investigation. See `sources/denver.md` §7 for Denver's own suitability
assessment, and
[`sources/city-comparison.md`](./sources/city-comparison.md) for the
comparative scoring, architecture-compatibility check, and fallback
recommendation (Chicago primary, New York City secondary) across the
five alternate cities investigated in this phase.
