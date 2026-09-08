import type { SummaryResponse } from "../api/types";

interface SummaryCardsProps {
  summary: SummaryResponse | null;
  loading: boolean;
}

function formatChange(percentChange: number | null): string {
  if (percentChange === null) return "no prior-period data";
  const sign = percentChange > 0 ? "+" : "";
  return `${sign}${percentChange}% vs. prior period`;
}

export default function SummaryCards({ summary, loading }: SummaryCardsProps) {
  if (loading) {
    return (
      <div className="summary-cards" aria-busy="true">
        <p>Loading summary…</p>
      </div>
    );
  }

  if (!summary) {
    return (
      <div className="summary-cards" role="status">
        <p>Summary unavailable right now — try adjusting filters or reloading.</p>
      </div>
    );
  }

  return (
    <div className="summary-cards">
      <article className="summary-card">
        <h3>Reported incidents</h3>
        <p className="summary-card__value">{summary.reported_incidents.toLocaleString()}</p>
        <p className="summary-card__detail">
          {summary.start_date} to {summary.end_date}
        </p>
      </article>

      <article className="summary-card">
        <h3>Change vs. previous period</h3>
        <p className="summary-card__value">{formatChange(summary.percent_change)}</p>
        <p className="summary-card__detail">
          Previous: {summary.previous_period_reported_incidents.toLocaleString()} reported incidents
          ({summary.previous_period_start_date} to {summary.previous_period_end_date})
        </p>
      </article>

      <article className="summary-card">
        <h3>Most common category</h3>
        <p className="summary-card__value">{summary.most_common_category ?? "—"}</p>
        <p className="summary-card__detail">Among reported incidents in the selected period</p>
      </article>

      <article className="summary-card">
        <h3>Most represented neighborhood</h3>
        <p className="summary-card__value">{summary.most_represented_neighborhood ?? "—"}</p>
        <p className="summary-card__detail">
          Chicago community area code — see the data notes for why this isn't a name
        </p>
      </article>
    </div>
  );
}
