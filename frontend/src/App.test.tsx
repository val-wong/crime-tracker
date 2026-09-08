import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

// Deliberately does NOT match SUMMARY.category_breakdown below (which
// only has THEFT/BATTERY) -- the dropdown must reflect this complete
// list from /api/categories, not whatever happens to be in the
// currently-committed summary's (filter-scoped) breakdown.
const CATEGORIES = ["BATTERY", "MOTOR VEHICLE THEFT", "ROBBERY", "THEFT"];

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
  top_neighborhoods: [{ neighborhood: "25", name: "Austin", count: 200 }],
  most_represented_neighborhood: "25",
  most_represented_neighborhood_name: "Austin",
};

function mockFetchSequence(
  statusResponse: DatasetStatusResponse,
  summaryResponse: SummaryResponse | null,
  categories: string[] = CATEGORIES,
) {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string) => {
      if (url.includes("/api/status")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve(statusResponse) });
      }
      if (url.includes("/api/categories")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ categories }) });
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

    expect(screen.getByRole("heading", { name: "CrimeSignal" })).toBeInTheDocument();
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
    expect(screen.getByRole("heading", { name: "CrimeSignal" })).toBeInTheDocument();
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

  it("offers the complete category list from /api/categories, not just what's in the summary breakdown", async () => {
    mockFetchSequence(FULL_STATUS, SUMMARY);
    render(<App />);
    await waitFor(() => expect(screen.getByText("1,200")).toBeInTheDocument());

    for (const category of ["THEFT", "BATTERY", "ROBBERY", "MOTOR VEHICLE THEFT"]) {
      expect(screen.getByRole("option", { name: category })).toBeInTheDocument();
    }
  });

  it("keeps category options stable when date/neighborhood filters change the summary breakdown", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal(
      "fetch",
      fetchSpy.mockImplementation((url: string) => {
        if (url.includes("/api/status")) {
          return Promise.resolve({ ok: true, json: () => Promise.resolve(FULL_STATUS) });
        }
        if (url.includes("/api/categories")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ categories: CATEGORIES }),
          });
        }
        if (url.includes("/api/summary")) {
          // A different, narrower breakdown each time -- proves the
          // dropdown isn't reading from this.
          return Promise.resolve({
            ok: true,
            json: () =>
              Promise.resolve({
                ...SUMMARY,
                category_breakdown: [{ category: "ROBBERY", count: 1 }],
              }),
          });
        }
        return Promise.reject(new Error(`Unexpected fetch to ${url}`));
      }),
    );

    const user = userEvent.setup();
    render(<App />);
    await waitFor(() => expect(screen.getByText("1,200")).toBeInTheDocument());

    await user.selectOptions(screen.getByLabelText("Neighborhood"), "Austin");
    await user.click(screen.getByRole("button", { name: "Apply filters" }));
    await waitFor(() => expect(window.location.search).toContain("neighborhood=25"));

    for (const category of ["THEFT", "BATTERY", "ROBBERY", "MOTOR VEHICLE THEFT"]) {
      expect(screen.getByRole("option", { name: category })).toBeInTheDocument();
    }
    // /api/categories is fetched once, independent of committed filters.
    expect(fetchSpy.mock.calls.filter(([url]) => url.includes("/api/categories"))).toHaveLength(1);
  });

  it("selecting 'All categories' and applying clears the committed category filter", async () => {
    window.history.replaceState(null, "", "/?category=THEFT");
    mockFetchSequence(FULL_STATUS, SUMMARY);
    const user = userEvent.setup();
    render(<App />);
    await waitFor(() => expect(screen.getByText("1,200")).toBeInTheDocument());

    await user.selectOptions(screen.getByLabelText("Category"), "All categories");
    await user.click(screen.getByRole("button", { name: "Apply filters" }));

    await waitFor(() => expect(window.location.search).not.toContain("category"));
  });

  it("does not touch the URL while editing filters -- only Apply commits them", async () => {
    mockFetchSequence(FULL_STATUS, SUMMARY);
    const user = userEvent.setup();
    render(<App />);
    await waitFor(() => expect(screen.getByText("1,200")).toBeInTheDocument());

    await user.selectOptions(screen.getByLabelText("Category"), "THEFT");
    expect(window.location.search).toBe("");

    await user.click(screen.getByRole("button", { name: "Apply filters" }));
    await waitFor(() => expect(window.location.search).toContain("category=THEFT"));
  });

  it("Apply commits category, neighborhood, and dates to the URL using API values", async () => {
    mockFetchSequence(FULL_STATUS, SUMMARY);
    const user = userEvent.setup();
    render(<App />);
    await waitFor(() => expect(screen.getByText("1,200")).toBeInTheDocument());

    await user.selectOptions(screen.getByLabelText("Category"), "THEFT");
    await user.selectOptions(screen.getByLabelText("Neighborhood"), "Austin");
    fireEvent.change(screen.getByLabelText("From"), { target: { value: "2026-08-01" } });
    fireEvent.change(screen.getByLabelText("To"), { target: { value: "2026-08-29" } });
    await user.click(screen.getByRole("button", { name: "Apply filters" }));

    await waitFor(() => {
      const params = new URLSearchParams(window.location.search);
      expect(params.get("category")).toBe("THEFT");
      expect(params.get("neighborhood")).toBe("25");
      expect(params.get("from")).toBe("2026-08-01");
      expect(params.get("to")).toBe("2026-08-29");
    });
  });

  it("Clear resets the form and the committed URL filters", async () => {
    window.history.replaceState(null, "", "/?category=THEFT");
    mockFetchSequence(FULL_STATUS, SUMMARY);
    const user = userEvent.setup();
    render(<App />);
    await waitFor(() => expect(screen.getByText("1,200")).toBeInTheDocument());

    const clearButton = screen.getByRole("button", { name: "Clear filters" });
    await user.click(clearButton);

    expect(window.location.search).toBe("");
    expect((screen.getByLabelText("Category") as HTMLSelectElement).value).toBe("");
  });

  it("reads initial filters from the URL on load and restores them into the form", () => {
    window.history.replaceState(
      null,
      "",
      "/?category=BATTERY&neighborhood=25&from=2026-08-01&to=2026-08-29",
    );
    mockFetchSequence(FULL_STATUS, SUMMARY);
    render(<App />);

    expect((screen.getByLabelText("Category") as HTMLSelectElement).value).toBe("BATTERY");
    expect((screen.getByLabelText(/Neighborhood/) as HTMLSelectElement).value).toBe("25");
    expect((screen.getByLabelText("From") as HTMLInputElement).value).toBe("2026-08-01");
    expect((screen.getByLabelText("To") as HTMLInputElement).value).toBe("2026-08-29");
  });

  it("does not pre-fill date inputs with the default period, but does show it as a note", async () => {
    mockFetchSequence(FULL_STATUS, SUMMARY);
    render(<App />);
    await waitFor(() => expect(screen.getByText("1,200")).toBeInTheDocument());

    expect((screen.getByLabelText("From") as HTMLInputElement).value).toBe("");
    expect((screen.getByLabelText("To") as HTMLInputElement).value).toBe("");
    expect(screen.getByText(/default recent period/)).toBeInTheDocument();
  });

  it("constrains the date inputs to the dataset's own coverage", async () => {
    mockFetchSequence(FULL_STATUS, SUMMARY);
    render(<App />);
    await waitFor(() => expect(screen.getByText("1,200")).toBeInTheDocument());

    expect(screen.getByLabelText("From")).toHaveAttribute("min", "2001-01-01");
    expect(screen.getByLabelText("To")).toHaveAttribute("max", "2026-08-31");
  });

  it("rejects an out-of-coverage date range on Apply without changing the URL", async () => {
    mockFetchSequence(FULL_STATUS, SUMMARY);
    const user = userEvent.setup();
    render(<App />);
    await waitFor(() => expect(screen.getByText("1,200")).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText("To"), { target: { value: "2099-01-01" } });
    await user.click(screen.getByRole("button", { name: "Apply filters" }));

    expect(screen.getByRole("alert")).toHaveTextContent(/after the available data range/);
    expect(window.location.search).toBe("");
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
