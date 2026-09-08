import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { formatCalendarDate } from "./formatCalendarDate";

// Regression test for a real bug found during browser QA: the status
// badge showed "Dec 31, 2000" for a dataset that actually starts
// 2001-01-01. `new Date("2001-01-01")` parses a bare date-only string
// as UTC midnight, and formatting that in a timezone *behind* UTC
// (every US timezone, including Chicago's) rolls it back to the
// previous calendar day. The bug only reproduces in such a timezone --
// explicitly forcing one here makes this test meaningful regardless of
// the timezone the test runner itself happens to default to.
describe("formatCalendarDate", () => {
  const originalTz = process.env.TZ;

  beforeEach(() => {
    process.env.TZ = "America/Chicago"; // UTC-6/-5 -- behind UTC
  });

  afterEach(() => {
    process.env.TZ = originalTz;
  });

  it("renders 2001-01-01 as Jan 1, 2001, not Dec 31, 2000", () => {
    expect(formatCalendarDate("2001-01-01")).toBe("Jan 1, 2001");
  });

  it("renders a late-month date without shifting into the next month", () => {
    expect(formatCalendarDate("2026-08-29")).toBe("Aug 29, 2026");
  });

  it("returns 'unknown' for a null/undefined value", () => {
    expect(formatCalendarDate(null)).toBe("unknown");
    expect(formatCalendarDate(undefined)).toBe("unknown");
  });

  it("respects custom formatting options (e.g. month/year only)", () => {
    expect(formatCalendarDate("2020-02-01", { month: "short", year: "numeric" })).toBe("Feb 2020");
  });
});
