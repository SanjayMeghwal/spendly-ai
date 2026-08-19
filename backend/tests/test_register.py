"""Tests for POST /api/v1/auth/register.

These run end to end - HTTP request, validation, service, real PostgreSQL -
because the things worth protecting here span all of those layers. A test that
called the service directly would not catch a response schema leaking the
password hash, and one that mocked the database would not catch the unique
constraint failing to fire.
"""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import MIN_PASSWORD_LENGTH, verify_password
from app.models import User
from app.services.user import EmailAlreadyRegistered, create_user

REGISTER_URL = "/api/v1/auth/register"
PASSWORD = "correct-horse-battery-staple"


def payload(**overrides: object) -> dict[str, object]:
    """A valid registration body, with fields overridable per test."""
    body: dict[str, object] = {
        "email": "ada@example.com",
        "password": PASSWORD,
        "full_name": "Ada Lovelace",
    }
    body.update(overrides)
    return body


@pytest.mark.integration
class TestSuccessfulRegistration:
    """The happy path, and what it must and must not return."""

    async def test_returns_201_with_the_created_user(self, db_client: AsyncClient) -> None:
        """201 Created, not 200. The status line is part of the contract."""
        response = await db_client.post(REGISTER_URL, json=payload())

        assert response.status_code == 201
        body = response.json()
        assert body["email"] == "ada@example.com"
        assert body["full_name"] == "Ada Lovelace"
        assert body["is_active"] is True
        assert body["id"]
        assert body["created_at"]

    async def test_response_never_exposes_the_password_or_its_hash(
        self, db_client: AsyncClient
    ) -> None:
        """The single most important assertion in this file.

        `response_model=UserRead` is what enforces this: FastAPI drops every
        attribute the schema does not declare. This test is what would notice
        if someone "helpfully" changed the route to return the ORM object
        unfiltered, or added the field to UserRead.
        """
        response = await db_client.post(REGISTER_URL, json=payload())

        assert response.status_code == 201
        body = response.json()
        assert "hashed_password" not in body
        assert "password" not in body
        # Belt and braces against the hash appearing under some other key.
        assert PASSWORD not in response.text
        assert "$argon2" not in response.text

    async def test_password_is_stored_hashed_and_verifies(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Proves we store a hash, and one that actually works.

        Asserting only "the column is not the plaintext" would pass if we
        stored a useless value like the reversed string, so the test also
        confirms the stored hash verifies against the original password.
        """
        await db_client.post(REGISTER_URL, json=payload())

        user = (
            await db_session.execute(select(User).where(User.email == "ada@example.com"))
        ).scalar_one()

        assert user.hashed_password != PASSWORD
        assert user.hashed_password.startswith("$argon2id$")
        assert verify_password(PASSWORD, user.hashed_password) is True

    async def test_full_name_is_optional(self, db_client: AsyncClient) -> None:
        response = await db_client.post(REGISTER_URL, json=payload(full_name=None))

        assert response.status_code == 201
        assert response.json()["full_name"] is None


@pytest.mark.integration
class TestEmailHandling:
    """Case normalisation, and the duplicate rules that depend on it."""

    async def test_email_is_normalised_to_lowercase(self, db_client: AsyncClient) -> None:
        """Mixed case in, lowercase stored and returned.

        Without this the CHECK constraint on the table would reject the row
        outright, so a failure here surfaces as a 500 rather than a duplicate.
        """
        response = await db_client.post(REGISTER_URL, json=payload(email="Ada@Example.COM"))

        assert response.status_code == 201
        assert response.json()["email"] == "ada@example.com"

    async def test_surrounding_whitespace_is_trimmed(self, db_client: AsyncClient) -> None:
        """A trailing space from a copy-paste must not create a second account."""
        response = await db_client.post(REGISTER_URL, json=payload(email="  ada@example.com  "))

        assert response.status_code == 201
        assert response.json()["email"] == "ada@example.com"

    async def test_duplicate_email_returns_409(self, db_client: AsyncClient) -> None:
        """409 Conflict - the request is valid, the state forbids it.

        Not 400 (malformed) and not 422 (unprocessable): the body is perfectly
        well formed, it simply conflicts with what already exists.
        """
        first = await db_client.post(REGISTER_URL, json=payload())
        assert first.status_code == 201

        second = await db_client.post(REGISTER_URL, json=payload())

        assert second.status_code == 409
        assert "already exists" in second.json()["detail"]

    async def test_duplicate_detection_ignores_case(self, db_client: AsyncClient) -> None:
        """The reason we normalise at all.

        Without normalisation these are two different strings to a
        case-sensitive UNIQUE index, so one person quietly gets two accounts
        and login becomes a coin flip between them.
        """
        await db_client.post(REGISTER_URL, json=payload(email="ada@example.com"))

        response = await db_client.post(REGISTER_URL, json=payload(email="ADA@EXAMPLE.COM"))

        assert response.status_code == 409

    async def test_error_response_leaks_no_internals(self, db_client: AsyncClient) -> None:
        """A 409 must not expose the schema behind it."""
        await db_client.post(REGISTER_URL, json=payload())

        text = (await db_client.post(REGISTER_URL, json=payload())).text.lower()

        for leak in ("traceback", "sqlalchemy", "uq_users_email", "integrityerror", "select"):
            assert leak not in text, f"409 response leaked {leak!r}"


@pytest.mark.integration
class TestValidation:
    """Rejected input must be rejected safely as well as correctly."""

    async def test_rejected_password_is_not_echoed_back(self, db_client: AsyncClient) -> None:
        """THE LEAK THIS SLICE EXISTS TO CLOSE.

        Pydantic puts the rejected value under an "input" key in every
        validation error, and FastAPI's DEFAULT handler serialises that
        straight into the response. A too-short password would therefore be
        returned to the client in plaintext and written to every log that
        records response bodies - and since people reuse passwords, the damage
        would not be limited to this application.

        Declaring the field `SecretStr` does not prevent it; pydantic still
        reports the raw input. app/api/errors.py is what closes it, by copying
        only an allowlist of safe keys out of each error.
        """
        bad_password = "short-one"
        assert len(bad_password) < MIN_PASSWORD_LENGTH

        response = await db_client.post(REGISTER_URL, json=payload(password=bad_password))

        assert response.status_code == 422
        assert bad_password not in response.text, (
            f"the rejected password was echoed back: {response.text}"
        )
        # The error must still be USEFUL - it should say which field failed.
        assert "password" in response.text

    async def test_rejects_a_password_below_the_minimum(self, db_client: AsyncClient) -> None:
        response = await db_client.post(REGISTER_URL, json=payload(password="tooshort"))

        assert response.status_code == 422

    async def test_rejects_a_password_above_the_maximum(self, db_client: AsyncClient) -> None:
        """Unbounded input would let an attacker force megabytes of hashing."""
        response = await db_client.post(REGISTER_URL, json=payload(password="a" * 129))

        assert response.status_code == 422

    @pytest.mark.parametrize(
        "bad_email",
        [
            pytest.param("not-an-email", id="no-at-sign"),
            pytest.param("@example.com", id="no-local-part"),
            pytest.param("ada@", id="no-domain"),
            pytest.param("", id="empty"),
        ],
    )
    async def test_rejects_a_malformed_email(self, db_client: AsyncClient, bad_email: str) -> None:
        response = await db_client.post(REGISTER_URL, json=payload(email=bad_email))

        assert response.status_code == 422

    async def test_rejects_a_missing_password(self, db_client: AsyncClient) -> None:
        body = payload()
        del body["password"]

        response = await db_client.post(REGISTER_URL, json=body)

        assert response.status_code == 422

    async def test_no_user_is_created_when_validation_fails(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Validation runs before the handler, so nothing should reach the DB."""
        await db_client.post(REGISTER_URL, json=payload(password="tooshort"))

        found = (
            await db_session.execute(select(User).where(User.email == "ada@example.com"))
        ).scalar_one_or_none()

        assert found is None


@pytest.mark.integration
class TestServiceLayer:
    """The service enforces its rules independently of the HTTP layer.

    Worth testing directly: these rules must hold for a CLI command or a
    background job too, neither of which goes through a route.
    """

    async def test_create_user_raises_a_domain_exception_not_an_http_one(
        self, db_session: AsyncSession
    ) -> None:
        """The layering rule, made executable.

        If someone raises HTTPException from the service to save a few lines,
        this test fails - the service would then be unusable outside a web
        request, and the business rules welded to FastAPI.
        """
        await create_user(db_session, email="ada@example.com", password=PASSWORD)

        with pytest.raises(EmailAlreadyRegistered):
            await create_user(db_session, email="ada@example.com", password=PASSWORD)

    async def test_session_is_usable_after_a_duplicate(self, db_session: AsyncSession) -> None:
        """The common duplicate path leaves the session healthy.

        NOTE this exercises the PRE-CHECK path only. The pre-check finds the
        existing row and raises before any INSERT, so no IntegrityError and no
        rollback occur here. The rollback is covered by the race test below -
        a distinction a mutation test forced into the open, since deleting the
        rollback left this test green.
        """
        await create_user(db_session, email="ada@example.com", password=PASSWORD)
        with pytest.raises(EmailAlreadyRegistered):
            await create_user(db_session, email="ada@example.com", password=PASSWORD)

        # The session must still work.
        found = (
            await db_session.execute(select(User).where(User.email == "ada@example.com"))
        ).scalar_one()
        assert found.email == "ada@example.com"

    async def test_a_non_duplicate_integrity_error_is_not_mislabelled(
        self, db_session: AsyncSession
    ) -> None:
        """A CHECK violation must NOT be reported as a duplicate email.

        Calling the service directly with a mixed-case address bypasses the
        schema's normalisation, exactly as a CLI command or a careless caller
        would. The `email = lower(email)` CHECK constraint then rejects it.

        That is a BUG in the caller, and it must surface as one. Reporting it
        as EmailAlreadyRegistered would hide a normalisation failure behind a
        plausible 409 indefinitely - and the user would be told an address is
        taken when it is not.

        This test exists because a mutation found the flaw: with the schema
        validator removed, the duplicate-detection test still passed, because
        the CHECK violation was being relabelled. It passed for the wrong
        reason, which is worse than failing.
        """
        with pytest.raises(IntegrityError):
            await create_user(db_session, email="NotLowercase@example.com", password=PASSWORD)

    async def test_losing_the_uniqueness_race_is_still_reported_as_a_duplicate(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Covers the path where the database, not Python, catches the duplicate.

        Two simultaneous registrations for the same address both run their
        pre-check SELECT, both find nothing, and both proceed to INSERT. One
        wins; the other hits the UNIQUE constraint. That is a
        time-of-check-to-time-of-use race, and no amount of Python-side
        checking closes it - only the constraint does.

        The race cannot be triggered on demand, so the pre-check is stubbed to
        report "no such user" while the row genuinely exists. This is exactly
        the narrow case CLAUDE.md permits mocking for: something
        non-deterministic. The DATABASE is still real, and it is the database
        doing the work under test.

        Without this test the entire IntegrityError branch is unreachable -
        confirmed by coverage, which flagged the line as never executed.
        """
        await create_user(db_session, email="ada@example.com", password=PASSWORD)

        async def pretend_the_row_does_not_exist(session: AsyncSession, email: str) -> User | None:
            return None

        monkeypatch.setattr("app.services.user.get_user_by_email", pretend_the_row_does_not_exist)

        with pytest.raises(EmailAlreadyRegistered):
            await create_user(db_session, email="ada@example.com", password=PASSWORD)

        # And the session must survive it - this is what the rollback buys.
        # Without it, the next query raises PendingRollbackError far from the
        # actual cause, in code that did nothing wrong.
        found = (
            await db_session.execute(select(User).where(User.email == "ada@example.com"))
        ).scalar_one()
        assert found.email == "ada@example.com"
