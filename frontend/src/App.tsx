import { lazy, Suspense, useEffect, useState } from "react";

import { fetchCategories, fetchStatus, fetchSummary } from "./api/client";
import type { DatasetStatusResponse, Incident, SummaryResponse } from "./api/types";
import DataCaveatInfo from "./components/DataCaveatInfo";
import FiltersPanel from "./components/FiltersPanel";
import IncidentDetail from "./components/IncidentDetail";
import StatusBanner from "./components/StatusBanner";
import SummaryCards from "./components/SummaryCards";
import TrendsPanel from "./components/TrendsPanel";
import { useFilterState } from "./hooks/useFilterState";

// maplibre-gl is a large dependency (see the build's chunk-size
// warning) -- loaded only when the map panel actually mounts, so the
// rest of the dashboard (filters, summary, trends) is interactive
// sooner on a slow connection.
const MapView = lazy(() => import("./components/MapView"));

export default function App() {
  const [filters, setFilters, clearFilters] = useFilterState();
  const [summary, setSummary] = useState<SummaryResponse | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [status, setStatus] = useState<DatasetStatusResponse | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);
  const [categoryOptions, setCategoryOptions] = useState<string[]>([]);
  const [selectedIncident, setSelectedIncident] = useState<Incident | null>(null);

  const apiFilters = {
    startDate: filters.from,
    endDate: filters.to,
    category: filters.category,
    neighborhood: filters.neighborhood,
  };

  useEffect(() => {
    // Fetched once here and passed down to both StatusBanner and
    // DataCaveatInfo -- each fetching its own copy independently was
    // confirmed during browser QA to quadruple concurrent load on
    // /api/status under React StrictMode's double-invoked effects,
    // measurably compounding its latency for no benefit.
    fetchStatus()
      .then(setStatus)
      .catch(() => setStatus(null))
      .finally(() => setStatusLoading(false));
  }, []);

  useEffect(() => {
    // Fetched once, independent of any committed filter -- see
    // fetchCategories's own comment for why this must not be derived
    // from /api/summary's (filter-scoped) category_breakdown.
    fetchCategories()
      .then((data) => setCategoryOptions([...data.categories].sort()))
      .catch(() => setCategoryOptions([]));
  }, []);

  useEffect(() => {
    let cancelled = false;
    setSummaryLoading(true);
    fetchSummary(apiFilters)
      .then((data) => {
        if (!cancelled) setSummary(data);
      })
      .catch(() => {
        if (!cancelled) setSummary(null);
      })
      .finally(() => {
        if (!cancelled) setSummaryLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters.from, filters.to, filters.category, filters.neighborhood]);

  // When no explicit date filter is committed, the backend still
  // analyzes a default period anchored to its own latest loaded date
  // (see app/services/summary.py::_default_period) -- surfaced here so
  // the filter form can show that effective range without misleadingly
  // pre-filling the date inputs themselves (see FiltersPanel.tsx).
  const effectivePeriod =
    !filters.from && !filters.to && summary
      ? { start: summary.start_date, end: summary.end_date }
      : null;

  return (
    <div className="app">
      <header className="app__header">
        <div>
          <h1 className="app__title">Crime Tracker</h1>
          <p className="app__tagline">Chicago — reported incidents, not individual predictions</p>
        </div>
        <StatusBanner status={status} loading={statusLoading} />
      </header>

      <main className="app__layout">
        <div className="app__map-column">
          <Suspense fallback={<div className="panel map-panel__loading">Loading map…</div>}>
            <MapView filters={apiFilters} onSelectIncident={setSelectedIncident} />
          </Suspense>
        </div>

        <div className="app__side-column">
          <FiltersPanel
            committedFilters={filters}
            onApply={setFilters}
            onClear={clearFilters}
            categoryOptions={categoryOptions}
            dateBounds={{ min: status?.earliest_occurred_date, max: status?.latest_occurred_date }}
            applyDisabled={summaryLoading}
            effectivePeriod={effectivePeriod}
          />
          <SummaryCards summary={summary} loading={summaryLoading} />
        </div>
      </main>

      <TrendsPanel summary={summary} loading={summaryLoading} />

      <DataCaveatInfo status={status} />

      {selectedIncident && (
        <>
          <div className="incident-detail__backdrop" onClick={() => setSelectedIncident(null)} />
          <IncidentDetail incident={selectedIncident} onClose={() => setSelectedIncident(null)} />
        </>
      )}
    </div>
  );
}
