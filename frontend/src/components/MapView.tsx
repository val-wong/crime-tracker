import type { FeatureCollection } from "geojson";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef, useState } from "react";

import { ApiError, fetchAggregate, fetchIncidents } from "../api/client";
import type { BoundingBox, GridCell, Incident, IncidentFilters } from "../api/types";

// Open, no-signup vector basemap -- see docs/map-aggregation.md and
// README for why MapLibre + this tile source was chosen over a
// commercial provider. Requires the "OpenStreetMap contributors /
// CARTO" attribution, which MapLibre's default AttributionControl
// renders automatically from the style's own metadata.
const MAP_STYLE_URL = "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json";

const CHICAGO_CENTER: [number, number] = [-87.6298, 41.8781];
const DEFAULT_ZOOM = 10;

// See docs/map-aggregation.md "Zoom -> grid size mapping": at this
// zoom and above, individual incidents are usually sparse enough to
// render one-by-one and a click needs a specific incident anyway.
const INDIVIDUAL_INCIDENT_MIN_ZOOM = 15;
const MOVE_DEBOUNCE_MS = 300;

type LoadState = "idle" | "loading" | "loaded" | "error";

interface MapViewProps {
  filters: IncidentFilters;
  onSelectIncident: (incident: Incident) => void;
}

function boundsToBbox(bounds: maplibregl.LngLatBounds): BoundingBox {
  return {
    minLon: bounds.getWest(),
    minLat: bounds.getSouth(),
    maxLon: bounds.getEast(),
    maxLat: bounds.getNorth(),
  };
}

