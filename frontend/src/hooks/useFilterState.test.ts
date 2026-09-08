import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { useFilterState } from "./useFilterState";

describe("useFilterState", () => {
  afterEach(() => {
    window.history.replaceState(null, "", "/");
  });

  it("reads initial state from the URL", () => {
    window.history.replaceState(
      null,
      "",
      "/?category=THEFT&neighborhood=25&from=2026-08-01&to=2026-09-01",
    );

    const { result } = renderHook(() => useFilterState());
    const [filters] = result.current;

    expect(filters).toEqual({
      category: "THEFT",
      neighborhood: "25",
      from: "2026-08-01",
      to: "2026-09-01",
    });
  });

  it("writes filter updates back to the URL", () => {
    const { result } = renderHook(() => useFilterState());

    act(() => {
      result.current[1]({ category: "BATTERY" });
    });

    expect(window.location.search).toContain("category=BATTERY");
    expect(result.current[0].category).toBe("BATTERY");
  });

  it("removes a param from the URL when it's cleared to undefined", () => {
    window.history.replaceState(null, "", "/?category=THEFT");
    const { result } = renderHook(() => useFilterState());

    act(() => {
      result.current[1]({ category: undefined });
    });

    expect(window.location.search).not.toContain("category");
  });

  it("clearFilters removes all params", () => {
    window.history.replaceState(null, "", "/?category=THEFT&neighborhood=25");
    const { result } = renderHook(() => useFilterState());

    act(() => {
      result.current[2]();
    });

    expect(window.location.search).toBe("");
    expect(result.current[0]).toEqual({});
  });
});
