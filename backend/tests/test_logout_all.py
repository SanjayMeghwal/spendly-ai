"""Tests for POST /api/v1/auth/logout-all, and the token_version check it relies on.

Ordinary logout ends one session and deliberately leaves that client's access
token working until it expires. This endpoint exists for the case where that
bounded gap is unacceptable - the user believes someone else has their
session - so the question running through this file is not "were the refresh
tokens revoked" but "did the access tokens actually die too".

That second half is enforced in app/api/deps.py, by comparing the `ver` claim
against users.token_version. Revoking refresh tokens without it would end the
ability to mint NEW access tokens while leaving every existing one alive -
which looks correct in a database and is not.

Helpers are duplicated from test_logout.py rather than shared, matching the
convention in test_login.py, test_me.py, and test_refresh.py: each test file
reads standalone.
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import create_access_token
from app.models import RefreshToken, User
from app.models.refresh_token import REASON_LOGOUT_ALL
from app.services.user import create_user

LOGIN_URL = "/api/v1/auth/login"
REFRESH_URL = "/api/v1/auth/refresh"
LOGOUT_ALL_URL = "/api/v1/auth/logout-all"
ME_URL = "/api/v1/auth/me"
EMAIL = "ada@example.com"
PASSWORD = "correct-horse-battery-staple"


async def register(
    session: AsyncSession,
    *,
    email: str = EMAIL,
    active: bool = True,
) -> User:
    """Create a user directly through the service, bypassing HTTP."""
    user = await create_user(session, email=email, password=PASSWORD, full_name="Ada")
    if not active:
        user.is_active = False
        await session.commit()
    return user


async def log_in(client: AsyncClient, *, email: str = EMAIL) -> dict[str, str]:
    """Sign in over HTTP and return the token pair."""
    response = await client.post(LOGIN_URL, json={"email": email, "password": PASSWORD})
    assert response.status_code == 200
    body: dict[str, str] = response.json()
    return body


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def reread(session: AsyncSession, user: User) -> User:
    """Re-read a user from the database, discarding the cached copy.

    log_out_everywhere bumps token_version with a bulk UPDATE, which does not
    refresh objects already in the identity map. Without populate_existing a
    test would assert against the stale in-memory value and pass while the
    database said something else.
    """
    fresh = await session.get(User, user.id, populate_existing=True)
    assert fresh is not None
    return fresh


async def live_token_count(session: AsyncSession, user: User) -> int:
    result = await session.execute(
        select(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .execution_options(populate_existing=True)
    )
    return len(list(result.scalars().all()))


@pytest.mark.integration
class TestEverySessionEnds:
    """The refresh-token half: no session anywhere can mint a new token."""

    async def test_returns_204_with_no_body(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await register(db_session)
        tokens = await log_in(db_client)

        response = await db_client.post(LOGOUT_ALL_URL, headers=auth(tokens["access_token"]))

        assert response.status_code == 204
        assert response.content == b""

    async def test_revokes_sessions_on_every_device(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Three separate logins, one call, nothing left alive.

        This is the difference from /auth/logout, which would end only the
        session whose token was presented.
        """
        user = await register(db_session)
        first = await log_in(db_client)
        await log_in(db_client)
        await log_in(db_client)
        assert await live_token_count(db_session, user) == 3

        await db_client.post(LOGOUT_ALL_URL, headers=auth(first["access_token"]))

        assert await live_token_count(db_session, user) == 0

    async def test_a_revoked_refresh_token_cannot_mint_anything(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The observable consequence, from a client's point of view."""
        await register(db_session)
        tokens = await log_in(db_client)

        await db_client.post(LOGOUT_ALL_URL, headers=auth(tokens["access_token"]))
        response = await db_client.post(
            REFRESH_URL, json={"refresh_token": tokens["refresh_token"]}
        )

        assert response.status_code == 401

    async def test_records_why_the_sessions_ended(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The audit trail must distinguish this from an ordinary logout.

        "Every session ended at once" and "one device signed out" are very
        different events when someone is reconstructing a suspected account
        compromise afterwards.
        """
        user = await register(db_session)
        tokens = await log_in(db_client)

        await db_client.post(LOGOUT_ALL_URL, headers=auth(tokens["access_token"]))

        result = await db_session.execute(
            select(RefreshToken)
            .where(RefreshToken.user_id == user.id)
            .execution_options(populate_existing=True)
        )
        rows = list(result.scalars().all())
        assert rows
        assert all(row.revoked_reason == REASON_LOGOUT_ALL for row in rows)

    async def test_another_users_sessions_are_untouched(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Scoped to the caller. A bulk revoke that ignored user_id would be
        a catastrophic way to log out an entire customer base at once."""
        await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        ada_tokens = await log_in(db_client, email="ada@example.com")
        await log_in(db_client, email="grace@example.com")

        await db_client.post(LOGOUT_ALL_URL, headers=auth(ada_tokens["access_token"]))

        assert await live_token_count(db_session, grace) == 1


@pytest.mark.integration
class TestAccessTokensDieToo:
    """The half that refresh-token revocation alone does NOT provide.

    Every test here fails if the token_version comparison in
    app/api/deps.py is removed - which is the point of writing them.
    """

    async def test_token_version_is_incremented(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        before = user.token_version
        tokens = await log_in(db_client)

        await db_client.post(LOGOUT_ALL_URL, headers=auth(tokens["access_token"]))

        assert (await reread(db_session, user)).token_version == before + 1

    async def test_the_callers_own_access_token_stops_working(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Including the token that authorised this very request.

        Intended, not a bug: "log me out everywhere" means everywhere, and
        the caller is one of the sessions. Compare
        test_logout.py::test_the_access_token_still_works_after_logout, which
        pins the opposite behaviour for ordinary logout.
        """
        await register(db_session)
        tokens = await log_in(db_client)
        assert (
            await db_client.get(ME_URL, headers=auth(tokens["access_token"]))
        ).status_code == 200

        await db_client.post(LOGOUT_ALL_URL, headers=auth(tokens["access_token"]))

        response = await db_client.get(ME_URL, headers=auth(tokens["access_token"]))
        assert response.status_code == 401

    async def test_another_devices_access_token_stops_working(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """THE WHOLE REASON THIS ENDPOINT EXISTS.

        This is the attacker's session. Revoking refresh tokens alone would
        leave it working for the rest of the access token's lifetime - which
        is precisely the window the user is trying to close.
        """
        await register(db_session)
        stolen = await log_in(db_client)
        mine = await log_in(db_client)

        await db_client.post(LOGOUT_ALL_URL, headers=auth(mine["access_token"]))

        response = await db_client.get(ME_URL, headers=auth(stolen["access_token"]))
        assert response.status_code == 401

    async def test_logging_in_again_works_immediately(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The counter invalidates old tokens without locking the account.

        A bumped version must not break future logins - the new token simply
        carries the new number.
        """
        await register(db_session)
        tokens = await log_in(db_client)
        await db_client.post(LOGOUT_ALL_URL, headers=auth(tokens["access_token"]))

        fresh = await log_in(db_client)

        assert (await db_client.get(ME_URL, headers=auth(fresh["access_token"]))).status_code == 200


@pytest.mark.integration
class TestStaleVersionIsRefused:
    """The dependency-level check, exercised directly rather than through logout.

    These mint tokens with a chosen `ver` instead of going through the
    endpoint, so the comparison is tested independently of whatever bumps the
    counter. A future password-change feature will bump it too.
    """

    async def test_an_older_version_is_401(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        stale = create_access_token(user.id, token_version=user.token_version - 1)

        response = await db_client.get(ME_URL, headers=auth(stale))

        assert response.status_code == 401

    async def test_a_newer_version_is_also_401(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Pins the `!=` in app/api/deps.py, which `<` would not satisfy.

        A token claiming a HIGHER version than the account has cannot be one
        we issued: it means our own state went backwards, as after restoring
        a database snapshot. A claim we cannot account for is refused rather
        than trusted - and with `<` it would sail through.
        """
        user = await register(db_session)
        impossible = create_access_token(user.id, token_version=user.token_version + 5)

        response = await db_client.get(ME_URL, headers=auth(impossible))

        assert response.status_code == 401

    async def test_a_superseded_token_is_indistinguishable_from_any_other_failure(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """No oracle. "Your session was ended elsewhere" would confirm to an
        attacker that the token was genuine and merely stale - telling them
        the account is real and that they were holding a working credential.
        """
        user = await register(db_session)

        superseded = await db_client.get(
            ME_URL, headers=auth(create_access_token(user.id, token_version=user.token_version - 1))
        )
        garbage = await db_client.get(ME_URL, headers=auth("not-a-token"))
        unknown = await db_client.get(
            ME_URL, headers=auth(create_access_token(uuid.uuid4(), token_version=1))
        )

        assert superseded.status_code == garbage.status_code == unknown.status_code == 401
        assert superseded.text == garbage.text == unknown.text
        assert superseded.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.integration
class TestAuthenticationRequired:
    """Unlike /auth/logout, this endpoint demands proof of current control."""

    async def test_no_token_is_401(self, db_client: AsyncClient) -> None:
        """A refresh token in the body would identify one session; this
        operation affects all of them, so possession of one credential is not
        enough."""
        response = await db_client.post(LOGOUT_ALL_URL)

        assert response.status_code == 401

    async def test_a_garbage_token_is_401(self, db_client: AsyncClient) -> None:
        response = await db_client.post(LOGOUT_ALL_URL, headers=auth("not-a-token"))

        assert response.status_code == 401

    async def test_a_refresh_token_cannot_be_used_here(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Presenting the refresh token as a bearer credential must fail.

        The `type` claim is what separates them; without that check a 30-day
        credential would authorise the most destructive action in the API.
        """
        await register(db_session)
        tokens = await log_in(db_client)

        response = await db_client.post(LOGOUT_ALL_URL, headers=auth(tokens["refresh_token"]))

        assert response.status_code == 401

    async def test_a_deactivated_account_is_403(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        token = create_access_token(user.id, token_version=user.token_version)
        user.is_active = False
        await db_session.commit()

        response = await db_client.post(LOGOUT_ALL_URL, headers=auth(token))

        assert response.status_code == 403

    async def test_one_user_cannot_end_another_users_sessions(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The owner comes from the signed token, so there is no id to forge."""
        await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")
        ada_tokens = await log_in(db_client, email="ada@example.com")
        await log_in(db_client, email="grace@example.com")

        await db_client.post(LOGOUT_ALL_URL, headers=auth(ada_tokens["access_token"]))

        assert (await reread(db_session, grace)).token_version == grace.token_version
        assert await live_token_count(db_session, grace) == 1
