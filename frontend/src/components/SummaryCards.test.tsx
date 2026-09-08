import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { SummaryResponse } from "../api/types";
import SummaryCards from "./SummaryCards";

const BASE_SUMMARY: SummaryResponse = {
  start_date: "2026-08-01",
  end_date: "2026-08-31",
  reported_incidents: 1200,
  previous_period_start_date: "2026-07-01",
  previous_period_end_date: "2026-07-31",
  previous_period_reported_incidents: 1000,
  percent_change: 20,
  end_date_clamped: false,
  category_breakdown: [{ category: "THEFT", count: 500 }],
  most_common_category: "THEFT",
  time_bucket: "week",
  incidents_by_time: [],
  top_neighborhoods: [{ neighborhood: "25", name: "Austin", count: 200 }],
  most_represented_neighborhood: "25",
  most_represented_neighborhood_name: "Austin",
};

describe("SummaryCards", () => {
  it("shows the resolved neighborhood name with its code, not the bare code", () => {
    render(<SummaryCards summary={BASE_SUMMARY} loading={false} />);

    expect(screen.getByText("Austin (25)")).toBeInTheDocument();
    expect(screen.queryByText("25", { selector: ".summary-card__value" })).not.toBeInTheDocument();
  });

  it("falls back to Unknown (code) when the code has no resolved name, without crashing", () => {
    const summary: SummaryResponse = {
      ...BASE_SUMMARY,
      most_represented_neighborhood: "9999",
      most_represented_neighborhood_name: null,
    };

    render(<SummaryCards summary={summary} loading={false} />);

    expect(screen.getByText("Unknown (9999)")).toBeInTheDocument();
  });

  it("shows an em dash when there's no most-represented neighborhood at all", () => {
    const summary: SummaryResponse = {
      ...BASE_SUMMARY,
      most_represented_neighborhood: null,
      most_represented_neighborhood_name: null,
    };

    render(<SummaryCards summary={summary} loading={false} />);

    expect(screen.getByText("—", { selector: ".summary-card__value" })).toBeInTheDocument();
  });
});
