import type {
  BoundingBox,
  DatasetStatusResponse,
  IncidentAggregateResponse,
  IncidentFilters,
  IncidentListResponse,
  SummaryResponse,
} from "./types";

// "localhost" and "127.0.0.1" are not interchangeable on every machine --
// on this project's dev setup, "localhost:8000" resolves to an unrelated
// service, while the Crime Tracker backend is only reachable via the
// explicit IPv4 loopback address. Always use 127.0.0.1 here.
const API_BASE_URL: string = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function getJson<T>(
  path: string,
  params: Record<string, string | number | undefined>,
): Promise<T> {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== "") {
      search.set(key, String(value));
    }
  }
  const query = search.toString();
  const url = `${API_BASE_URL}${path}${query ? `?${query}` : ""}`;

  const response = await fetch(url);
  if (!response.ok) {
    throw new ApiError(`Request to ${path} failed with status ${response.status}`, response.status);
  }
  return response.json() as Promise<T>;
}

export function fetchStatus(): Promise<DatasetStatusResponse> {
  return getJson<DatasetStatusResponse>("/api/status", {});
}

export interface FetchIncidentsOptions extends IncidentFilters {
  bbox?: BoundingBox;
  limit?: number;
  offset?: number;
}

export function fetchIncidents(options: FetchIncidentsOptions = {}): Promise<IncidentListResponse> {
  return getJson<IncidentListResponse>("/api/incidents", {
    start_date: options.startDate,
    end_date: options.endDate,
    category: options.category,
    neighborhood: options.neighborhood,
    min_lon: options.bbox?.minLon,
    min_lat: options.bbox?.minLat,
    max_lon: options.bbox?.maxLon,
    max_lat: options.bbox?.maxLat,
    limit: options.limit,
    offset: options.offset,
  });
}

export interface FetchAggregateOptions extends IncidentFilters {
  bbox?: BoundingBox;
  zoom: number;
}

export function fetchAggregate(options: FetchAggregateOptions): Promise<IncidentAggregateResponse> {
  return getJson<IncidentAggregateResponse>("/api/incidents/aggregate", {
    zoom: options.zoom,
    start_date: options.startDate,
    end_date: options.endDate,
    category: options.category,
    neighborhood: options.neighborhood,
    min_lon: options.bbox?.minLon,
    min_lat: options.bbox?.minLat,
    max_lon: options.bbox?.maxLon,
    max_lat: options.bbox?.maxLat,
  });
}

export function fetchSummary(options: IncidentFilters = {}): Promise<SummaryResponse> {
  return getJson<SummaryResponse>("/api/summary", {
    start_date: options.startDate,
    end_date: options.endDate,
    category: options.category,
    neighborhood: options.neighborhood,
  });
}
