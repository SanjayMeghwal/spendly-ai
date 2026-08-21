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

from pydantic import PostgresDsn, SecretStr, field_validator
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

    # --- Authentication -------------------------------------------------------
    # The key that signs every JWT. NO DEFAULT, deliberately.
    #
    # A fallback value here would be the single most dangerous line in the
    # codebase: this file is committed to a PUBLIC repository, so a default
    # would be a published signing key. Anyone could mint a token for any
    # user. Every real breach of this kind started as a convenient default
    # that nobody remembered to override in production.
    #
    # SecretStr keeps it out of logs and tracebacks: repr(settings) renders it
    # as SecretStr('**********') rather than the key itself. Reading it needs
    # an explicit .get_secret_value().
    SECRET_KEY: SecretStr

    # HS256 is symmetric: the same key signs and verifies. That is correct
    # while one application does both. If a separate service ever needs to
    # VERIFY tokens without being able to MINT them, this must become RS256 -
    # asymmetric, so the verifier holds only the public key.
    JWT_ALGORITHM: str = "HS256"

    # Access tokens carry no server-side state, so this value IS the exposure
    # window for a stolen one: nothing can shorten it after the fact.
    #
    # 15 minutes rather than 30 - lowered when refresh tokens arrived, which
    # is exactly the trade they buy. Before them, a short lifetime meant
    # re-entering a password every 30 minutes, so the number was a compromise
    # between security and irritation. Now a client silently exchanges its
    # refresh token for a new access token in the background, and the user
    # notices nothing, so the only remaining pressure on this number is
    # downward.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15

    # Refresh tokens ARE revocable - each one has a row in `refresh_tokens` -
    # so this is not an exposure window in the same sense. It is how long a
    # session may live before the user must type their password again.
    #
    # Measured in DAYS, not minutes, because that is the point: the long-lived
    # credential is the one we can switch off, and the short-lived one is the
    # one we cannot.
    #
    # This is an ABSOLUTE lifetime, not a sliding one. Rotating a refresh
    # token does not extend it (see services/refresh.py), so a session ends 30
    # days after LOGIN no matter how actively it is used. A sliding window
    # would let a stolen token be renewed forever.
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    @field_validator("SECRET_KEY")
    @classmethod
    def secret_key_must_be_strong(cls, value: SecretStr) -> SecretStr:
        """Reject a key short enough to brute-force offline.

        An HS256 signature is only as strong as its key. Given any token we
        issue, an attacker can try candidate keys offline at full speed until
        one reproduces the signature - no network, no rate limit, no logs.
        A short or guessable key means they can then forge a token for ANY
        user, and it will verify perfectly.

        32 bytes matches the HMAC-SHA256 output size; beyond that, extra
        length adds nothing. Generate one with:

            python -c "import secrets; print(secrets.token_urlsafe(32))"

        This runs at STARTUP, so a weak key stops the process rather than
        silently protecting nothing.
        """
        if len(value.get_secret_value()) < 32:
            raise ValueError(
                "SECRET_KEY must be at least 32 characters. Generate one with: "
                'python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )
        return value

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
