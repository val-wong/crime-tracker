import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { DatasetStatusResponse } from "../api/types";
import StatusBanner from "./StatusBanner";

const STATUS: DatasetStatusResponse = {
  city: "Chicago",
  source_name: "City of Chicago - Crimes - 2001 to Present",
  publisher: "Chicago Police Department",
  source_url: "https://data.cityofchicago.org",
  attribution: "This site provides applications using data...",
  is_population_complete: true,
  latest_successful_ingestion_at: "2026-09-01T00:00:00Z",
  earliest_occurred_date: "2001-01-01",
  latest_occurred_date: "2026-08-29",
  incident_count: 8000000,
  offense_count: 8600000,
};

// Regression test for a real bug found during browser QA: the badge
// showed "Dec 31, 2000 - Aug 28, 2026" for a dataset that actually
// covers 2001-01-01 through 2026-08-29 -- a date-only value shifted
// back one calendar day by a UTC-parse/local-render mismatch. This
// only reproduces in a timezone behind UTC, so it's forced explicitly
// here rather than relying on the test runner's own default timezone.
describe("StatusBanner date rendering", () => {
  const originalTz = process.env.TZ;

  beforeEach(() => {
    process.env.TZ = "America/Chicago";
  });

  afterEach(() => {
    process.env.TZ = originalTz;
  });

  it("renders the dataset's earliest and latest dates without a one-day shift", () => {
    render(<StatusBanner status={STATUS} loading={false} />);

    expect(screen.getByText(/Jan 1, 2001/)).toBeInTheDocument();
    expect(screen.getByText(/Aug 29, 2026/)).toBeInTheDocument();
    expect(screen.queryByText(/Dec 31, 2000/)).not.toBeInTheDocument();
    expect(screen.queryByText(/Aug 28, 2026/)).not.toBeInTheDocument();
  });
});
