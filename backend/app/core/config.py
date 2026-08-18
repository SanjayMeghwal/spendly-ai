"""Application configuration.

Config is read from environment variables (and a local `.env` file during
development), then validated. Following the Twelve-Factor App principle,
configuration lives in the environment and never in source code.

The key property here is FAIL FAST: if a required variable is missing or
malformed, the application refuses to start with a readable error naming the
offending field. The alternative - starting successfully and crashing on the
first database query hours later - is far more expensive to debug.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict

# `.env` lives at the repository root, but the app runs from `backend/`.
# Computing the path from this file's location means it resolves correctly
# no matter which directory the process was launched from.
#   config.py -> core -> app -> backend -> <repo root>
ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    """Validated application settings.

    Every field is typed. Pydantic coerces and validates on instantiation, so
    an invalid DATABASE_URL raises at startup rather than at query time.
    """

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=True,
        # `.env` also holds POSTGRES_USER, POSTGRES_PASSWORD, etc. for
        # docker-compose. Those are not application settings, so ignore them
        # rather than failing on unexpected keys.
        extra="ignore",
    )

    # --- Application metadata -------------------------------------------------
    PROJECT_NAME: str = "Spendly AI"
    API_V1_PREFIX: str = "/api/v1"

    # --- Environment ----------------------------------------------------------
    # Literal (not str) so a typo like "prod" fails validation immediately
    # instead of silently disabling production safeguards.
    ENVIRONMENT: Literal["local", "test", "production"] = "local"

    # --- Database -------------------------------------------------------------
    # PostgresDsn validates the scheme, host, port, and database name. A
    # malformed URL is rejected here, not by SQLAlchemy three layers deep.
    # No default: this MUST be provided, and its absence must be an error.
    DATABASE_URL: PostgresDsn

    @property
    def database_url(self) -> str:
        """DATABASE_URL as a plain string, which SQLAlchemy expects."""
        return str(self.DATABASE_URL)

    @property
    def is_production(self) -> bool:
        """Used to gate debug output, API docs, and error verbosity."""
        return self.ENVIRONMENT == "production"


@lru_cache
def get_settings() -> Settings:
    """Return the singleton Settings instance.

    Cached because reading and validating `.env` on every request would be
    wasteful, and because a single source of truth avoids subtle drift.

    Being a function (rather than a module-level constant) also makes it
    overridable in tests through FastAPI's dependency_overrides.
    """
    # No arguments: the pydantic-settings machinery populates every field from
    # the environment and `.env`. The pydantic mypy plugin understands this.
    return Settings()
