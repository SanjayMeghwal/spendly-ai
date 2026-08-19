"""Tests for application configuration.

Config validation is a security control, not bookkeeping. These settings fail
at STARTUP by design - the alternative is a process that boots happily and
signs tokens with a guessable key until somebody notices.
"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings

VALID_DB_URL = "postgresql+asyncpg://user:pw@localhost:5433/db"
VALID_KEY = "x" * 32


class TestSecretKeyStrength:
    """A weak signing key must stop the process."""

    @pytest.mark.parametrize(
        "weak_key",
        [
            pytest.param("", id="empty"),
            pytest.param("secret", id="the-classic"),
            pytest.param("x" * 31, id="one-char-short"),
        ],
    )
    def test_rejects_a_key_that_is_too_short(self, weak_key: str) -> None:
        """An HS256 signature is only as strong as its key.

        Given any token we have issued, an attacker can try candidate keys
        offline at full speed - no network, no rate limiting, no logs. A short
        key falls quickly, and then they can forge a token for ANY user that
        verifies perfectly.

        This has to fail at startup. A weak key produces no errors at runtime;
        everything works exactly as normal while protecting nothing.
        """
        with pytest.raises(ValidationError, match="at least 32 characters"):
            Settings(SECRET_KEY=weak_key, DATABASE_URL=VALID_DB_URL)  # type: ignore[arg-type]

    def test_accepts_a_key_at_the_boundary(self) -> None:
        """Exactly 32 is enough - it matches the HMAC-SHA256 output size."""
        settings = Settings(SECRET_KEY=VALID_KEY, DATABASE_URL=VALID_DB_URL)  # type: ignore[arg-type]

        assert settings.SECRET_KEY.get_secret_value() == VALID_KEY

    def test_secret_key_is_masked_in_repr(self) -> None:
        """Settings objects reach log lines and tracebacks.

        SecretStr renders as '**********', so dumping the config - which is a
        normal thing to do while debugging - cannot publish the signing key.
        """
        settings = Settings(SECRET_KEY=VALID_KEY, DATABASE_URL=VALID_DB_URL)  # type: ignore[arg-type]

        assert VALID_KEY not in repr(settings)
        assert VALID_KEY not in str(settings.SECRET_KEY)


class TestRequiredSettings:
    """Absent configuration must be an error, never a default."""

    def test_the_running_app_has_a_secret_key(self) -> None:
        """Guards the wiring: .env, CI env, and the field name must agree.

        A rename here would otherwise surface as an unrelated failure much
        later, or - far worse - as a silent fallback.
        """
        assert len(get_settings().SECRET_KEY.get_secret_value()) >= 32
