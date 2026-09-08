import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";

import FiltersPanel from "./FiltersPanel";

const COVERAGE = { min: "2001-01-01", max: "2026-08-29" };

// A representative slice of the complete, filter-independent category
// list /api/categories returns (see backend/app/api/categories.py) --
// deliberately more than the 1-2 categories a filtered summary
// breakdown would typically contain.
const CATEGORIES = ["BATTERY", "MOTOR VEHICLE THEFT", "ROBBERY", "THEFT"];

function renderPanel(overrides: Partial<ComponentProps<typeof FiltersPanel>> = {}) {
  const onApply = vi.fn();
  const onClear = vi.fn();
  render(
    <FiltersPanel
      committedFilters={{}}
      onApply={onApply}
      onClear={onClear}
      categoryOptions={CATEGORIES}
      dateBounds={COVERAGE}
      {...overrides}
    />,
  );
  return { onApply, onClear };
}

function setDate(label: string, value: string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value } });
}

async function applyFilters(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Apply filters" }));
}

describe("FiltersPanel category dropdown", () => {
  it("renders 'All categories' plus the complete set of known categories, not a free-text field", () => {
    renderPanel();

    expect(screen.queryByPlaceholderText(/e\.g\. THEFT/)).not.toBeInTheDocument();
    expect(screen.getByRole("option", { name: "All categories" })).toBeInTheDocument();
    for (const category of CATEGORIES) {
      expect(screen.getByRole("option", { name: category })).toBeInTheDocument();
    }
  });

  it("does not change the offered categories when the category options prop is otherwise stable across re-renders", () => {
    // Regression guard for the bug this task fixes: the dropdown must
    // reflect a filter-independent list, not something scoped to the
    // currently selected category or a narrow per-request breakdown.
    const { rerender } = render(
      <FiltersPanel
        committedFilters={{}}
        onApply={vi.fn()}
        onClear={vi.fn()}
        categoryOptions={CATEGORIES}
        dateBounds={COVERAGE}
      />,
    );
    for (const category of CATEGORIES) {
      expect(screen.getByRole("option", { name: category })).toBeInTheDocument();
    }

    // Re-rendering with a *different* committed category (as would
    // happen after selecting one and applying) must not narrow the
    // option list.
    rerender(
      <FiltersPanel
        committedFilters={{ category: "ROBBERY" }}
        onApply={vi.fn()}
        onClear={vi.fn()}
        categoryOptions={CATEGORIES}
        dateBounds={COVERAGE}
      />,
    );
    for (const category of CATEGORIES) {
      expect(screen.getByRole("option", { name: category })).toBeInTheDocument();
    }
  });

  it("selecting THEFT and applying sends the exact API category value", async () => {
    const user = userEvent.setup();
    const { onApply } = renderPanel();

    await user.selectOptions(screen.getByLabelText("Category"), "THEFT");
    await applyFilters(user);

    expect(onApply).toHaveBeenCalledWith(expect.objectContaining({ category: "THEFT" }));
  });

  it("selecting 'All categories' clears the category filter on apply", async () => {
    const user = userEvent.setup();
    const { onApply } = renderPanel({ committedFilters: { category: "THEFT" } });

    await user.selectOptions(screen.getByLabelText("Category"), "All categories");
    await applyFilters(user);

    expect(onApply).toHaveBeenCalledWith(expect.objectContaining({ category: undefined }));
  });

  it("preserves an unrecognized committed category (e.g. a stale shared link) without crashing", () => {
    renderPanel({ committedFilters: { category: "ARSON-COLD-CASE" } });

    const select = screen.getByLabelText("Category") as HTMLSelectElement;
    expect(select.value).toBe("ARSON-COLD-CASE");
  });
});

describe("FiltersPanel neighborhood dropdown", () => {
  it("offers official Chicago community area names, not numeric codes", () => {
    renderPanel();
    expect(screen.getByRole("option", { name: "Austin" })).toBeInTheDocument();
  });

  it("selecting a neighborhood by name applies its numeric code", async () => {
    const user = userEvent.setup();
    const { onApply } = renderPanel();

    await user.selectOptions(screen.getByLabelText("Neighborhood"), "Austin");
    await applyFilters(user);

    expect(onApply).toHaveBeenCalledWith(expect.objectContaining({ neighborhood: "25" }));
  });

  it("preserves an unrecognized neighborhood code from the URL without crashing", () => {
    renderPanel({ committedFilters: { neighborhood: "9999" } });

    const select = screen.getByLabelText("Neighborhood") as HTMLSelectElement;
    expect(select.value).toBe("9999");
    expect(screen.getByRole("option", { name: "Unknown (9999)" })).toBeInTheDocument();
  });
});

