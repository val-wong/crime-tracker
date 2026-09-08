# Product Overview

## Purpose

CrimeSignal is a public-interest platform for exploring **aggregate,
area-level crime patterns** using official public crime-incident data
published by cities. It exists to help residents, journalists,
researchers, and local policymakers understand *what kinds of incidents
are happening, where, and when* — using transparent, explainable,
historical data.

CrimeSignal is **not** a predictive-policing or surveillance tool. It
does not attempt to identify, profile, or score individuals. See
"Safety & Ethics Constraints" below for hard boundaries that apply to
every phase of this project.

## Initial Target User

- A resident or community member who wants to understand recent crime
  patterns in their neighborhood.
- A local journalist or researcher who wants to explore trends across
  categories, time, and geography.
- A civic technologist evaluating the platform as a foundation for
  further public-interest analysis.

Law enforcement operational use, individual risk scoring, or any use
that informs decisions about specific people is explicitly out of
scope (see Non-Goals).

## V1 Scope

V1 is the first data-carrying release, built on top of this bootstrap
phase. It should:

1. Ingest one city's official public crime dataset.
2. Preserve the raw records exactly as received, with source
   provenance intact.
3. Normalize records into the internal incident schema (see
   [`architecture.md`](./architecture.md)).
4. Expose normalized incidents through a read API.
5. Display incidents on a map.
6. Support filtering by date, category, location, and time.
7. Show basic historical trends (e.g., counts over time, by category).

## Explicit Non-Goals

The following are **out of scope for V1** and are not being designed
toward in this bootstrap phase:

- Individual offender profiling.
- Facial recognition or any biometric identification.
- Person-level risk scores of any kind.
- Claims that a specific crime will occur at a specific place or time.
- Opaque machine-learning predictions presented without explanation.
- Forecasting or predictive-policing features of any kind (deferred
  indefinitely; see Future Phases for the narrow, explainable
  alternative that may eventually be considered).
- Authentication/authorization, cloud infrastructure, and CI/CD (all
  deferred beyond what is trivially useful for this phase).

## Safety & Ethics Constraints

These constraints are durable and apply to every future phase, not
just V1:

- **No individual targeting.** The platform must never attempt to
  predict which individual will commit a crime, identify likely
  offenders, or produce any output keyed to a specific person.
- **Aggregate only.** All analysis operates on aggregated incident
  data at the level of geography (e.g., neighborhood, district) and
  category (e.g., offense type) — never at the level of an individual
  or household.
- **Explainability first.** Any future risk or trend indicator must be
  traceable to the underlying historical counts and methodology that
  produced it. No black-box scoring.
- **Provenance preserved.** Raw source data is retained unmodified
  alongside normalized data so that any downstream statistic can be
  traced back to its original record and source.
- **Transparency about limitations.** Crime data reflects reported and
  recorded incidents, not the true rate of crime, and is shaped by
  reporting behavior, policing practices, and data-quality differences
  across cities. The product should communicate these limitations
  rather than imply false precision.

## Future Phases

Roughly in order, each contingent on review of the prior phase:

1. **Bootstrap (this phase).** Repository scaffolding, architecture
   docs, and a minimal runnable skeleton. No real data source
   integration yet.
2. **Single-city ingestion (V1).** Ingest, normalize, and serve one
   city's public dataset as described above.
3. **Multi-source normalization.** Extend ingestion and the normalized
   schema to additional cities, hardening the mapping between varied
   source schemas and the internal model.
4. **Trend detection.** Area-level and category-level historical trend
   analysis (e.g., moving averages, year-over-year comparisons)
   presented with clear methodology.
5. **Explainable, aggregate risk indicators.** Area-level and
   category-level statistical indicators derived from aggregate
   historical patterns, published with their methodology and
   limitations. These indicators must remain explainable and must
   never resolve to an individual person.

Forecasting and predictive-policing features are explicitly deferred
and are not assumed to be built even in later phases without separate,
deliberate review of the ethical and legal implications.
