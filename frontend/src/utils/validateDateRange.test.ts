import { describe, expect, it } from "vitest";

import { validateDateRange } from "./validateDateRange";

const COVERAGE = { min: "2001-01-01", max: "2026-08-29" };

describe("validateDateRange", () => {
  it("accepts a range fully inside the available coverage", () => {
    expect(validateDateRange("2026-08-01", "2026-08-29", COVERAGE)).toBeNull();
  });

  it("accepts when only one side is set", () => {
    expect(validateDateRange("2026-08-01", undefined, COVERAGE)).toBeNull();
    expect(validateDateRange(undefined, "2026-08-29", COVERAGE)).toBeNull();
  });

  it("accepts when neither side is set", () => {
    expect(validateDateRange(undefined, undefined, COVERAGE)).toBeNull();
  });

  it("rejects From after To", () => {
    const error = validateDateRange("2026-08-29", "2026-08-01", COVERAGE);
    expect(error).toMatch(/'From' date must be on or before 'To' date/);
  });

  it("rejects a To date after the latest available occurrence date", () => {
    const error = validateDateRange("2026-08-01", "2026-09-15", COVERAGE);
    expect(error).toMatch(/after the available data range/);
    expect(error).toMatch(/Aug 29, 2026/);
  });

  it("rejects a From date before the earliest available occurrence date", () => {
    const error = validateDateRange("2000-01-01", "2026-08-29", COVERAGE);
    expect(error).toMatch(/before the available data range/);
    expect(error).toMatch(/Jan 1, 2001/);
  });

  it("rejects a To date before the earliest available occurrence date", () => {
    const error = validateDateRange(undefined, "2000-06-01", COVERAGE);
    expect(error).toMatch(/before the available data range/);
  });

  it("rejects a From date after the latest available occurrence date", () => {
    const error = validateDateRange("2026-09-15", undefined, COVERAGE);
    expect(error).toMatch(/after the available data range/);
  });

  it("skips coverage checks when bounds are unavailable (status not loaded)", () => {
    expect(validateDateRange("1999-01-01", "2099-01-01", {})).toBeNull();
  });
});
