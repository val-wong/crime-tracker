import { useEffect, useState } from "react";

import { COMMUNITY_AREA_OPTIONS } from "../data/chicagoCommunityAreas";
import type { FilterState } from "../hooks/useFilterState";
import { formatCalendarDate } from "../utils/formatCalendarDate";
import { type DateBounds, validateDateRange } from "../utils/validateDateRange";

interface FiltersPanelProps {
  // The committed (URL-backed) filters -- what data is actually fetched
  // against. Edits to the form below are held as local "draft" state
  // and only reach this until "Apply filters" is pressed, so typing in
  // a field never triggers a fetch by itself (see useFilterState.ts and
  // App.tsx for how committed filters drive requests).
  committedFilters: FilterState;
  onApply: (filters: FilterState) => void;
  onClear: () => void;
  categoryOptions: string[];
  dateBounds: DateBounds;
  // True while a request driven by the committed filters is in flight --
  // disables Apply so repeated clicks can't pile up redundant requests.
  applyDisabled?: boolean;
  // The effective start/end date actually analyzed when no explicit
  // date filter is committed (the backend's own default period, see
  // app/services/summary.py::_default_period) -- shown as a caption
  // rather than pre-filled into the date inputs, since pre-filling
  // would misrepresent an implicit default as a user-chosen value.
  effectivePeriod?: { start: string; end: string } | null;
}

export default function FiltersPanel({
  committedFilters,
  onApply,
  onClear,
  categoryOptions,
  dateBounds,
  applyDisabled = false,
  effectivePeriod = null,
}: FiltersPanelProps) {
  const [draft, setDraft] = useState<FilterState>(committedFilters);
  const [error, setError] = useState<string | null>(null);

  // Committed filters only ever change here via Apply/Clear, but
  // syncing on every change (rather than only setting initial state)
  // also correctly seeds the draft from a shared URL on first load.
  useEffect(() => {
    setDraft(committedFilters);
  }, [committedFilters]);

  const hasActiveFilters = Boolean(
    draft.category ||
    draft.neighborhood ||
    draft.from ||
    draft.to ||
    committedFilters.category ||
    committedFilters.neighborhood ||
    committedFilters.from ||
    committedFilters.to,
  );

  const hasUnrecognizedNeighborhood = Boolean(
    draft.neighborhood &&
    !COMMUNITY_AREA_OPTIONS.some((option) => option.code === draft.neighborhood),
  );
  const hasUnrecognizedCategory = Boolean(
    draft.category && !categoryOptions.includes(draft.category),
  );

  function updateDraft(update: Partial<FilterState>) {
    setDraft((prev) => ({ ...prev, ...update }));
  }

  function handleApply() {
    const validationError = validateDateRange(draft.from, draft.to, dateBounds);
    if (validationError) {
      setError(validationError);
      return;
    }
    setError(null);
    onApply(draft);
  }

  function handleClear() {
    setError(null);
    setDraft({});
    onClear();
  }

  return (
    <details className="panel filters-panel" open>
      <summary className="filters-panel__summary">
        <h2 className="filters-panel__heading">Filters</h2>
      </summary>

      <form className="filters-panel__form" aria-label="Filter reported incidents">
        <div className="filters-panel__field">
          <label htmlFor="filter-from">From</label>
          <input
            id="filter-from"
            type="date"
            value={draft.from ?? ""}
            min={dateBounds.min ?? undefined}
            max={draft.to || dateBounds.max || undefined}
            onChange={(e) => updateDraft({ from: e.target.value || undefined })}
          />
        </div>

        <div className="filters-panel__field">
          <label htmlFor="filter-to">To</label>
          <input
            id="filter-to"
            type="date"
            value={draft.to ?? ""}
            min={draft.from || dateBounds.min || undefined}
            max={dateBounds.max ?? undefined}
            onChange={(e) => updateDraft({ to: e.target.value || undefined })}
          />
        </div>

        {!draft.from && !draft.to && effectivePeriod && (
          <p className="filters-panel__note">
            No date filter applied — showing the default recent period:{" "}
            {formatCalendarDate(effectivePeriod.start)} – {formatCalendarDate(effectivePeriod.end)}.
          </p>
        )}

        {dateBounds.min && dateBounds.max && (
          <p className="filters-panel__hint">
            Data available {formatCalendarDate(dateBounds.min)} –{" "}
            {formatCalendarDate(dateBounds.max)}
          </p>
        )}

        {error && (
          <p className="filters-panel__error" role="alert">
            {error}
          </p>
        )}

        <div className="filters-panel__field">
          <label htmlFor="filter-category">Category</label>
          <select
            id="filter-category"
            value={draft.category ?? ""}
            onChange={(e) => updateDraft({ category: e.target.value || undefined })}
          >
            <option value="">All categories</option>
            {hasUnrecognizedCategory && <option value={draft.category}>{draft.category}</option>}
            {categoryOptions.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </div>

        <div className="filters-panel__field">
          <label htmlFor="filter-neighborhood">Neighborhood</label>
          <select
            id="filter-neighborhood"
            value={draft.neighborhood ?? ""}
            onChange={(e) => updateDraft({ neighborhood: e.target.value || undefined })}
          >
            <option value="">All neighborhoods</option>
            {hasUnrecognizedNeighborhood && (
              <option value={draft.neighborhood}>Unknown ({draft.neighborhood})</option>
            )}
            {COMMUNITY_AREA_OPTIONS.map(({ code, name }) => (
              <option key={code} value={code}>
                {name}
              </option>
            ))}
          </select>
        </div>

        <div className="filters-panel__actions">
          <button
            type="button"
            className="filters-panel__apply"
            onClick={handleApply}
            disabled={applyDisabled}
          >
            Apply filters
          </button>
          <button type="button" onClick={handleClear} disabled={!hasActiveFilters}>
            Clear filters
          </button>
        </div>
      </form>
    </details>
  );
}
