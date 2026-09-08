import { useCallback, useMemo, useState } from "react";

// Keeps the dashboard's filters in the URL (e.g.
// ?category=THEFT&neighborhood=25&from=2026-08-01&to=2026-09-01) so a
// refresh or a shared link reproduces the same view -- see
// docs/product.md V1 scope. Deliberately a small hand-rolled hook
// rather than a routing library: this app has exactly one view, so
// there's nothing for a router to route between (see docs/product.md
// "Design restraint").

export interface FilterState {
  category?: string;
  neighborhood?: string;
  from?: string;
  to?: string;
}

const PARAM_KEYS: Record<keyof FilterState, string> = {
  category: "category",
  neighborhood: "neighborhood",
  from: "from",
  to: "to",
};

function readFromUrl(): FilterState {
  const params = new URLSearchParams(window.location.search);
  const state: FilterState = {};
  for (const key of Object.keys(PARAM_KEYS) as (keyof FilterState)[]) {
    const value = params.get(PARAM_KEYS[key]);
    if (value) state[key] = value;
  }
  return state;
}

function writeToUrl(state: FilterState) {
  const params = new URLSearchParams(window.location.search);
  for (const key of Object.keys(PARAM_KEYS) as (keyof FilterState)[]) {
    const paramKey = PARAM_KEYS[key];
    const value = state[key];
    if (value) {
      params.set(paramKey, value);
    } else {
      params.delete(paramKey);
    }
  }
  const query = params.toString();
  const newUrl = `${window.location.pathname}${query ? `?${query}` : ""}`;
  window.history.replaceState(null, "", newUrl);
}

export function useFilterState(): [
  FilterState,
  (update: Partial<FilterState>) => void,
  () => void,
] {
  const [filters, setFiltersState] = useState<FilterState>(() => readFromUrl());

  const setFilters = useCallback((update: Partial<FilterState>) => {
    setFiltersState((prev) => {
      const next = { ...prev, ...update };
      writeToUrl(next);
      return next;
    });
  }, []);

  const clearFilters = useCallback(() => {
    setFiltersState({});
    writeToUrl({});
  }, []);

  return useMemo(() => [filters, setFilters, clearFilters], [filters, setFilters, clearFilters]);
}
