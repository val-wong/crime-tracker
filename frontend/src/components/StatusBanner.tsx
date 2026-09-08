import type { DatasetStatusResponse } from "../api/types";
import { formatCalendarDate } from "../utils/formatCalendarDate";

interface StatusBannerProps {
  status: DatasetStatusResponse | null;
  loading: boolean;
}

export default function StatusBanner({ status, loading }: StatusBannerProps) {
  if (loading) {
    return (
      <span className="status-banner" data-variant="loading">
        Checking dataset status…
      </span>
    );
  }

  if (!status) {
    return (
      <span className="status-banner" data-variant="error" role="status">
        Dataset status unavailable — backend unreachable
      </span>
    );
  }

  const coverage =
    status.earliest_occurred_date && status.latest_occurred_date
      ? `${formatCalendarDate(status.earliest_occurred_date)} – ${formatCalendarDate(status.latest_occurred_date)}`
      : "no data loaded yet";

  return (
    <span
      className="status-banner"
      data-variant={status.is_population_complete ? "complete" : "partial"}
      role="status"
    >
      <strong>{status.is_population_complete ? "Full dataset" : "Partial development data"}</strong>
      <span className="status-banner__detail"> · {coverage}</span>
      {!status.is_population_complete && (
        <span className="status-banner__detail">
          {" "}
          · {status.incident_count.toLocaleString()} incidents loaded so far
        </span>
      )}
    </span>
  );
}
