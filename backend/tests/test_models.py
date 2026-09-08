"""Migration sanity checks and ORM model/relationship tests."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.models.enums import LocationPrecision
from app.models.incident import Incident
from app.models.offense import Offense
from app.models.source import Source
from app.models.source_record import SourceRecord


def test_migrations_created_expected_tables(engine):
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    expected = {
        "sources",
        "ingestion_runs",
        "source_records",
        "raw_source_record_versions",
        "incidents",
        "offenses",
    }
    assert expected.issubset(tables)


def test_postgis_extension_is_active(db):
    version = db.execute(text("SELECT PostGIS_version()")).scalar_one()
    assert version  # any non-empty string means the extension is active


def _make_source(db, key="test-source") -> Source:
    source = Source(source_key=key, name="Test Source")
    db.add(source)
    db.flush()
    return source


def test_incident_with_multiple_offenses(db):
    source = _make_source(db)
    incident = Incident(
        source_id=source.id,
        external_incident_id="INC-1",
        location_precision=LocationPrecision.UNKNOWN,
    )
    db.add(incident)
    db.flush()

    offense_a = Offense(
        incident_id=incident.id,
        source_id=source.id,
        external_offense_id="INC-1-A",
        source_category="theft",
    )
    offense_b = Offense(
        incident_id=incident.id,
        source_id=source.id,
        external_offense_id="INC-1-B",
        source_category="vandalism",
    )
    db.add_all([offense_a, offense_b])
    db.flush()
    db.refresh(incident)

    assert len(incident.offenses) == 2
    assert {o.external_offense_id for o in incident.offenses} == {"INC-1-A", "INC-1-B"}
    assert offense_a.incident is incident


def test_external_source_id_uniqueness(db):
    source = _make_source(db)
    now = datetime.now(timezone.utc)
    db.add(
        SourceRecord(
            source_id=source.id,
            external_record_id="DUP-1",
            first_seen_at=now,
            last_seen_at=now,
            current_fingerprint="a" * 64,
        )
    )
    db.flush()

    db.add(
        SourceRecord(
            source_id=source.id,
            external_record_id="DUP-1",
            first_seen_at=now,
            last_seen_at=now,
            current_fingerprint="b" * 64,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_same_external_id_allowed_across_different_sources(db):
    source_a = _make_source(db, key="source-a")
    source_b = _make_source(db, key="source-b")
    now = datetime.now(timezone.utc)
    db.add(
        SourceRecord(
            source_id=source_a.id,
            external_record_id="SAME-ID",
            first_seen_at=now,
            last_seen_at=now,
            current_fingerprint="a" * 64,
        )
    )
    db.add(
        SourceRecord(
            source_id=source_b.id,
            external_record_id="SAME-ID",
            first_seen_at=now,
            last_seen_at=now,
            current_fingerprint="b" * 64,
        )
    )
    db.flush()  # should not raise


def test_incident_geographic_field_round_trip(db):
    from geoalchemy2 import WKTElement

    source = _make_source(db)
    incident = Incident(
        source_id=source.id,
        external_incident_id="GEO-1",
        latitude=39.7392,
        longitude=-104.9903,
        location=WKTElement("POINT(-104.9903 39.7392)", srid=4326),
        location_precision=LocationPrecision.EXACT,
    )
    db.add(incident)
    db.flush()

    lon, lat = db.execute(
        text(
            "SELECT ST_X(location::geometry), ST_Y(location::geometry) "
            "FROM incidents WHERE id = :id"
        ),
        {"id": incident.id},
    ).one()
    assert lon == pytest.approx(-104.9903, abs=1e-6)
    assert lat == pytest.approx(39.7392, abs=1e-6)


def test_incident_without_coordinates_has_null_location(db):
    source = _make_source(db)
    incident = Incident(
        source_id=source.id,
        external_incident_id="NO-GEO-1",
        location_precision=LocationPrecision.SUPPRESSED,
    )
    db.add(incident)
    db.flush()
    db.refresh(incident)
    assert incident.location is None
    assert incident.location_precision == LocationPrecision.SUPPRESSED
