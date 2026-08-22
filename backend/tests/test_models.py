"""Tests for ORM model behaviour that is not covered by exercising an endpoint."""

from app.core.security import hash_password
from app.models.user import User


class TestUserRepr:
    """__repr__ ends up in log files, error trackers, and terminal scrollback."""

    def test_does_not_leak_the_password_hash(self) -> None:
        """A repr is the easiest accidental route for credential material to escape.

        Any unhandled exception involving a User can put its repr into a log
        line or an error-tracker payload - places with a much longer retention
        and a much wider audience than the database itself.
        """
        user = User(
            email="ada@example.com",
            hashed_password=hash_password("a sufficiently long password"),
        )

        rendered = repr(user)

        assert "argon2" not in rendered
        assert user.hashed_password not in rendered

    def test_identifies_the_user_usefully(self) -> None:
        """It must still be worth reading, or people will add a worse one."""
        user = User(email="ada@example.com", hashed_password="irrelevant")

        rendered = repr(user)

        assert "ada@example.com" in rendered
