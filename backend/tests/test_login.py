"""Tests for POST /api/v1/auth/login.

The theme running through this file is that a login endpoint must reveal
NOTHING except "these credentials work" or "they do not" - not through the
status code, not through the body, and not through how long it takes to
answer.
"""

import time

import pytest
from argon2 import PasswordHasher
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import decode_access_token
from app.models import User
from app.services.auth import InactiveUser, InvalidCredentials, authenticate_user
from app.services.user import create_user

LOGIN_URL = "/api/v1/auth/login"
EMAIL = "ada@example.com"
PASSWORD = "correct-horse-battery-staple"


async def register(session: AsyncSession, *, active: bool = True) -> User:
    """Create a user directly through the service, bypassing HTTP."""
    user = await create_user(session, email=EMAIL, password=PASSWORD, full_name="Ada")
    if not active:
        user.is_active = False
        await session.commit()
    return user


def credentials(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {"email": EMAIL, "password": PASSWORD}
    body.update(overrides)
    return body


@pytest.mark.integration
class TestSuccessfulLogin:
    """The happy path, and what the token actually contains."""

    async def test_returns_a_token_pair(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Both tokens, and the ACCESS token's lifetime.

        `expires_in` describes the access token only - the refresh token's
        expiry is deliberately not published (see schemas/auth.py). Pinning
        the number here means shortening the access-token lifetime is a
        deliberate edit rather than a silent change to the API contract.
        """
        await register(db_session)

        response = await db_client.post(LOGIN_URL, json=credentials())

        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["expires_in"] == 15 * 60
        assert body["access_token"]
        assert body["refresh_token"]
        # The two must be different strings. Returning the same token twice
        # under two names would give a 30-day credential the routing of a
        # 15-minute one, defeating the entire split.
        assert body["access_token"] != body["refresh_token"]

    async def test_token_identifies_the_user_who_logged_in(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The token must name the right person.

        Without this, an endpoint that trusts `sub` could serve one user's
        financial data to another - the worst possible bug in this project.
        """
        user = await register(db_session)

        response = await db_client.post(LOGIN_URL, json=credentials())

        assert decode_access_token(response.json()["access_token"]) == user.id

    async def test_login_is_case_insensitive_on_email(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Stored addresses are lowercase, so the lookup must fold case too.

        Without normalisation here, a correct password would be rejected
        purely because of how the address was typed - and the user would have
        no way to tell why.
        """
        await register(db_session)

        response = await db_client.post(LOGIN_URL, json=credentials(email="Ada@EXAMPLE.com"))

        assert response.status_code == 200

    async def test_response_contains_no_credentials(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Neither the password nor the stored hash may appear anywhere."""
        await register(db_session)

        response = await db_client.post(LOGIN_URL, json=credentials())

        assert PASSWORD not in response.text
        assert "$argon2" not in response.text
        assert "hashed_password" not in response.text


@pytest.mark.integration
class TestFailedLoginRevealsNothing:
    """The core security property of this endpoint."""

    async def test_wrong_password_returns_401(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await register(db_session)

        response = await db_client.post(LOGIN_URL, json=credentials(password="wrong-password-xx"))

        assert response.status_code == 401

    async def test_unknown_email_returns_401(self, db_client: AsyncClient) -> None:
        response = await db_client.post(LOGIN_URL, json=credentials(email="nobody@example.com"))

        assert response.status_code == 401

    async def test_both_failures_are_byte_for_byte_identical(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """THE point of this endpoint's design.

        A different message for "no such account" would let anyone turn a list
        of email addresses into a list of confirmed customers. For a finance
        product that fact is sensitive before any password is involved, and it
        halves the work of a credential-stuffing campaign.

        Comparing the full bodies - not just the status codes - catches a
        well-meaning future change like adding "check your email address".
        """
        await register(db_session)

        wrong_password = await db_client.post(
            LOGIN_URL, json=credentials(password="wrong-password-xx")
        )
        unknown_email = await db_client.post(
            LOGIN_URL, json=credentials(email="nobody@example.com")
        )

        assert wrong_password.status_code == unknown_email.status_code
        assert wrong_password.text == unknown_email.text

    async def test_401_message_does_not_reveal_whether_the_account_exists(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The message itself must not disclose membership.

        The byte-for-byte test above proves the two failures MATCH EACH OTHER.
        It does not prove the shared message is safe - swapping it for "No
        account exists with that email address" keeps both responses identical
        while telling every caller exactly what we were hiding. A mutation
        test found precisely that gap.

        So this pins the CONTENT as well as the symmetry.
        """
        await register(db_session)

        for body in (credentials(password="wrong-password-xx"), credentials(email="x@y.com")):
            detail = (await db_client.post(LOGIN_URL, json=body)).json()["detail"].lower()

            for disclosure in (
                "no account",
                "not found",
                "does not exist",
                "unknown email",
                "no user",
                "unregistered",
                "wrong password",
                "incorrect password",
            ):
                assert disclosure not in detail, (
                    f"the 401 message reveals account existence: {detail!r}"
                )

    async def test_401_carries_the_www_authenticate_header(self, db_client: AsyncClient) -> None:
        """Required by RFC 6750 - it tells a client HOW to authenticate."""
        response = await db_client.post(LOGIN_URL, json=credentials())

        assert response.headers["WWW-Authenticate"] == "Bearer"

    async def test_failure_leaks_no_internals(self, db_client: AsyncClient) -> None:
        response = await db_client.post(LOGIN_URL, json=credentials())

        text = response.text.lower()
        for leak in ("traceback", "sqlalchemy", "argon2", "select", "users"):
            assert leak not in text, f"401 response leaked {leak!r}"

    async def test_password_policy_is_not_enforced_at_login(self, db_client: AsyncClient) -> None:
        """A short password must be a 401, never a 422.

        Rejecting it as a validation error would tell an attacker our password
        POLICY for free, and would make a short guess distinguishable from a
        long one. At login there is exactly one answer for every bad
        credential.
        """
        response = await db_client.post(LOGIN_URL, json=credentials(password="short"))

        assert response.status_code == 401


@pytest.mark.integration
class TestTimingEqualisation:
    """A response that returns 60x faster leaks what the message would have."""

    @staticmethod
    async def _median_ms(call: object, runs: int = 3) -> float:
        assert callable(call)
        samples = []
        for _ in range(runs):
            start = time.perf_counter()
            await call()
            samples.append((time.perf_counter() - start) * 1000)
        return sorted(samples)[len(samples) // 2]

    async def test_unknown_email_costs_the_same_as_a_wrong_password(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Proves dummy_verify is actually WIRED IN, not merely present.

        The unknown-email path does no Argon2 work of its own, so without
        dummy_verify it returns in ~1ms while a wrong password takes ~64ms.
        Both say "incorrect email or password", but the clock says which
        addresses have accounts.

        Asserted as a RATIO so it holds on a slow or loaded runner, where both
        measurements scale together. The tolerance is wide because the attack
        needs a large, consistent gap - roughly 60x - and a missing
        dummy_verify would land far outside this band.
        """
        await register(db_session)

        wrong_password_ms = await self._median_ms(
            lambda: db_client.post(LOGIN_URL, json=credentials(password="wrong-password-xx"))
        )
        unknown_email_ms = await self._median_ms(
            lambda: db_client.post(LOGIN_URL, json=credentials(email="nobody@example.com"))
        )

        assert unknown_email_ms > wrong_password_ms * 0.4, (
            f"unknown email answered in {unknown_email_ms:.1f}ms against "
            f"{wrong_password_ms:.1f}ms for a wrong password - fast enough to "
            "enumerate which addresses have accounts"
        )


@pytest.mark.integration
class TestDeactivatedAccounts:
    """403 here is safe to distinguish, unlike a 401."""

    async def test_deactivated_user_cannot_log_in(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await register(db_session, active=False)

        response = await db_client.post(LOGIN_URL, json=credentials())

        assert response.status_code == 403

    async def test_deactivated_user_receives_no_token(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The status code is not the control - the absent token is."""
        await register(db_session, active=False)

        response = await db_client.post(LOGIN_URL, json=credentials())

        assert "access_token" not in response.json()

    async def test_wrong_password_on_a_deactivated_account_is_still_401(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Order matters: the password is checked BEFORE the active flag.

        Reversed, the endpoint would answer 403 to anyone guessing at a
        deactivated address - confirming the account exists without knowing
        the password. Distinguishing 403 is only safe once the caller has
        proved they own the account.
        """
        await register(db_session, active=False)

        response = await db_client.post(LOGIN_URL, json=credentials(password="wrong-password-xx"))

        assert response.status_code == 401


@pytest.mark.integration
class TestHashUpgradeOnLogin:
    """Cost parameters can rise without anyone resetting a password."""

    async def test_a_weak_hash_is_silently_upgraded(self, db_session: AsyncSession) -> None:
        """The only moment a stored hash can be strengthened.

        We never hold the plaintext, so hashes cannot be upgraded in bulk -
        the sole opportunity is during a successful login, when the user
        supplies it. Without this, the parameters chosen today are frozen for
        the lifetime of every existing account.
        """
        weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
        user = User(email=EMAIL, hashed_password=weak.hash(PASSWORD))
        db_session.add(user)
        await db_session.commit()
        assert "m=8," in user.hashed_password

        await authenticate_user(db_session, email=EMAIL, password=PASSWORD)

        assert "m=65536" in user.hashed_password, "hash was not upgraded on login"

    async def test_the_upgraded_hash_still_verifies(self, db_session: AsyncSession) -> None:
        """An upgrade that broke login would be far worse than a weak hash."""
        weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)
        db_session.add(User(email=EMAIL, hashed_password=weak.hash(PASSWORD)))
        await db_session.commit()

        await authenticate_user(db_session, email=EMAIL, password=PASSWORD)
        # Logging in a second time exercises the freshly written hash.
        user = await authenticate_user(db_session, email=EMAIL, password=PASSWORD)

        assert user.email == EMAIL

    async def test_a_current_hash_is_left_alone(self, db_session: AsyncSession) -> None:
        """No pointless write - and no new hash on every single login."""
        user = await register(db_session)
        original = user.hashed_password

        await authenticate_user(db_session, email=EMAIL, password=PASSWORD)

        assert user.hashed_password == original


@pytest.mark.integration
class TestServiceLayer:
    """The rules hold without HTTP, for a CLI or a background job."""

    async def test_raises_domain_exceptions_not_http_ones(self, db_session: AsyncSession) -> None:
        await register(db_session)

        with pytest.raises(InvalidCredentials):
            await authenticate_user(db_session, email=EMAIL, password="wrong-password-xx")

        with pytest.raises(InvalidCredentials):
            await authenticate_user(db_session, email="nobody@example.com", password=PASSWORD)

    async def test_inactive_user_raises_its_own_exception(self, db_session: AsyncSession) -> None:
        await register(db_session, active=False)

        with pytest.raises(InactiveUser):
            await authenticate_user(db_session, email=EMAIL, password=PASSWORD)
