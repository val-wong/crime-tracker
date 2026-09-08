import type { SummaryResponse } from "../api/types";
import { formatCalendarDate } from "../utils/formatCalendarDate";
import BarChart from "./BarChart";

interface TrendsPanelProps {
  summary: SummaryResponse | null;
  loading: boolean;
}

export default function TrendsPanel({ summary, loading }: TrendsPanelProps) {
  return (
    <section className="panel" aria-label="Trends">
      <h2>Trends</h2>
      <p className="panel__note">
        Counts reflect records in the source dataset, not a measure of actual crime or area danger —
        see "About this data" below.
      </p>

      {loading || !summary ? (
        <p>Loading trends…</p>
      ) : (
        <>
          <h3 className="panel__subheading">
            Reported incidents by {summary.time_bucket === "week" ? "week" : "month"}
          </h3>
          <BarChart
            title={`Reported incidents by ${summary.time_bucket}`}
            valueLabel="Reported incidents"
            items={summary.incidents_by_time.map((t) => ({
              label:
                formatCalendarDate(t.bucket_start, {
                  month: "short",
                  day: summary.time_bucket === "week" ? "numeric" : undefined,
                  year: "numeric",
                }) + (t.is_partial ? " (partial)" : ""),
              value: t.count,
            }))}
          />

          <h3 className="panel__subheading">Category breakdown</h3>
          <BarChart
            title="Reported incidents by category"
            valueLabel="Reported incidents"
            items={summary.category_breakdown.slice(0, 10).map((c) => ({
              label: c.category ?? "Unknown",
              value: c.count,
            }))}
          />
        </>
      )}
    </section>
  );
}
