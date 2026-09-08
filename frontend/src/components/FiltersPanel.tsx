import type { FilterState } from "../hooks/useFilterState";

interface FiltersPanelProps {
  filters: FilterState;
  onChange: (update: Partial<FilterState>) => void;
  onClear: () => void;
  categoryOptions: string[];
  neighborhoodOptions: string[];
}

export default function FiltersPanel({
  filters,
  onChange,
  onClear,
  categoryOptions,
  neighborhoodOptions,
}: FiltersPanelProps) {
  const hasActiveFilters = Boolean(
    filters.category || filters.neighborhood || filters.from || filters.to,
  );

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
            value={filters.from ?? ""}
            onChange={(e) => onChange({ from: e.target.value || undefined })}
          />
        </div>

        <div className="filters-panel__field">
          <label htmlFor="filter-to">To</label>
          <input
            id="filter-to"
            type="date"
            value={filters.to ?? ""}
            onChange={(e) => onChange({ to: e.target.value || undefined })}
          />
        </div>

        <div className="filters-panel__field">
          <label htmlFor="filter-category">Category</label>
          <input
            id="filter-category"
            type="text"
            list="category-options"
            placeholder="e.g. THEFT"
            value={filters.category ?? ""}
            onChange={(e) => onChange({ category: e.target.value || undefined })}
          />
          <datalist id="category-options">
            {categoryOptions.map((c) => (
              <option key={c} value={c} />
            ))}
          </datalist>
        </div>

        <div className="filters-panel__field">
          <label htmlFor="filter-neighborhood">
            Neighborhood <span className="filters-panel__hint">(community area code)</span>
          </label>
          <input
            id="filter-neighborhood"
            type="text"
            list="neighborhood-options"
            placeholder="e.g. 25"
            value={filters.neighborhood ?? ""}
            onChange={(e) => onChange({ neighborhood: e.target.value || undefined })}
          />
          <datalist id="neighborhood-options">
            {neighborhoodOptions.map((n) => (
              <option key={n} value={n} />
            ))}
          </datalist>
        </div>

        <button type="button" onClick={onClear} disabled={!hasActiveFilters}>
          Clear filters
        </button>
      </form>
    </details>
  );
}
