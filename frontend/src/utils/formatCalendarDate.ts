// Calendar ("date-only") values from the API -- e.g. "2001-01-01" --
// have no time-of-day or timezone meaning at all. `new Date(value)`
// parses a bare "YYYY-MM-DD" string as UTC midnight (per the
// ECMAScript spec), but `.toLocaleDateString()` then renders it in the
// *browser's local* timezone -- for any timezone behind UTC (which
// includes every US timezone), that shifts the displayed date back by
// one calendar day (e.g. "2001-01-01" rendered as "Dec 31, 2000").
//
// Passing `timeZone: "UTC"` makes the formatting step use the same UTC
// frame the parsing step already assumed, so the calendar date shown
// always matches the calendar date received, regardless of the
// viewer's own timezone.
export function formatCalendarDate(
  value: string | null | undefined,
  options: Intl.DateTimeFormatOptions = { year: "numeric", month: "short", day: "numeric" },
): string {
  if (!value) return "unknown";
  return new Date(value).toLocaleDateString(undefined, { ...options, timeZone: "UTC" });
}
