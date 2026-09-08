import { useEffect, useRef } from "react";

import type { Incident } from "../api/types";

interface IncidentDetailProps {
  incident: Incident | null;
  onClose: () => void;
}

function formatDateTime(value: string | null): string {
  if (!value) return "Unknown";
  return new Date(value).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

const PRECISION_LABEL: Record<string, string> = {
  exact: "Exact",
  approximate: "Approximate",
  block: "Generalized to the block",
  intersection: "Generalized to an intersection",
  suppressed: "Suppressed by the source",
  unknown: "Unknown precision",
};

export default function IncidentDetail({ incident, onClose }: IncidentDetailProps) {
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (incident) closeButtonRef.current?.focus();
  }, [incident]);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      // Minimal focus trap: keep Tab/Shift+Tab cycling within the
      // dialog rather than letting keyboard focus escape into the
      // backgrounded page behind it (docs/product.md item 13:
      // keyboard-accessible controls).
      if (e.key === "Tab" && panelRef.current) {
        const focusable = panelRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
        );
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }
    if (incident) {
      document.addEventListener("keydown", onKeyDown);
      return () => document.removeEventListener("keydown", onKeyDown);
    }
  }, [incident, onClose]);

  if (!incident) return null;

  const primaryOffense = incident.offenses[0];

  return (
    <div
      ref={panelRef}
      className="incident-detail"
      role="dialog"
      aria-label="Incident details"
      aria-modal="true"
    >
      <button
        ref={closeButtonRef}
        type="button"
        className="incident-detail__close"
        onClick={onClose}
        aria-label="Close incident details"
      >
        ×
      </button>

      <h2 className="incident-detail__title">
        {primaryOffense?.source_category ?? "Reported incident"}
      </h2>
      {primaryOffense?.source_subcategory && (
        <p className="incident-detail__subtitle">{primaryOffense.source_subcategory}</p>
      )}

      <dl className="incident-detail__list">
        <dt>Occurred</dt>
        <dd>{formatDateTime(incident.occurred_at)}</dd>

        <dt>Neighborhood</dt>
        <dd>{incident.neighborhood ?? "Unknown"} (community area code)</dd>

        <dt>District / Beat</dt>
        <dd>
          {incident.district ?? "Unknown"} / {incident.beat ?? "Unknown"}
        </dd>

        <dt>Location</dt>
        <dd>
          {incident.address_text ?? "Unknown block"}
          <br />
          <span className="incident-detail__precision">
            {PRECISION_LABEL[incident.location_precision] ?? incident.location_precision}
            {" — this is not the exact location of the incident. "}
            Chicago shifts published coordinates to a nearby point on the same block to protect
            privacy.
          </span>
        </dd>

        {incident.offenses.length > 1 && (
          <>
            <dt>Other offenses on this incident</dt>
            <dd>
              <ul className="incident-detail__offense-list">
                {incident.offenses.slice(1).map((o) => (
                  <li key={o.id}>
                    {o.source_category}
                    {o.source_subcategory ? ` — ${o.source_subcategory}` : ""}
                  </li>
                ))}
              </ul>
            </dd>
          </>
        )}

        <dt>Source</dt>
        <dd>City of Chicago — Crimes 2001 to Present (case {incident.external_incident_id})</dd>
      </dl>
    </div>
  );
}
