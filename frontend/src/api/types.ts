// Mirrors backend/app/schemas/*.py -- see docs/map-aggregation.md and
// docs/operations/chicago-ingestion.md for the endpoints these serve.

export type LocationPrecision =
  "exact" | "approximate" | "block" | "intersection" | "suppressed" | "unknown";

export interface OffenseSummary {
  id: string;
  external_offense_id: string;
  raw_offense_code: string | null;
  fbi_code: string | null;
  source_category: string | null;
  source_subcategory: string | null;
  victim_count: number | null;
}

export interface Incident {
  id: string;
  source_id: string;
  external_incident_id: string;
  occurred_at: string | null;
  reported_at: string | null;
  latitude: number | null;
  longitude: number | null;
  location_precision: LocationPrecision;
  address_text: string | null;
  neighborhood: string | null;
  district: string | null;
  beat: string | null;
  categories: string[];
  offenses: OffenseSummary[];
}

export interface IncidentListResponse {
  items: Incident[];
  total: number;
  limit: number;
  offset: number;
}

export interface GridCell {
  lon: number;
  lat: number;
  count: number;
}

export interface IncidentAggregateResponse {
  cells: GridCell[];
  grid_degrees: number;
}

export interface DatasetStatusResponse {
  city: string;
  source_name: string;
  publisher: string | null;
  source_url: string | null;
  attribution: string;
  is_population_complete: boolean;
  latest_successful_ingestion_at: string | null;
  earliest_occurred_date: string | null;
  latest_occurred_date: string | null;
  incident_count: number;
  offense_count: number;
}

export interface CategoryBreakdownItem {
  category: string | null;
  count: number;
}

export interface TimeBucketItem {
  bucket_start: string;
  count: number;
  // True when the source's real data coverage ends before this
  // bucket's natural week/month span -- only ever set on the last item.
  is_partial: boolean;
}

export interface NeighborhoodItem {
  neighborhood: string | null;
  // Official Chicago community area name for `neighborhood`'s code --
  // null when `neighborhood` is null or isn't a recognized code.
  name: string | null;
  count: number;
}

export interface SummaryResponse {
  start_date: string;
  end_date: string;
  reported_incidents: number;
  previous_period_start_date: string;
  previous_period_end_date: string;
  previous_period_reported_incidents: number;
  percent_change: number | null;
  // True when a requested/defaulted end_date reached past the source's
  // real data coverage and was pulled back to it -- `end_date` above is
  // already the effective (clamped) date that was actually analyzed.
  end_date_clamped: boolean;
  category_breakdown: CategoryBreakdownItem[];
  most_common_category: string | null;
  time_bucket: "week" | "month";
  incidents_by_time: TimeBucketItem[];
  top_neighborhoods: NeighborhoodItem[];
  most_represented_neighborhood: string | null;
  // Official name for `most_represented_neighborhood`'s code -- added
  // alongside the existing code field; null when there's no
  // most-represented neighborhood or its code isn't recognized.
  most_represented_neighborhood_name: string | null;
}

export interface BoundingBox {
  minLon: number;
  minLat: number;
  maxLon: number;
  maxLat: number;
}

export interface IncidentFilters {
  startDate?: string;
  endDate?: string;
  category?: string;
  neighborhood?: string;
}

export interface CategoryListResponse {
  categories: string[];
}
