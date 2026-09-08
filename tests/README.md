# Tests (Cross-Cutting)

Unit tests live alongside the code they test:

- Backend unit tests: `backend/tests/`
- Frontend unit tests: `frontend/src/**/*.test.tsx`

This top-level `tests/` directory is reserved for tests that span more
than one component — e.g., a future end-to-end test that exercises
ingestion, the API, and the frontend together, or a contract test
between `scripts/ingest/` output and the backend's normalization step.

Nothing lives here yet in this bootstrap phase, since there is no
cross-component behavior to test until ingestion (a future phase) is
integrated.
