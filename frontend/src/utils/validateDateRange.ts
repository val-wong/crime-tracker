import { formatCalendarDate } from "./formatCalendarDate";

// Calendar-date strings ("YYYY-MM-DD") compare correctly with plain
// string/lexicographic operators -- no `Date` parsing (and therefore no
// timezone-shift risk, see formatCalendarDate.ts) is needed to know
// which of two calendar dates comes first.

export interface DateBounds {
  // The dataset's earliest/latest loaded occurrence date (see
  // /api/status). `null`/`undefined` when status hasn't loaded yet or
  // the source has no data -- bounds simply aren't enforced in that
  // case rather than blocking every filter attempt.
  min?: string | null;
  max?: string | null;
}

// Returns an error message describing the first violated rule, or
// `null` when the range is acceptable to apply.
export function validateDateRange(
  from: string | undefined,
  to: string | undefined,
  bounds: DateBounds,
): string | null {
  if (from && to && from > to) {
    return "'From' date must be on or before 'To' date.";
  }

  if (bounds.min) {
    if (from && from < bounds.min) {
      return `'From' date is before the available data range, which starts ${formatCalendarDate(bounds.min)}.`;
    }
    if (to && to < bounds.min) {
      return `'To' date is before the available data range, which starts ${formatCalendarDate(bounds.min)}.`;
    }
  }

  if (bounds.max) {
    if (to && to > bounds.max) {
      return `'To' date is after the available data range, which ends ${formatCalendarDate(bounds.max)}.`;
    }
    if (from && from > bounds.max) {
      return `'From' date is after the available data range, which ends ${formatCalendarDate(bounds.max)}.`;
    }
  }

  return null;
}
