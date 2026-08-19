"""Tests for the User model.

These verify the SCHEMA CONTRACT, not Python attribute assignment. Asserting
that `User(email=x).email == x` would test SQLAlchemy, not our design. What is
worth testing is what the DATABASE guarantees: which values it refuses, and
which it fills in for us.

Every test here needs PostgreSQL. That is the point - CHECK constraints and
TIMESTAMPTZ are exactly what a SQLite stand-in would silently fake.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User


def make_user(email: str = "sanjay@example.com", **overrides: object) -> User:
    """Build an unsaved User with sensible defaults.

    The hash here is a placeholder string, not a real Argon2 hash. This module
    tests the storage layer; hashing is tested where hashing lives.
    """
    fields: dict[str, object] = {
        "email": email,
        "hashed_password": "placeholder-not-a-real-hash",
    }
    fields.update(overrides)
    return User(**fields)


@pytest.mark.integration
class TestPersistence:
    """A user round-trips through PostgreSQL intact."""

    async def test_user_can_be_saved_and_read_back(self, db_session: AsyncSession) -> None:
        """Proves the mapping, the driver, and the schema actually agree."""
        db_session.add(make_user())
        await db_session.commit()

        found = (
            await db_session.execute(select(User).where(User.email == "sanjay@example.com"))
        ).scalar_one()

        assert found.email == "sanjay@example.com"
        assert found.full_name is None

    async def test_database_supplies_the_defaults(self, db_session: AsyncSession) -> None:
        """id, is_active, and the timestamps must be filled in for us.

        If any of these were left to the caller, forgetting one would create a
        user row that is neither active nor inactive, or has no creation time -
        and we would only find out from a NULL in a report months later.
        """
        user = make_user()
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        assert isinstance(user.id, uuid.UUID)
        assert user.is_active is True
        assert user.created_at is not None
        assert user.updated_at is not None

    async def test_timestamps_are_timezone_aware_and_utc(self, db_session: AsyncSession) -> None:
        """TIMESTAMPTZ, not TIMESTAMP.

        A naive datetime is a number with no meaning attached. In a finance
        application that makes "which month did this transaction fall in?"
        unanswerable across a timezone boundary. This test fails loudly if
        someone ever drops `timezone=True` from the column.
        """
        user = make_user()
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)

        assert user.created_at.tzinfo is not None, "created_at is naive - column lost TIMESTAMPTZ"
        # Assigned by PostgreSQL's now(), so it must be within a few seconds
        # of ours. A generous window keeps this from flaking on a slow runner.
        assert abs((datetime.now(UTC) - user.created_at).total_seconds()) < 60


@pytest.mark.integration
class TestEmailConstraints:
    """The database - not application code - is the last line of defence."""

    async def test_duplicate_email_is_rejected(self, db_session: AsyncSession) -> None:
        """Two accounts must never share a mailbox.

        Checking "does this email exist?" in Python before inserting is NOT
        enough: two concurrent registrations can both find nothing and both
        insert. Only the unique constraint actually prevents it.
        """
        db_session.add(make_user("taken@example.com"))
        await db_session.commit()

        db_session.add(make_user("taken@example.com"))
        with pytest.raises(IntegrityError) as exc:
            await db_session.commit()

        assert "uq_users_email" in str(exc.value)

    async def test_mixed_case_email_is_rejected(self, db_session: AsyncSession) -> None:
        """The lowercase rule is enforced by the database, not by convention.

        Without the CHECK constraint, 'Sanjay@example.com' and
        'sanjay@example.com' are different strings to a case-sensitive UNIQUE
        index - so one person registers twice and login becomes ambiguous.
        Normalising in the service layer only protects the code paths that
        remember to normalise; this protects all of them.
        """
        db_session.add(make_user("Sanjay@Example.com"))

        with pytest.raises(IntegrityError) as exc:
            await db_session.commit()

        assert "ck_users_email_lowercase" in str(exc.value)


class TestRepr:
    """__repr__ output reaches logs and error trackers. It must stay clean."""

    def test_repr_never_exposes_the_password_hash(self) -> None:
        """A hash in a log is a credential leak.

        Holding the hash is what makes offline brute-forcing possible, so it
        must never reach a log line, a traceback, or an error tracker. No
        database needed: this is pure formatting.
        """
        user = make_user(hashed_password="$argon2id$v=19$m=65536,t=3,p=4$SECRET")

        rendered = repr(user)

        assert "SECRET" not in rendered
        assert "argon2" not in rendered
        assert "sanjay@example.com" in rendered
