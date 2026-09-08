import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import type { DatasetStatusResponse, Incident, SummaryResponse } from "./api/types";

// maplibre-gl needs a real WebGL canvas, which jsdom doesn't provide.
// The map itself is exercised by MapView's own concerns (bbox math,
// zoom->grid mapping tested at the backend) -- here we stub it out so
// App-level tests can focus on data loading, filters, and the
// incident-detail flow. The stub exposes a button that simulates a map
// click selecting an incident, so the App-level selection flow is
// still covered.
vi.mock("./components/MapView", () => ({
  default: ({ onSelectIncident }: { onSelectIncident: (incident: Incident) => void }) => (
    <div data-testid="map-stub">
      <button
        type="button"
        onClick={() =>
          onSelectIncident({
            id: "incident-1",
            source_id: "source-1",
            external_incident_id: "JX000001",
            occurred_at: "2026-01-01T12:00:00Z",
            reported_at: null,
            latitude: 41.88,
            longitude: -87.63,
            location_precision: "block",
            address_text: "001XX N STATE ST",
            neighborhood: "32",
            district: "001",
            beat: "0111",
            categories: ["THEFT"],
            offenses: [
              {
                id: "offense-1",
                external_offense_id: "1",
                raw_offense_code: "0810",
                fbi_code: "06",
                source_category: "THEFT",
                source_subcategory: "OVER $500",
                victim_count: null,
              },
            ],
          })
        }
      >
        Simulate map click
      </button>
    </div>
  ),
}));

const FULL_STATUS: DatasetStatusResponse = {
  city: "Chicago",
  source_name: "City of Chicago - Crimes - 2001 to Present",
  publisher: "Chicago Police Department",
  source_url: "https://data.cityofchicago.org",
  attribution: "This site provides applications using data...",
  is_population_complete: true,
  latest_successful_ingestion_at: "2026-09-01T00:00:00Z",
  earliest_occurred_date: "2001-01-01",
  latest_occurred_date: "2026-08-31",
  incident_count: 8000000,
  offense_count: 8600000,
};

const PARTIAL_STATUS: DatasetStatusResponse = {
  ...FULL_STATUS,
  is_population_complete: false,
  incident_count: 500000,
  offense_count: 500000,
};

const SUMMARY: SummaryResponse = {
  start_date: "2026-08-01",
  end_date: "2026-08-31",
  reported_incidents: 1200,
  previous_period_start_date: "2026-07-01",
  previous_period_end_date: "2026-07-31",
  previous_period_reported_incidents: 1000,
  percent_change: 20,
  end_date_clamped: false,
  category_breakdown: [
    { category: "THEFT", count: 500 },
    { category: "BATTERY", count: 300 },
  ],
  most_common_category: "THEFT",
  time_bucket: "week",
  incidents_by_time: [
    { bucket_start: "2026-08-01", count: 300, is_partial: false },
    { bucket_start: "2026-08-08", count: 400, is_partial: false },
  ],
  top_neighborhoods: [{ neighborhood: "25", count: 200 }],
  most_represented_neighborhood: "25",
};

function mockFetchSequence(
  statusResponse: DatasetStatusResponse,
  summaryResponse: SummaryResponse | null,
) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url.includes("/api/status")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(statusResponse) });
      }
      if (url.includes("/api/summary")) {
        if (summaryResponse === null) {
          return Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) });
        }
        return Promise.resolve({ ok: true, json: () => Promise.resolve(summaryResponse) });
      }
      return Promise.reject(new Error(`Unexpected fetch to ${url}`));
    }),
  );
}

describe("App", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.history.replaceState(null, "", "/");
  });

  it("renders the header, map, filters, and data caveat section", async () => {
    mockFetchSequence(FULL_STATUS, SUMMARY);
    render(<App />);

    expect(screen.getByRole("heading", { name: "Crime Tracker" })).toBeInTheDocument();
    expect(await screen.findByTestId("map-stub")).toBeInTheDocument();
    expect(screen.getByLabelText("Filter reported incidents")).toBeInTheDocument();
    expect(screen.getByText("About this data")).toBeInTheDocument();

    await waitFor(() => expect(screen.getByText("1,200")).toBeInTheDocument());
  });

  it("shows a loading state before summary data arrives", () => {
    mockFetchSequence(FULL_STATUS, SUMMARY);
    render(<App />);

    expect(screen.getByText("Loading summary…")).toBeInTheDocument();
  });

  it("shows an error-tolerant state when the summary request fails", async () => {
    mockFetchSequence(FULL_STATUS, null);
    render(<App />);

    expect(await screen.findByText(/summary unavailable/i)).toBeInTheDocument();
    // Falls back to a real error state rather than either crashing or
    // showing "Loading summary…" forever (loading=false but summary=null
    // are two different states, both must be handled distinctly).
    expect(screen.queryByText("Loading summary…")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Crime Tracker" })).toBeInTheDocument();
  });

  it("shows the full-dataset status banner when population is complete", async () => {
    mockFetchSequence(FULL_STATUS, SUMMARY);
    render(<App />);

    expect(await screen.findByText("Full dataset")).toBeInTheDocument();
  });

  it("shows the partial-data warning when population is incomplete", async () => {
    mockFetchSequence(PARTIAL_STATUS, SUMMARY);
    render(<App />);

    expect(await screen.findByText("Partial development data")).toBeInTheDocument();

    const caveat = screen.getByText("About this data").closest("details");
    expect(caveat).not.toBeNull();
    expect(within(caveat as HTMLElement).getByText(/development notice/i)).toBeInTheDocument();
  });

  it("updates the URL when a filter changes and clears it on reset", async () => {
    mockFetchSequence(FULL_STATUS, SUMMARY);
    const user = userEvent.setup();
    render(<App />);

    const categoryInput = screen.getByLabelText("Category");
    await user.type(categoryInput, "THEFT");

    await waitFor(() => expect(window.location.search).toContain("category=THEFT"));

    const clearButton = screen.getByRole("button", { name: "Clear filters" });
    await user.click(clearButton);

    expect(window.location.search).toBe("");
    expect((categoryInput as HTMLInputElement).value).toBe("");
  });

  it("reads initial filters from the URL on load", () => {
    window.history.replaceState(null, "", "/?category=BATTERY&neighborhood=25");
    mockFetchSequence(FULL_STATUS, SUMMARY);
    render(<App />);

    expect((screen.getByLabelText("Category") as HTMLInputElement).value).toBe("BATTERY");
    expect((screen.getByLabelText(/Neighborhood/) as HTMLInputElement).value).toBe("25");
  });

  it("opens and closes the incident detail panel on selection", async () => {
    mockFetchSequence(FULL_STATUS, SUMMARY);
    const user = userEvent.setup();
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Simulate map click" }));

    const dialog = await screen.findByRole("dialog", { name: "Incident details" });
    expect(within(dialog).getByRole("heading", { name: "THEFT" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Close incident details" }));
    expect(screen.queryByRole("dialog", { name: "Incident details" })).not.toBeInTheDocument();
  });
});
