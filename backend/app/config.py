"""Application configuration.

Values are read from environment variables (see ../.env.example at the
repo root).
"""

from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://crime_tracker:crime_tracker@localhost:5433/crime_tracker"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    backend_host: str = "0.0.0.0"
    backend_port: int = 8000
    # Comma-separated. Vite's default dev port is 5173, but it picks the
    # next free port (5174, ...) when 5173 is already in use, so both are
    # allowed here for local development.
    backend_cors_origins: str = "http://localhost:5173,http://localhost:5174"

    database_url: str = _DEFAULT_DATABASE_URL
    # Only used by the test suite; defaults to DATABASE_URL + "_test" so
    # tests never run against the same database as local development.
    test_database_url: Optional[str] = None

    # How many consecutive *complete* reconciliation runs must miss a
    # source record before it is considered confirmed-removed. See
    # docs/ingestion-framework.md.
    removal_confirmation_runs: int = 2

    # Optional Socrata app token for Chicago requests. Never required —
    # anonymous access works (see docs/sources/chicago.md §9) — but an
    # app token avoids the shared, unthrottled-guarantee-free anonymous
    # IP pool for real, repeated ingestion. Never commit a real value;
    # see .env.example.
    chicago_app_token: Optional[str] = None

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.backend_cors_origins.split(",") if origin.strip()]

    @property
    def resolved_test_database_url(self) -> str:
        if self.test_database_url:
            return self.test_database_url
        return f"{self.database_url}_test"


@lru_cache
def get_settings() -> Settings:
    return Settings()
