"""Tests for the user service, called directly rather than over HTTP.

These exist alongside test_auth.py on purpose. The API tests prove the whole
stack works end to end; these prove the business rules hold when the service is
called from anywhere else - a CLI command, a background job, a future admin
tool. Any rule enforced only by the HTTP layer is a rule that stops applying
the moment something else calls the service.
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.schemas.user import UserCreate
from app.services import user_service
from app.services.exceptions import EmailAlreadyRegisteredError
from tests.conftest import TEST_EMAIL, TEST_PASSWORD

pytestmark = pytest.mark.integration


class TestRegisterUser:
    async def test_persists_a_user(self, db_session: AsyncSession) -> None:
        user = await user_service.register_user(
            db_session,
            UserCreate(email="grace@example.com", password="a long enough password"),
        )

        assert user.id is not None
        assert user.email == "grace@example.com"
        # Defaults applied by the database, not guessed by the application.
        assert user.is_active is True
        assert user.is_superuser is False
        assert user.created_at is not None

    async def test_never_stores_the_plaintext(self, db_session: AsyncSession) -> None:
        password = "a long enough password"

        user = await user_service.register_user(
            db_session, UserCreate(email="grace@example.com", password=password)
        )

        assert user.hashed_password != password
        assert password not in user.hashed_password

    async def test_raises_on_a_duplicate_email(
        self, db_session: AsyncSession, registered_user: User
    ) -> None:
        """A domain exception, NOT an HTTPException.

        This is the layering rule made testable: the service knows nothing
        about HTTP, so it cannot raise a status code. Translating this into a
        409 is the API layer's job, and that is why the service stays callable
        from a CLI or a worker.
        """
        with pytest.raises(EmailAlreadyRegisteredError):
            await user_service.register_user(
                db_session,
                UserCreate(email=TEST_EMAIL, password="a long enough password"),
            )

    async def test_a_failed_registration_leaves_no_row_behind(
        self, db_session: AsyncSession, registered_user: User
    ) -> None:
        """A rejected registration must not half-write anything.

        If the rollback were missing, the session would be left holding a
        pending INSERT that the NEXT successful commit would flush - creating a
        row nobody asked for, in an unrelated request.
        """
        with pytest.raises(EmailAlreadyRegisteredError):
            await user_service.register_user(
                db_session, UserCreate(email=TEST_EMAIL, password="a long enough password")
            )

        # The session must still be usable, and must contain exactly the one
        # user the fixture created.
        found = await user_service.get_user_by_email(db_session, TEST_EMAIL)
        assert found is not None
        assert found.id == registered_user.id


class TestLookups:
    async def test_finds_a_user_by_email(
        self, db_session: AsyncSession, registered_user: User
    ) -> None:
        found = await user_service.get_user_by_email(db_session, TEST_EMAIL)

        assert found is not None
        assert found.id == registered_user.id

    @pytest.mark.parametrize(
        "variant",
        [
            pytest.param("ADA@EXAMPLE.COM", id="uppercase"),
            pytest.param("Ada@Example.com", id="mixed_case"),
            pytest.param("  ada@example.com  ", id="surrounding_whitespace"),
        ],
    )
    async def test_email_lookup_normalises_its_input(
        self, db_session: AsyncSession, registered_user: User, variant: str
    ) -> None:
        """Normalisation must live in the service, not only in the schema.

        The Pydantic schema lowercases what arrives over HTTP, but a caller
        that bypasses it - a test, a script, a future admin command - would
        otherwise silently fail to find an existing user and conclude the
        address is free.
        """
        found = await user_service.get_user_by_email(db_session, variant)

        assert found is not None
        assert found.id == registered_user.id

    async def test_returns_none_for_an_unknown_email(self, db_session: AsyncSession) -> None:
        assert await user_service.get_user_by_email(db_session, "nobody@example.com") is None

    async def test_returns_none_for_an_unknown_id(self, db_session: AsyncSession) -> None:
        """Exercised on every authenticated request, for deleted accounts."""
        assert await user_service.get_user_by_id(db_session, uuid.uuid4()) is None


class TestAuthenticateUser:
    async def test_accepts_correct_credentials(
        self, db_session: AsyncSession, registered_user: User
    ) -> None:
        user = await user_service.authenticate_user(db_session, TEST_EMAIL, TEST_PASSWORD)

        assert user is not None
        assert user.id == registered_user.id

    async def test_rejects_a_wrong_password(
        self, db_session: AsyncSession, registered_user: User
    ) -> None:
        assert await user_service.authenticate_user(db_session, TEST_EMAIL, "wrong") is None

    async def test_rejects_an_unknown_email(self, db_session: AsyncSession) -> None:
        assert (
            await user_service.authenticate_user(db_session, "nobody@example.com", TEST_PASSWORD)
            is None
        )

    async def test_rejects_a_deactivated_account(
        self, db_session: AsyncSession, inactive_user: User
    ) -> None:
        """Valid password, but the account is switched off.

        Deactivating rather than deleting is what preserves a user's financial
        history; this is the check that makes deactivation actually mean
        something.
        """
        assert (
            await user_service.authenticate_user(
                db_session, "deactivated@example.com", TEST_PASSWORD
            )
            is None
        )

    async def test_returns_none_rather_than_raising_for_every_failure(
        self, db_session: AsyncSession, registered_user: User, inactive_user: User
    ) -> None:
        """One indistinguishable outcome for three different causes.

        Unknown email, wrong password, and deactivated account all return None,
        so the caller *cannot* accidentally build a response that reveals which
        one it was. Making the enumeration-safe behaviour the only available
        behaviour beats documenting it.
        """
        outcomes = [
            await user_service.authenticate_user(db_session, "nobody@example.com", TEST_PASSWORD),
            await user_service.authenticate_user(db_session, TEST_EMAIL, "wrong"),
            await user_service.authenticate_user(
                db_session, "deactivated@example.com", TEST_PASSWORD
            ),
        ]

        assert outcomes == [None, None, None]


class TestConcurrentRegistration:
    """The branch that only a race can reach in production."""

    async def test_a_lost_race_still_produces_a_clean_domain_error(
        self,
        db_session: AsyncSession,
        registered_user: User,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Two simultaneous registrations for one address must not yield a 500.

        THE RACE: register_user does a SELECT to check the address is free,
        then an INSERT. Two requests can both run the SELECT before either
        INSERTs. Both see "free", both proceed, and the second INSERT violates
        the UNIQUE constraint.

        WHY THIS IS MONKEYPATCHED: the interleaving is real but timing-
        dependent, and a test that has to win a race to pass is a flaky test.
        Forcing the pre-check to report "no such user" reproduces the exact
        state the losing request finds itself in, deterministically.

        Note what is NOT faked: the database, the constraint, and the INSERT
        are all real. The unique violation genuinely happens - only the
        scheduling that causes it is arranged rather than raced for.
        """

        async def pretend_email_is_free(*args: object, **kwargs: object) -> User | None:
            return None

        monkeypatch.setattr(user_service, "get_user_by_email", pretend_email_is_free)

        with pytest.raises(EmailAlreadyRegisteredError):
            await user_service.register_user(
                db_session,
                UserCreate(email=TEST_EMAIL, password="a long enough password"),
            )

    async def test_the_session_is_usable_after_a_lost_race(
        self,
        db_session: AsyncSession,
        registered_user: User,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The rollback in the IntegrityError handler is doing real work.

        Without it the session is left in a failed transaction, and EVERY
        subsequent query on it raises PendingRollbackError - so one duplicate
        registration would poison the rest of that request.
        """

        async def pretend_email_is_free(*args: object, **kwargs: object) -> User | None:
            return None

        monkeypatch.setattr(user_service, "get_user_by_email", pretend_email_is_free)

        with pytest.raises(EmailAlreadyRegisteredError):
            await user_service.register_user(
                db_session, UserCreate(email=TEST_EMAIL, password="a long enough password")
            )

        # The session must still work. Undo the patch so the real lookup runs.
        monkeypatch.undo()
        found = await user_service.get_user_by_email(db_session, TEST_EMAIL)
        assert found is not None


class TestPasswordRehashing:
    async def test_a_successful_login_upgrades_a_weak_hash(
        self,
        db_session: AsyncSession,
        registered_user: User,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Hashing parameters must be able to rise over time.

        An old hash cannot be upgraded in place - the plaintext is gone. The
        one moment it is briefly available again is a successful login, which
        is why the upgrade lives there.

        `password_needs_rehash` is forced to True rather than actually creating
        a hash with weaker parameters, because argon2-cffi offers no supported
        way to mint a deliberately outdated hash, and hand-crafting one would
        be testing our forgery rather than the upgrade path.
        """
        original_hash = registered_user.hashed_password

        monkeypatch.setattr(user_service, "password_needs_rehash", lambda _: True)

        user = await user_service.authenticate_user(db_session, TEST_EMAIL, TEST_PASSWORD)

        assert user is not None
        assert user.hashed_password != original_hash
        # Still a valid hash for the SAME password - the upgrade must not lock
        # the user out of their own account.
        monkeypatch.undo()
        assert await user_service.authenticate_user(db_session, TEST_EMAIL, TEST_PASSWORD)

    async def test_a_current_hash_is_left_alone(
        self, db_session: AsyncSession, registered_user: User
    ) -> None:
        """Rehashing on every login would double the cost of the endpoint."""
        original_hash = registered_user.hashed_password

        user = await user_service.authenticate_user(db_session, TEST_EMAIL, TEST_PASSWORD)

        assert user is not None
        assert user.hashed_password == original_hash