describe("FiltersPanel draft/apply behavior", () => {
  it("does not call onApply while editing draft fields", async () => {
    const user = userEvent.setup();
    const { onApply } = renderPanel();

    await user.selectOptions(screen.getByLabelText("Category"), "THEFT");
    await user.selectOptions(screen.getByLabelText("Neighborhood"), "Austin");
    setDate("From", "2026-08-01");

    expect(onApply).not.toHaveBeenCalled();
  });

  it("commits every draft field to the applied filter state, using the API values", async () => {
    const user = userEvent.setup();
    const { onApply } = renderPanel();

    await user.selectOptions(screen.getByLabelText("Category"), "THEFT");
    await user.selectOptions(screen.getByLabelText("Neighborhood"), "Austin");
    setDate("From", "2026-08-01");
    setDate("To", "2026-08-29");
    await applyFilters(user);

    expect(onApply).toHaveBeenCalledWith({
      category: "THEFT",
      neighborhood: "25",
      from: "2026-08-01",
      to: "2026-08-29",
    });
  });

  it("reflects committed filters (e.g. from a shared URL) as the initial draft", () => {
    renderPanel({
      committedFilters: {
        category: "THEFT",
        neighborhood: "25",
        from: "2026-08-01",
        to: "2026-08-29",
      },
    });

    expect((screen.getByLabelText("Category") as HTMLSelectElement).value).toBe("THEFT");
    expect((screen.getByLabelText("Neighborhood") as HTMLSelectElement).value).toBe("25");
    expect((screen.getByLabelText("From") as HTMLInputElement).value).toBe("2026-08-01");
    expect((screen.getByLabelText("To") as HTMLInputElement).value).toBe("2026-08-29");
  });

  it("disables Apply while a committed-filter request is loading", () => {
    renderPanel({ applyDisabled: true });
    expect(screen.getByRole("button", { name: "Apply filters" })).toBeDisabled();
  });
});

describe("FiltersPanel Clear filters", () => {
  it("disables Clear when no filters are active in draft or committed state", () => {
    renderPanel();
    expect(screen.getByRole("button", { name: "Clear filters" })).toBeDisabled();
  });

  it("enables Clear for an uncommitted draft edit alone", async () => {
    const user = userEvent.setup();
    renderPanel();

    await user.selectOptions(screen.getByLabelText("Category"), "THEFT");
    expect(screen.getByRole("button", { name: "Clear filters" })).toBeEnabled();
  });

  it("resets draft fields and calls onClear", async () => {
    const user = userEvent.setup();
    const { onClear } = renderPanel({ committedFilters: { category: "THEFT" } });

    await user.click(screen.getByRole("button", { name: "Clear filters" }));

    expect(onClear).toHaveBeenCalledOnce();
    expect((screen.getByLabelText("Category") as HTMLSelectElement).value).toBe("");
  });
});

describe("FiltersPanel date validation", () => {
  it("rejects From after To and does not apply", async () => {
    const user = userEvent.setup();
    const { onApply } = renderPanel();

    setDate("From", "2026-08-29");
    setDate("To", "2026-08-01");
    await applyFilters(user);

    expect(screen.getByRole("alert")).toHaveTextContent(/'From' date must be on or before 'To'/);
    expect(onApply).not.toHaveBeenCalled();
  });

  it("rejects a From date before the dataset's earliest available date and does not apply", async () => {
    const user = userEvent.setup();
    const { onApply } = renderPanel();

    setDate("From", "1999-01-01");
    await applyFilters(user);

    expect(screen.getByRole("alert")).toHaveTextContent(/before the available data range/);
    expect(onApply).not.toHaveBeenCalled();
  });

  it("rejects a To date after the dataset's latest available date and does not apply", async () => {
    const user = userEvent.setup();
    const { onApply } = renderPanel();

    setDate("To", "2099-01-01");
    await applyFilters(user);

    expect(screen.getByRole("alert")).toHaveTextContent(/after the available data range/);
    expect(onApply).not.toHaveBeenCalled();
  });

  it("sets min/max on the date inputs from dataset coverage", () => {
    renderPanel();
    expect(screen.getByLabelText("From")).toHaveAttribute("min", "2001-01-01");
    expect(screen.getByLabelText("To")).toHaveAttribute("max", "2026-08-29");
  });

  it("clears a previous validation error once a valid range is applied", async () => {
    const user = userEvent.setup();
    const { onApply } = renderPanel();

    setDate("From", "2026-08-29");
    setDate("To", "2026-08-01");
    await applyFilters(user);
    expect(screen.getByRole("alert")).toBeInTheDocument();

    setDate("From", "2026-08-01");
    setDate("To", "2026-08-20");
    await applyFilters(user);

    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(onApply).toHaveBeenCalledWith(
      expect.objectContaining({ from: "2026-08-01", to: "2026-08-20" }),
    );
  });
});

describe("FiltersPanel default period messaging", () => {
  it("shows the effective default period when no date filter is committed", () => {
    renderPanel({ effectivePeriod: { start: "2026-07-30", end: "2026-08-20" } });

    const note = screen.getByText(/default recent period/);
    expect(note).toBeInTheDocument();
    expect(note).toHaveTextContent("Aug 20, 2026");
  });

  it("does not show the default-period note once a date filter is committed", () => {
    renderPanel({
      committedFilters: { from: "2026-08-01" },
      effectivePeriod: { start: "2026-07-30", end: "2026-08-29" },
    });

    expect(screen.queryByText(/default recent period/)).not.toBeInTheDocument();
  });
});
