import { lazy, Suspense, useEffect, useState } from "react";

import { fetchStatus, fetchSummary } from "./api/client";
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

  const categoryOptions =
    summary?.category_breakdown.map((c) => c.category).filter((c): c is string => Boolean(c)) ?? [];
  const neighborhoodOptions =
    summary?.top_neighborhoods.map((n) => n.neighborhood).filter((n): n is string => Boolean(n)) ??
    [];

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
            filters={filters}
            onChange={setFilters}
            onClear={clearFilters}
            categoryOptions={categoryOptions}
            neighborhoodOptions={neighborhoodOptions}
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
