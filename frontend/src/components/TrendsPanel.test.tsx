import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { SummaryResponse } from "../api/types";
import TrendsPanel from "./TrendsPanel";

const BASE_SUMMARY: SummaryResponse = {
  start_date: "2026-07-30",
  end_date: "2026-08-29",
  reported_incidents: 700,
  previous_period_start_date: "2026-06-29",
  previous_period_end_date: "2026-07-29",
  previous_period_reported_incidents: 650,
  percent_change: 7.7,
  end_date_clamped: false,
  category_breakdown: [{ category: "THEFT", count: 400 }],
  most_common_category: "THEFT",
  time_bucket: "week",
  incidents_by_time: [
    { bucket_start: "2026-08-03", count: 300, is_partial: false },
    { bucket_start: "2026-08-24", count: 150, is_partial: true },
  ],
  top_neighborhoods: [{ neighborhood: "25", name: "Austin", count: 200 }],
  most_represented_neighborhood: "25",
  most_represented_neighborhood_name: "Austin",
};

// Regression test: a trend bucket whose real source coverage ends
// partway through it must say so -- otherwise the last bar reads as a
// complete week/month when the source simply hasn't caught up yet
// (see app/services/summary.py::_mark_partial_final_bucket).
describe("TrendsPanel partial bucket labeling", () => {
  it("labels only the bucket flagged is_partial, not earlier ones", () => {
    render(<TrendsPanel summary={BASE_SUMMARY} loading={false} />);

    expect(screen.getByText(/Aug 24, 2026 \(partial\)/)).toBeInTheDocument();
    expect(screen.getByText(/^Aug 3, 2026$/)).toBeInTheDocument();
  });

  it("does not label any bucket when none are partial", () => {
    const summary: SummaryResponse = {
      ...BASE_SUMMARY,
      incidents_by_time: [
        { bucket_start: "2026-08-03", count: 300, is_partial: false },
        { bucket_start: "2026-08-10", count: 280, is_partial: false },
      ],
    };
    render(<TrendsPanel summary={summary} loading={false} />);

    expect(screen.queryByText(/\(partial\)/)).not.toBeInTheDocument();
  });
});
