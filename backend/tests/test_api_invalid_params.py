"""Invalid query parameter handling across the incidents/aggregate/
summary/status endpoints. No fixture data needed -- these are all
request-validation failures that should be rejected before any query
runs. Never depends on Chicago's live API.
"""

from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import app


def _client(db):
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_incidents_rejects_limit_above_max(db):
    client = _client(db)
    try:
        response = client.get("/api/incidents", params={"limit": 100000})
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert response.status_code == 422


def test_incidents_rejects_negative_offset(db):
    client = _client(db)
    try:
        response = client.get("/api/incidents", params={"offset": -1})
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert response.status_code == 422


def test_incidents_rejects_malformed_date(db):
    client = _client(db)
    try:
        response = client.get("/api/incidents", params={"start_date": "not-a-date"})
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert response.status_code == 422


def test_aggregate_rejects_zoom_out_of_range(db):
    client = _client(db)
    try:
        response = client.get("/api/incidents/aggregate", params={"zoom": 99})
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert response.status_code == 422


def test_aggregate_rejects_negative_zoom(db):
    client = _client(db)
    try:
        response = client.get("/api/incidents/aggregate", params={"zoom": -1})
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert response.status_code == 422


def test_aggregate_rejects_partial_bbox(db):
    client = _client(db)
    try:
        response = client.get(
            "/api/incidents/aggregate", params={"zoom": 10, "min_lon": -87.7, "min_lat": 41.8}
        )
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert response.status_code == 422


def test_summary_rejects_malformed_date(db):
    client = _client(db)
    try:
        response = client.get("/api/summary", params={"start_date": "13/45/2026"})
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert response.status_code == 422


def test_status_ignores_unknown_query_params(db):
    """/api/status takes no query parameters -- confirm an unexpected
    one is simply ignored (FastAPI's default), not a 500."""
    client = _client(db)
    try:
        response = client.get("/api/status", params={"unexpected": "value"})
    finally:
        app.dependency_overrides.pop(get_db, None)
    assert response.status_code == 200
