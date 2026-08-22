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

from pydantic import Field, PostgresDsn, SecretStr, field_validator
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

    # --- Authentication -------------------------------------------------------
    # SecretStr, not str: pydantic masks it as `SecretStr('**********')` in
    # every repr, log line, and traceback. Reading it requires an explicit
    # .get_secret_value(), so leaking it becomes a deliberate act rather than
    # an accident in an error message.
    #
    # No default. A default here would be catastrophic: every deployment that
    # forgot to set it would share one publicly-known signing key, and anyone
    # could mint a valid token for any account.
    SECRET_KEY: SecretStr

    # HS256 is symmetric - the same key signs and verifies. That is right while
    # one service does both. If a separate service ever needs to verify tokens
    # without the power to mint them, this moves to RS256 (private key signs,
    # public key verifies). Literal, so a typo fails at startup.
    JWT_ALGORITHM: Literal["HS256"] = "HS256"

    # Short by design. An access token cannot be revoked before it expires, so
    # this value IS the blast radius of a stolen token. 30 minutes is the
    # common floor; it gets more comfortable once refresh tokens exist.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=30, gt=0, le=1440)

    @field_validator("SECRET_KEY")
    @classmethod
    def _secret_key_must_be_strong(cls, v: SecretStr) -> SecretStr:
        """Reject a signing key that is too short to be safe.

        HS256 is HMAC-SHA256, so a key shorter than the 32-byte hash output
        adds no security and is brute-forceable offline: an attacker holding
        one token can grind candidate keys until the signature verifies, then
        mint tokens for any account. Checked here so the failure is a readable
        startup error rather than a silent weakness.

        Generate one with:
            python -c "import secrets; print(secrets.token_urlsafe(48))"
        """
        if len(v.get_secret_value()) < 32:
            raise ValueError(
                "SECRET_KEY must be at least 32 characters. Generate one with: "
                'python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        return v

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
