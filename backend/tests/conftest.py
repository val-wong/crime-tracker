"""Shared test fixtures.

Tests run against a real PostgreSQL/PostGIS database (see
app.config.Settings.resolved_test_database_url), never SQLite —
JSONB and PostGIS geography behavior can't be faithfully exercised
against SQLite, and this project's schema depends on both. See
README.md for how to start the database locally.

Isolation strategy: each test runs inside one DB transaction that is
rolled back afterwards, so tests never see each other's data and never
need to worry about cleanup order across foreign keys.
"""

import os

import psycopg
import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session

from alembic import command
from alembic.config import Config
from app.config import get_settings

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _ensure_database_exists(database_url: str) -> None:
    """Create the target database if it doesn't exist yet.

    Connects to Postgres' own "postgres" maintenance database to issue
    CREATE DATABASE, since you can't create a database while connected
    to it.
    """
    url = make_url(database_url)
    target_db = url.database
    maintenance_conninfo = (
        f"host={url.host} port={url.port} user={url.username} "
        f"password={url.password} dbname=postgres"
    )
    with psycopg.connect(maintenance_conninfo, autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (target_db,)
        ).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{target_db}"')


def _run_migrations(database_url: str) -> None:
    alembic_cfg = Config(os.path.join(BACKEND_ROOT, "alembic.ini"))
    alembic_cfg.set_main_option("script_location", os.path.join(BACKEND_ROOT, "alembic"))
    os.environ["DATABASE_URL"] = database_url
    command.upgrade(alembic_cfg, "head")


@pytest.fixture(scope="session")
def test_database_url() -> str:
    return get_settings().resolved_test_database_url


@pytest.fixture(scope="session")
def engine(test_database_url):
    _ensure_database_exists(test_database_url)
    _run_migrations(test_database_url)
    eng = create_engine(test_database_url, future=True)
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine) -> Session:
    """A Session bound to one transaction, rolled back after the test."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, future=True)
    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    """Settings uses lru_cache; clear it so env var changes take effect
    across tests that need distinct DATABASE_URL values."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
