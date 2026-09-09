"""Persistence functions for Incident and Offense rows.

Includes both simple per-record functions (used by tests/fixtures and
small-scale sources) and bulk variants (`get_existing_by_external_ids`,
`bulk_insert_incidents`, `get_offenses_by_source_record_ids`,
`bulk_insert_offenses`, `bulk_update_offenses`) used by the batched
reconciliation path for sources too large for one-row-at-a-time round
trips — see docs/sources/chicago-ingestion-design.md "Performance".
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional

from geoalchemy2 import Geography, Geometry, WKTElement
from sqlalchemy import bindparam, cast, func, select, update
from sqlalchemy.orm import Session, selectinload

from app.adapters.base import IncidentDraft, OffenseDraft
from app.models.incident import Incident
from app.models.offense import Offense


@dataclass(frozen=True)
class BoundingBox:
    """A map-viewport bounding box in WGS84 degrees (west/south/east/north
    -- the same order MapLibre/Leaflet's `getBounds()` produces)."""

    min_lon: float
    min_lat: float
    max_lon: float
    max_lat: float


def get_by_external_id(
    db: Session, *, source_id: uuid.UUID, external_incident_id: str
) -> Optional[Incident]:
    return db.execute(
        select(Incident).where(
            Incident.source_id == source_id,
            Incident.external_incident_id == external_incident_id,
        )
    ).scalar_one_or_none()


def _location_for(draft: IncidentDraft) -> Optional[WKTElement]:
    if draft.latitude is not None and draft.longitude is not None:
        return WKTElement(f"POINT({draft.longitude} {draft.latitude})", srid=4326)
    return None


def get_or_create(db: Session, *, source_id: uuid.UUID, draft: IncidentDraft) -> Incident:
    existing = get_by_external_id(
        db, source_id=source_id, external_incident_id=draft.external_incident_id
    )
    if existing is not None:
        return existing

    incident = Incident(
        source_id=source_id,
        external_incident_id=draft.external_incident_id,
        occurred_at=draft.occurred_at,
        occurred_at_raw=draft.occurred_at_raw,
        reported_at=draft.reported_at,
        reported_at_raw=draft.reported_at_raw,
        source_time_convention=draft.source_time_convention,
        latitude=draft.latitude,
        longitude=draft.longitude,
        location=_location_for(draft),
        location_precision=draft.location_precision,
        address_text=draft.address_text,
        neighborhood=draft.neighborhood,
        district=draft.district,
        beat=draft.beat,
    )
    db.add(incident)
    db.flush()
    return incident


def get_offense_by_source_record(db: Session, *, source_record_id: uuid.UUID) -> Optional[Offense]:
    return db.execute(
        select(Offense).where(Offense.source_record_id == source_record_id)
    ).scalar_one_or_none()


def update_offense(db: Session, offense: Offense, *, draft: OffenseDraft) -> Offense:
    offense.raw_offense_code = draft.raw_offense_code
    offense.fbi_code = draft.fbi_code
    offense.source_category = draft.source_category
    offense.source_subcategory = draft.source_subcategory
    offense.normalized_category = draft.normalized_category
    offense.normalized_subcategory = draft.normalized_subcategory
    offense.victim_count = draft.victim_count
    db.flush()
    return offense


def create_offense(
    db: Session,
    *,
    incident_id: uuid.UUID,
    source_id: uuid.UUID,
    source_record_id: Optional[uuid.UUID],
    draft: OffenseDraft,
) -> Offense:
    offense = Offense(
        incident_id=incident_id,
        source_id=source_id,
        source_record_id=source_record_id,
        external_offense_id=draft.external_offense_id,
        raw_offense_code=draft.raw_offense_code,
        fbi_code=draft.fbi_code,
        source_category=draft.source_category,
        source_subcategory=draft.source_subcategory,
        normalized_category=draft.normalized_category,
        normalized_subcategory=draft.normalized_subcategory,
        victim_count=draft.victim_count,
    )
    db.add(offense)
    db.flush()
    return offense


def count_incidents(
    db: Session,
    *,
    source_id: Optional[uuid.UUID] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    category: Optional[str] = None,
    neighborhood: Optional[str] = None,
    district: Optional[str] = None,
    bbox: Optional[BoundingBox] = None,
) -> int:
    """Exact count. Used where correctness matters more than raw speed
    (e.g. app/services/status.py's dataset-wide incident_count) -- see
    `count_incidents_capped` below for the map-viewport pagination case,
    where an exact count of a poorly-selective bbox was confirmed to
    dominate request latency for no real benefit."""
    stmt = select(func.count()).select_from(Incident)
    stmt = apply_incident_filters(
        stmt,
        source_id=source_id,
        start_date=start_date,
        end_date=end_date,
        category=category,
        neighborhood=neighborhood,
        district=district,
        bbox=bbox,
    )
    return db.execute(stmt).scalar_one()


# An exact COUNT(*) must visit every matching row -- fine for a
# selective filter, but confirmed directly (EXPLAIN ANALYZE against the
# full ~8.6M-row dataset) to take ~1.8s for a bounding-box query
# matching 175,000 rows in a dense part of Chicago, dominating what
# should be a fast close-zoom map request. The frontend's map view
# never displays this exact total (it only renders `items`), so an
# honest, capped count -- "at least this many, possibly more" once the
# cap is hit -- is a reasonable, fast-by-construction trade for
# *pagination* totals specifically. The cap is generous enough that any
# realistic filtered page's true count is almost always returned
# exactly.
#
# Deliberately a separate function from `count_incidents` (not a
# capped=True flag on it) -- a real regression during this phase's own
# QA showed why: `app/services/status.py`'s dataset-wide
# `incident_count` also called `count_incidents`, and briefly got
# silently capped at 10,000 instead of reporting the real ~8.6M when
# the cap was added directly to the shared function. Keeping the fast,
# approximate path as its own named function makes it opt-in per
# caller rather than a behavior change every existing caller inherits.
COUNT_CAP = 10_000


def count_incidents_capped(
    db: Session,
    *,
    source_id: Optional[uuid.UUID] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    category: Optional[str] = None,
    neighborhood: Optional[str] = None,
    district: Optional[str] = None,
    bbox: Optional[BoundingBox] = None,
) -> int:
    filtered = select(Incident.id)
    filtered = apply_incident_filters(
        filtered,
        source_id=source_id,
        start_date=start_date,
        end_date=end_date,
        category=category,
        neighborhood=neighborhood,
        district=district,
        bbox=bbox,
    )
    capped = filtered.limit(COUNT_CAP).subquery()
    stmt = select(func.count()).select_from(capped)
    return db.execute(stmt).scalar_one()


def list_incidents(
    db: Session,
    *,
    source_id: Optional[uuid.UUID] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    category: Optional[str] = None,
    neighborhood: Optional[str] = None,
    district: Optional[str] = None,
    bbox: Optional[BoundingBox] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Incident]:
    stmt = select(Incident).options(selectinload(Incident.offenses))
    stmt = apply_incident_filters(
        stmt,
        source_id=source_id,
        start_date=start_date,
        end_date=end_date,
        category=category,
        neighborhood=neighborhood,
        district=district,
        bbox=bbox,
    )
    stmt = stmt.order_by(Incident.occurred_at.desc().nulls_last(), Incident.created_at.desc())
    stmt = stmt.limit(limit).offset(offset)
    return list(db.execute(stmt).scalars())


def get_occurred_at_range(
    db: Session, *, source_id: uuid.UUID
) -> tuple[Optional[date], Optional[date]]:
    """(earliest, latest) `occurred_at` currently loaded for a source --
    used by the public dataset-status endpoint so the frontend can show
    what period is actually covered by a bounded/partial population
    (see docs/operations/chicago-ingestion.md)."""
    row = db.execute(
        select(func.min(Incident.occurred_at), func.max(Incident.occurred_at)).where(
            Incident.source_id == source_id
        )
    ).one()
    earliest, latest = row
    return (earliest.date() if earliest else None, latest.date() if latest else None)


def count_offenses(db: Session, *, source_id: uuid.UUID) -> int:
    # Filters directly on Offense.source_id (denormalized from its
    # incident -- see app/models/offense.py) rather than joining to
    # Incident. The join version was confirmed via EXPLAIN ANALYZE to
    # take ~17s at full Chicago scale (a parallel seq scan of both
    # multi-million-row tables into a hash join); this is a single
    # indexed count.
    return db.execute(
        select(func.count()).select_from(Offense).where(Offense.source_id == source_id)
    ).scalar_one()


def apply_incident_filters(
    stmt,
    *,
    source_id,
    start_date,
    end_date,
    category,
    neighborhood,
    district=None,
    bbox: Optional[BoundingBox] = None,
):
    if source_id is not None:
        stmt = stmt.where(Incident.source_id == source_id)
    if start_date is not None:
        stmt = stmt.where(Incident.occurred_at >= start_date)
    if end_date is not None:
        # `occurred_at` is a real timestamp, not a date -- comparing it
        # to a plain date with `<=` casts that date to midnight
        # (00:00:00), which silently excludes every incident recorded
        # later that same calendar day. `end_date` is meant to be
        # inclusive of the whole day, so the upper bound must be
        # "before the start of the next day" instead. Found while
        # fixing the summary period to anchor on the source's exact
        # latest occurred_at date (see app/services/summary.py), which
        # would otherwise have silently dropped the very last record it
        # was anchored to.
        stmt = stmt.where(Incident.occurred_at < end_date + timedelta(days=1))
    if neighborhood is not None:
        stmt = stmt.where(Incident.neighborhood == neighborhood)
    if district is not None:
        stmt = stmt.where(Incident.district == district)
    if category is not None:
        # category lives on Offense, not Incident -- join only when asked for.
        stmt = stmt.where(
            Incident.id.in_(select(Offense.incident_id).where(Offense.source_category == category))
        )
    if bbox is not None:
        stmt = stmt.where(_bbox_condition(bbox))
    return stmt


def _bbox_condition(bbox: BoundingBox):
    """ST_Intersects against a geography envelope -- uses the GIST index
    on `incidents.location` (confirmed via EXPLAIN ANALYZE; see
    docs/map-aggregation.md "Bounding-box incident queries")."""
    envelope = cast(
        func.ST_MakeEnvelope(bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat, 4326),
        Geography,
    )
    return func.ST_Intersects(Incident.location, envelope)


# ---------------------------------------------------------------------------
# Aggregate (grid) queries for wider map zoom levels -- see
# docs/map-aggregation.md for the full zoom/grid-size strategy.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GridCell:
    lon: float
    lat: float
    count: int


def aggregate_incidents(
    db: Session,
    *,
    grid_degrees: float,
    source_id: Optional[uuid.UUID] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    category: Optional[str] = None,
    neighborhood: Optional[str] = None,
    bbox: Optional[BoundingBox] = None,
    max_cells: int = 5000,
) -> list[GridCell]:
    """Group incidents into `grid_degrees`-sized cells and count each --
    a deliberately simple alternative to H3 (see
    docs/map-aggregation.md), pragmatic for a single-city V1.

    Cells are labeled by the **centroid of their contributing
    incidents' actual coordinates**, not the geometric cell center --
    confirmed via direct inspection of the real Chicago dataset that
    the geometric center (`floor(lon/grid)*grid + grid/2`) can land
    outside the true coordinate range of every single incident
    assigned to that cell: cell (-87.525, 41.775) at the 0.05° grid
    has 3,514 real incidents, every one of them at longitude <=
    -87.5414 (west, on land, along the lakefront) -- yet its
    geometric center (-87.525) is *east* of all of them, out over
    Lake Michigan. This is a direct, mechanical consequence of a
    shoreline cell not being uniformly populated: the geometric
    formula only depends on which grid cell a point falls in, never
    on where within that cell its real points actually are. The
    centroid (`avg(lon)`, `avg(lat)`) is computed from the exact same
    grouped rows already being counted -- an aggregate function
    alongside `count()` in the same pass, not an extra scan or join --
    and by construction can never fall outside the convex hull of the
    points it averages, so it can never drift into a part of the cell
    (e.g. open water) that has zero contributing incidents. `max_cells`
    bounds the response size regardless of how many incidents match
    (ordered by count descending, so the busiest cells are never
    dropped first)."""
    point = cast(Incident.location, Geometry)
    lon = func.ST_X(point)
    lat = func.ST_Y(point)
    # Cell *membership* is still the plain geometric bucket -- grouping
    # by this key is unchanged from before, so which incidents land in
    # which cell (and therefore every cell's count) is identical to the
    # prior geometric-center implementation. Only the *displayed*
    # lon/lat below changes.
    cell_key_lon = func.floor(lon / grid_degrees)
    cell_key_lat = func.floor(lat / grid_degrees)
    centroid_lon = func.avg(lon).label("lon")
    centroid_lat = func.avg(lat).label("lat")
    count = func.count().label("count")

    stmt = (
        select(centroid_lon, centroid_lat, count)
        .select_from(Incident)
        .where(Incident.location.isnot(None))
    )
    stmt = apply_incident_filters(
        stmt,
        source_id=source_id,
        start_date=start_date,
        end_date=end_date,
        category=category,
        neighborhood=neighborhood,
        bbox=bbox,
    )
    stmt = stmt.group_by(cell_key_lon, cell_key_lat).order_by(count.desc()).limit(max_cells)
    rows = db.execute(stmt).all()
    return [GridCell(lon=row.lon, lat=row.lat, count=row.count) for row in rows]


# ---------------------------------------------------------------------------
# Bulk variants for batched reconciliation (see app/services/reconciliation.py)
# ---------------------------------------------------------------------------


def get_existing_by_external_ids(
    db: Session, *, source_id: uuid.UUID, external_incident_ids: Sequence[str]
) -> dict[str, Incident]:
    if not external_incident_ids:
        return {}
    rows = (
        db.execute(
            select(Incident).where(
                Incident.source_id == source_id,
                Incident.external_incident_id.in_(external_incident_ids),
            )
        )
        .scalars()
        .all()
    )
    return {row.external_incident_id: row for row in rows}


def bulk_insert_incidents(db: Session, rows: Sequence[dict[str, Any]]) -> None:
    """``rows`` are plain dicts already shaped as Incident columns,
    including a pre-generated ``id`` — see
    app/services/reconciliation.py's batch sync step."""
    if not rows:
        return
    db.execute(Incident.__table__.insert(), rows)


def get_offenses_by_source_record_ids(
    db: Session, *, source_record_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, Offense]:
    if not source_record_ids:
        return {}
    rows = (
        db.execute(select(Offense).where(Offense.source_record_id.in_(source_record_ids)))
        .scalars()
        .all()
    )
    return {row.source_record_id: row for row in rows}


def bulk_insert_offenses(db: Session, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        return
    db.execute(Offense.__table__.insert(), rows)


def bulk_update_offenses(db: Session, rows: Sequence[dict[str, Any]]) -> None:
    """Each row must include ``id`` (the offense's own primary key) plus
    the columns to update — bulk UPDATE-by-primary-key via executemany.
    """
    if not rows:
        return
    stmt = (
        update(Offense.__table__)
        .where(Offense.__table__.c.id == bindparam("_id"))
        .values(
            raw_offense_code=bindparam("raw_offense_code"),
            fbi_code=bindparam("fbi_code"),
            source_category=bindparam("source_category"),
            source_subcategory=bindparam("source_subcategory"),
            victim_count=bindparam("victim_count"),
            updated_at=bindparam("updated_at"),
        )
    )
    params = [{**row, "_id": row["id"]} for row in rows]
    for p in params:
        p.pop("id", None)
    db.execute(stmt, params)
