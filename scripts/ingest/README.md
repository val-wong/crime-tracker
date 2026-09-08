# Ingestion Scripts

This directory will hold per-source ingestion scripts — one per city
data source, each responsible only for fetching raw data from that
source and writing it, unmodified, into `data/raw/`.

No ingestion scripts exist yet. Integrating a first city's data source
is a separate, future phase (see [`docs/product.md`](../../docs/product.md)
and [`docs/data-sources.md`](../../docs/data-sources.md)); this
bootstrap phase only establishes the directory and its intended
contract:

- Each script should be scoped to a single source.
- Scripts should not transform, filter, or normalize data — that logic
  belongs downstream (see [`docs/architecture.md`](../../docs/architecture.md)).
- Scripts should record where and when data was fetched from,
  alongside the raw output.
