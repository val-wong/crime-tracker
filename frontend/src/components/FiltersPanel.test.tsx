import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import FiltersPanel from "./FiltersPanel";

describe("FiltersPanel", () => {
  it("calls onChange with the field that changed", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(
      <FiltersPanel
        filters={{}}
        onChange={onChange}
        onClear={vi.fn()}
        categoryOptions={[]}
        neighborhoodOptions={[]}
      />,
    );

    await user.type(screen.getByLabelText("Category"), "T");
    expect(onChange).toHaveBeenCalledWith({ category: "T" });
  });

  it("disables Clear filters when no filters are active", () => {
    render(
      <FiltersPanel
        filters={{}}
        onChange={vi.fn()}
        onClear={vi.fn()}
        categoryOptions={[]}
        neighborhoodOptions={[]}
      />,
    );

    expect(screen.getByRole("button", { name: "Clear filters" })).toBeDisabled();
  });

  it("enables and triggers Clear filters when a filter is active", async () => {
    const onClear = vi.fn();
    const user = userEvent.setup();
    render(
      <FiltersPanel
        filters={{ category: "THEFT" }}
        onChange={vi.fn()}
        onClear={onClear}
        categoryOptions={[]}
        neighborhoodOptions={[]}
      />,
    );

    const clearButton = screen.getByRole("button", { name: "Clear filters" });
    expect(clearButton).toBeEnabled();
    await user.click(clearButton);
    expect(onClear).toHaveBeenCalledOnce();
  });

  it("lists suggested categories and neighborhoods in datalists", () => {
    render(
      <FiltersPanel
        filters={{}}
        onChange={vi.fn()}
        onClear={vi.fn()}
        categoryOptions={["THEFT", "BATTERY"]}
        neighborhoodOptions={["25"]}
      />,
    );

    expect(document.querySelector('option[value="THEFT"]')).not.toBeNull();
    expect(document.querySelector('option[value="25"]')).not.toBeNull();
  });
});
