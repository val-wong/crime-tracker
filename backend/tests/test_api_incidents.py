from fastapi.testclient import TestClient

from app.db.session import get_db
from app.main import app


def test_incidents_empty_on_clean_database(db):
    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        response = client.get("/api/incidents")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0, "limit": 100, "offset": 0}