export default function MapView({ filters, onSelectIncident }: MapViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibregl.Map | null>(null);
  const debounceRef = useRef<number | undefined>(undefined);
  const filtersRef = useRef(filters);
  filtersRef.current = filters;

  const [loadState, setLoadState] = useState<LoadState>("idle");
  const [mode, setMode] = useState<"aggregate" | "incidents">("aggregate");
  const [visibleCount, setVisibleCount] = useState(0);

  useEffect(() => {
    if (!containerRef.current) return undefined;

    const map = new maplibregl.Map({
      container: containerRef.current,
      style: MAP_STYLE_URL,
      center: CHICAGO_CENTER,
      zoom: DEFAULT_ZOOM,
      attributionControl: { compact: true },
    });
    mapRef.current = map;
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");

    map.on("load", () => {
      map.addSource("aggregate-cells", { type: "geojson", data: emptyFeatureCollection() });
      map.addLayer({
        id: "aggregate-cells-layer",
        type: "circle",
        source: "aggregate-cells",
        // Breakpoints span the real, measured citywide (0.05deg grid)
        // cell-count distribution at full Chicago scale (~8.6M
        // incidents): min=32, median=66,885, p90=438,539, max=741,867.
        // Real browser QA on this distribution found the *previous*
        // tuning (radius topping out at 36px, only two stops above
        // 50,000) visually dominated the map: since the median already
        // sits just above that 50,000 stop, most visible dense cells in
        // a typical view landed within a few px of the 36px ceiling and
        // read as "everything is maxed out." This tuning lowers the
        // ceiling (36 -> 21px, roughly a 40% reduction in on-screen
        // area) and adds stops at 150,000/350,000 -- both inside the
        // real p90-to-max band -- so cells across that band stay
        // visually distinguishable instead of clustering at one size.
        // Color keeps the same sequential yellow -> deep-red ramp
        // (monotonic in both lightness and size, never the sole
        // encoding of magnitude) with matching intermediate stops.
        paint: {
          "circle-radius": [
            "interpolate",
            ["linear"],
            ["get", "count"],
            1,
            3,
            50,
            5,
            500,
            7,
            5000,
            9,
            50000,
            12,
            150000,
            15,
            350000,
            18,
            700000,
            21,
          ],
          "circle-color": [
            "interpolate",
            ["linear"],
            ["get", "count"],
            1,
            "#fde68a",
            50,
            "#fdba74",
            500,
            "#fb923c",
            5000,
            "#f59e0b",
            50000,
            "#ea580c",
            150000,
            "#c2410c",
            350000,
            "#9a3412",
            700000,
            "#7f1d1d",
          ],
          "circle-opacity": 0.68,
          "circle-stroke-width": 1,
          "circle-stroke-color": "#78350f",
        },
      });

      map.addSource("incident-points", { type: "geojson", data: emptyFeatureCollection() });
      map.addLayer({
        id: "incident-points-layer",
        type: "circle",
        source: "incident-points",
        paint: {
          "circle-radius": 6,
          "circle-color": "#b91c1c",
          "circle-opacity": 0.85,
          "circle-stroke-width": 1,
          "circle-stroke-color": "#ffffff",
        },
      });

      map.on("click", "incident-points-layer", (e) => {
        const feature = e.features?.[0];
        if (!feature?.properties?.raw) return;
        onSelectIncident(JSON.parse(feature.properties.raw as string) as Incident);
      });
      map.on(
        "mouseenter",
        "incident-points-layer",
        () => (map.getCanvas().style.cursor = "pointer"),
      );
      map.on("mouseleave", "incident-points-layer", () => (map.getCanvas().style.cursor = ""));

      loadViewportData(map);
    });

    map.on("moveend", () => {
      window.clearTimeout(debounceRef.current);
      debounceRef.current = window.setTimeout(() => loadViewportData(map), MOVE_DEBOUNCE_MS);
    });

    async function loadViewportData(map: maplibregl.Map) {
      const zoom = map.getZoom();
      const bbox = boundsToBbox(map.getBounds());
      const currentFilters = filtersRef.current;
      setLoadState("loading");

      try {
        if (zoom >= INDIVIDUAL_INCIDENT_MIN_ZOOM) {
          const result = await fetchIncidents({
            bbox,
            startDate: currentFilters.startDate,
            endDate: currentFilters.endDate,
            category: currentFilters.category,
            neighborhood: currentFilters.neighborhood,
            limit: 500,
          });
          const source = map.getSource("incident-points") as maplibregl.GeoJSONSource | undefined;
          source?.setData(incidentsToFeatureCollection(result.items));
          (map.getSource("aggregate-cells") as maplibregl.GeoJSONSource | undefined)?.setData(
            emptyFeatureCollection(),
          );
          // mode and visibleCount are set together, only once the new
          // data has actually arrived -- setting `mode` eagerly before
          // the fetch resolved was confirmed via browser QA to cause a
          // real, misleading bug: the caption's template would already
          // say "individual reported incidents" while `visibleCount`
          // still held a leftover citywide aggregate sum (e.g. showing
          // "162,047 individual reported incidents" for what was
          // actually a capped 500-row page), for as long as the new
          // request was in flight.
          setMode("incidents");
          setVisibleCount(result.items.length);
        } else {
          const result = await fetchAggregate({
            bbox,
            zoom: Math.round(zoom),
            startDate: currentFilters.startDate,
            endDate: currentFilters.endDate,
            category: currentFilters.category,
            neighborhood: currentFilters.neighborhood,
          });
          const source = map.getSource("aggregate-cells") as maplibregl.GeoJSONSource | undefined;
          source?.setData(cellsToFeatureCollection(result.cells));
          (map.getSource("incident-points") as maplibregl.GeoJSONSource | undefined)?.setData(
            emptyFeatureCollection(),
          );
          setMode("aggregate");
          setVisibleCount(result.cells.reduce((sum, c) => sum + c.count, 0));
        }
        setLoadState("loaded");
      } catch (err) {
        setLoadState("error");
        console.error("Failed to load map data", err instanceof ApiError ? err.message : err);
      }
    }

    // Re-fetch whenever filters change, without waiting for a map move.
    (map as unknown as { __reload?: () => void }).__reload = () => loadViewportData(map);

    return () => {
      window.clearTimeout(debounceRef.current);
      map.remove();
      mapRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    const reload = (map as unknown as { __reload?: () => void } | null)?.__reload;
    if (map && reload && map.isStyleLoaded()) {
      reload();
    }
  }, [filters]);

  return (
    <section className="panel map-panel" aria-label="Incident map">
      <h2 className="visually-hidden">Incident map</h2>
      {/* MapLibre manages its own canvas's keyboard interaction and
          ARIA attributes -- no role/aria-label added here to avoid
          conflicting with that (the wrapping <section>'s aria-label
          above already identifies this region). */}
      <div ref={containerRef} className="map-panel__map" />
      <p className="map-panel__caption" aria-live="polite">
        {loadState === "loading" && "Loading map data…"}
        {loadState === "error" && "Could not load map data. Try adjusting filters or reloading."}
        {loadState === "loaded" &&
          (mode === "aggregate"
            ? `Showing ${visibleCount.toLocaleString()} reported incidents grouped into map areas at this zoom level.`
            : `Showing ${visibleCount.toLocaleString()} individual reported incidents in this view. Click a point for details.`)}
      </p>
      <p className="map-panel__note">
        Locations are generalized to the block by the source — map pins are never the exact location
        of an incident.
      </p>
    </section>
  );
}

function emptyFeatureCollection(): FeatureCollection {
  return { type: "FeatureCollection", features: [] };
}

function cellsToFeatureCollection(cells: GridCell[]): FeatureCollection {
  return {
    type: "FeatureCollection",
    features: cells.map((cell) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [cell.lon, cell.lat] },
      properties: { count: cell.count },
    })),
  };
}

function incidentsToFeatureCollection(incidents: Incident[]): FeatureCollection {
  return {
    type: "FeatureCollection",
    features: incidents
      .filter((incident) => incident.longitude !== null && incident.latitude !== null)
      .map((incident) => ({
        type: "Feature",
        geometry: {
          type: "Point",
          coordinates: [incident.longitude as number, incident.latitude as number],
        },
        properties: { id: incident.id, raw: JSON.stringify(incident) },
      })),
  };
}
