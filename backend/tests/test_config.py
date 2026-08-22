"""Tests for configuration validation.

Config validation is a security control here, not a convenience. A weak or
missing signing key does not fail loudly at runtime - it fails silently, by
making every token in the system forgeable. The only place to catch that is
startup, which is what these tests pin down.
"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings

VALID_DB_URL = "postgresql+asyncpg://user:password@localhost:5432/db"
VALID_SECRET = "x" * 32


def _settings(**overrides: object) -> Settings:
    """Build Settings from explicit values, ignoring any `.env` on disk.

    `_env_file=None` is the important part and was a real bug in the first
    version of this file: without it, pydantic-settings still loads the
    developer's `.env`, so a test asserting that an omitted field is rejected
    quietly passed because `.env` supplied the value. The test could not fail,
    which is worse than not having it.

    Explicit keyword arguments plus no env file means these tests assert the
    behaviour of the Settings class itself, identically on every machine.
    """
    values: dict[str, object] = {"DATABASE_URL": VALID_DB_URL, "SECRET_KEY": VALID_SECRET}
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


class TestSecretKey:
    def test_accepts_a_key_at_the_minimum_length(self) -> None:
        """32 characters is the boundary, and must be allowed."""
        assert _settings(SECRET_KEY="y" * 32).SECRET_KEY.get_secret_value() == "y" * 32

    def test_rejects_a_short_key(self) -> None:
        """HS256 with a short key is brute-forceable offline.

        An attacker holding one valid token can grind candidate keys against
        its signature until one verifies, then mint tokens for any account.
        Failing at startup is the difference between a deployment that refuses
        to boot and one that silently has no authentication at all.
        """
        with pytest.raises(ValidationError, match="at least 32 characters"):
            _settings(SECRET_KEY="too-short")

    def test_requires_a_key_to_be_present(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """There is deliberately no default.

        A default would mean every deployment that forgot to set one shared a
        single publicly-known signing key.

        Both sources of the value have to be removed for this to mean anything:
        `_env_file=None` ignores the `.env` a developer has locally, and
        delenv removes the real environment variable CI sets. Miss either and
        the test passes without ever exercising the missing-key path.
        """
        monkeypatch.delenv("SECRET_KEY", raising=False)

        with pytest.raises(ValidationError):
            Settings(_env_file=None, DATABASE_URL=VALID_DB_URL)  # type: ignore[call-arg]

    def test_is_masked_in_the_representation(self) -> None:
        """SecretStr keeps the key out of logs, reprs, and tracebacks.

        Config objects get printed during debugging and captured by error
        trackers. Reading the value has to be a deliberate act.
        """
        settings = _settings(SECRET_KEY="a-very-secret-key-that-is-long-enough")

        assert "a-very-secret-key" not in repr(settings)
        assert "a-very-secret-key" not in str(settings.SECRET_KEY)


class TestTokenLifetime:
    def test_defaults_to_thirty_minutes(self) -> None:
        assert _settings().ACCESS_TOKEN_EXPIRE_MINUTES == 30

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param(0, id="zero"),
            pytest.param(-1, id="negative"),
            pytest.param(1441, id="over_24_hours"),
        ],
    )
    def test_rejects_an_unusable_lifetime(self, value: int) -> None:
        """A token cannot be revoked, so its lifetime is its blast radius.

        Zero or negative would mint tokens that are already expired; a very
        long one turns a single leaked token into indefinite access.
        """
        with pytest.raises(ValidationError):
            _settings(ACCESS_TOKEN_EXPIRE_MINUTES=value)


class TestEnvironment:
    def test_rejects_an_unrecognised_environment(self) -> None:
        """Literal, not str, so a typo cannot silently disable safeguards.

        `ENVIRONMENT=prod` against a `str` field would leave is_production
        False - quietly publishing the interactive API docs and verbose errors
        in production.
        """
        with pytest.raises(ValidationError):
            _settings(ENVIRONMENT="prod")

    def test_is_production_only_for_production(self) -> None:
        assert _settings(ENVIRONMENT="production").is_production is True
        assert _settings(ENVIRONMENT="local").is_production is False
        assert _settings(ENVIRONMENT="test").is_production is False
