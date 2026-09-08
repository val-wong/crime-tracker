import type { DatasetStatusResponse } from "../api/types";

interface DataCaveatInfoProps {
  status: DatasetStatusResponse | null;
}

// Chicago's Terms of Use require this exact sentence to appear at any
// site serving an application built on this data -- see
// docs/sources/chicago.md §10 and
// docs/operations/chicago-ingestion.md "Required attribution". Do not
// reword, shorten, or move this out of the rendered page.
const CHICAGO_ATTRIBUTION =
  "This site provides applications using data that has been modified for use from its " +
  "original source, www.cityofchicago.org, the official website of the City of Chicago. " +
  "The City of Chicago makes no claims as to the content, accuracy, timeliness, or " +
  "completeness of any of the data provided at this site. The data provided at this site " +
  "is subject to change at any time. It is understood that the data provided at this site " +
  "is being used at one's own risk.";

export default function DataCaveatInfo({ status }: DataCaveatInfoProps) {
  return (
    <details className="panel data-caveat">
      <summary>
        <h2 className="data-caveat__heading">About this data</h2>
      </summary>

      <ul className="data-caveat__list">
        <li>
          Data comes from the Chicago Police Department via the City of Chicago's official open data
          portal ("Crimes — 2001 to Present").
        </li>
        <li>
          Locations shown on the map are generalized: Chicago shifts each incident's published
          coordinates to a nearby point on the same block. Map pins are never the exact location of
          an incident.
        </li>
        <li>
          Reported crime does not equal all crime. These records reflect incidents reported to and
          recorded by police — not every crime that occurs is reported, and reporting rates vary by
          category, neighborhood, and over time.
        </li>
        <li>
          Records may be updated, corrected, or reclassified by the source after they are first
          published. Figures shown here reflect the data as most recently ingested — see the dataset
          status in the header for freshness and completeness.
        </li>
        <li>
          Historical figures should be interpreted as counts of reported incidents, not a complete
          or authoritative account of crime in Chicago.
        </li>
        <li>
          This product does not predict individual criminal behavior, identify or profile any
          person, and does not produce danger, risk, or safety scores for any place or person.
        </li>
      </ul>

      {status && !status.is_population_complete && (
        <p className="data-caveat__partial-notice">
          <strong>Development notice:</strong> the currently loaded dataset is a partial,
          non-contiguous sample used during development — it is not yet a complete historical
          record. Trend figures may not reflect the full picture until the full dataset is loaded.
        </p>
      )}

      <p className="data-caveat__attribution">{CHICAGO_ATTRIBUTION}</p>
    </details>
  );
}
