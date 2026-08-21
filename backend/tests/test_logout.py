"""Tests for POST /api/v1/auth/logout.

Logout is the endpoint most often implemented as a lie - the client deletes
its tokens and the server is never told, leaving a 30-day credential alive
for anyone who captured a copy. So the questions here are: does the token
actually stop working, does the answer leak anything about tokens we do not
recognise, and does ending one session leave the others alone.

Helpers are duplicated from test_refresh.py rather than shared, matching the
convention in test_login.py and test_me.py: each test file reads standalone.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import create_refresh_token, decode_refresh_token
from app.models import RefreshToken, User
from app.models.refresh_token import REASON_LOGOUT, REASON_ROTATED
from app.services.refresh import issue_refresh_token, log_out
from app.services.user import create_user

LOGIN_URL = "/api/v1/auth/login"
REFRESH_URL = "/api/v1/auth/refresh"
LOGOUT_URL = "/api/v1/auth/logout"
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


async def send_logout(client: AsyncClient, token: str) -> Response:
    return await client.post(LOGOUT_URL, json={"refresh_token": token})


async def send_refresh(client: AsyncClient, token: str) -> Response:
    return await client.post(REFRESH_URL, json={"refresh_token": token})


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def row_for(session: AsyncSession, token: str) -> RefreshToken:
    """The database row a refresh token names.

    `populate_existing` refreshes the identity map from the database: the
    endpoint under test writes through this same session with a bulk UPDATE,
    which does not necessarily update objects already cached.
    """
    row = await session.get(
        RefreshToken,
        decode_refresh_token(token).token_id,
        populate_existing=True,
    )
    assert row is not None
    return row


async def live_token_count(session: AsyncSession, user: User) -> int:
    result = await session.execute(
        select(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .execution_options(populate_existing=True)
    )
    return len(list(result.scalars().all()))


@pytest.mark.integration
class TestLogoutEndsTheSession:
    """The property that makes this endpoint worth having."""

    async def test_the_refresh_token_stops_working(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await register(db_session)
        pair = await log_in(db_client)

        assert (await send_logout(db_client, pair["refresh_token"])).status_code == 204

        assert (await send_refresh(db_client, pair["refresh_token"])).status_code == 401

    async def test_returns_no_body(self, db_client: AsyncClient, db_session: AsyncSession) -> None:
        """204 means "done, nothing to say" - and there is nothing to say."""
        await register(db_session)
        pair = await log_in(db_client)

        response = await send_logout(db_client, pair["refresh_token"])

        assert response.status_code == 204
        assert response.content == b""

    async def test_records_why_the_session_ended(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await register(db_session)
        pair = await log_in(db_client)

        await send_logout(db_client, pair["refresh_token"])

        row = await row_for(db_session, pair["refresh_token"])
        assert row.revoked_at is not None
        assert row.revoked_reason == REASON_LOGOUT

    async def test_ends_the_whole_family_not_just_the_token_presented(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A client holding a SPENT token can still end its session.

        This is the ordinary case after a failed refresh: the client's stored
        token was already rotated away, and logging out with it must still
        work. Revoking by family rather than by token is what makes that
        possible - and it is why the replacement, which the client may never
        have received, dies too.
        """
        await register(db_session)
        original = await log_in(db_client)
        current = (await send_refresh(db_client, original["refresh_token"])).json()

        await send_logout(db_client, original["refresh_token"])

        assert (await send_refresh(db_client, current["refresh_token"])).status_code == 401
        # The already-rotated token keeps its original reason: it did not die
        # by logout, and overwriting that would erase the session's history.
        assert (await row_for(db_session, original["refresh_token"])).revoked_reason == (
            REASON_ROTATED
        )
        assert (await row_for(db_session, current["refresh_token"])).revoked_reason == REASON_LOGOUT

    async def test_a_deactivated_user_can_still_log_out(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Nothing about revoking a credential requires an active account.

        Refusing here would leave a live session attached to an account
        somebody has already decided to disable - the opposite of the point.
        """
        user = await register(db_session)
        pair = await log_in(db_client)
        user.is_active = False
        await db_session.commit()

        assert (await send_logout(db_client, pair["refresh_token"])).status_code == 204
        assert await live_token_count(db_session, user) == 0


@pytest.mark.integration
class TestLogoutIsIdempotent:
    """Clients retry. A retry must not be an error, and must not rewrite history."""

    async def test_logging_out_twice_succeeds_twice(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await register(db_session)
        pair = await log_in(db_client)

        first = await send_logout(db_client, pair["refresh_token"])
        second = await send_logout(db_client, pair["refresh_token"])

        assert first.status_code == second.status_code == 204

    async def test_the_second_logout_does_not_move_the_timestamp(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The audit trail records when the session ENDED, not when it was last poked.

        This is what the `revoked_at IS NULL` filter in revoke_family buys:
        without it, every repeat call would overwrite the timestamp, and the
        question "when was this session actually revoked?" would have no
        reliable answer.
        """
        await register(db_session)
        pair = await log_in(db_client)

        await send_logout(db_client, pair["refresh_token"])
        first_revoked_at = (await row_for(db_session, pair["refresh_token"])).revoked_at

        await send_logout(db_client, pair["refresh_token"])

        assert (await row_for(db_session, pair["refresh_token"])).revoked_at == first_revoked_at


@pytest.mark.integration
class TestUnrecognisedTokensRevealNothing:
    """RFC 7009: a revocation endpoint reports success even for an invalid token.

    Any other behaviour turns this endpoint into a signature-checking oracle -
    paste a candidate token, read the status code, learn whether it was ever
    genuine. /refresh gives an attacker no such answer, and logout must not be
    the softer door into the same question.
    """

    @pytest.mark.parametrize(
        "token",
        [
            pytest.param("not-a-token", id="garbage"),
            pytest.param("", id="empty"),
            pytest.param("a.b.c", id="jwt-shaped-junk"),
        ],
    )
    async def test_garbage_is_accepted_silently(self, db_client: AsyncClient, token: str) -> None:
        assert (await send_logout(db_client, token)).status_code == 204

    async def test_an_unknown_but_validly_signed_token_is_accepted_silently(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        unknown = create_refresh_token(
            user.id,
            token_id=uuid.uuid4(),
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )

        assert (await send_logout(db_client, unknown)).status_code == 204

    async def test_every_answer_is_byte_identical(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A real token and three fakes must be indistinguishable."""
        await register(db_session)
        pair = await log_in(db_client)

        responses = [
            await send_logout(db_client, pair["refresh_token"]),
            await send_logout(db_client, "not-a-token"),
            await send_logout(db_client, pair["access_token"]),
            await send_logout(db_client, pair["refresh_token"]),  # already revoked
        ]

        assert {r.status_code for r in responses} == {204}
        assert {r.content for r in responses} == {b""}

    async def test_an_access_token_revokes_nothing(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Documents the cost of always answering 204.

        A client that sends the wrong token is told the logout succeeded while
        the session is untouched. That is a genuine usability trap, accepted
        deliberately in exchange for the endpoint revealing nothing - and it is
        pinned here so nobody "fixes" it without deciding to.
        """
        user = await register(db_session)
        pair = await log_in(db_client)

        assert (await send_logout(db_client, pair["access_token"])).status_code == 204

        assert await live_token_count(db_session, user) == 1
        assert (await send_refresh(db_client, pair["refresh_token"])).status_code == 200


@pytest.mark.integration
class TestLogoutIsScopedToOneSession:
    """Ending a session must not end anyone else's - including the same user's."""

    async def test_other_devices_stay_signed_in(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """One login is one family, so logging out of one leaves the other alone.

        This is exactly why revocation is by `family_id` rather than by
        `user_id`: "log out of this device" and "log out everywhere" are
        different operations and must stay different.
        """
        await register(db_session)
        phone = await log_in(db_client)
        laptop = await log_in(db_client)

        await send_logout(db_client, phone["refresh_token"])

        assert (await send_refresh(db_client, laptop["refresh_token"])).status_code == 200

    async def test_another_users_session_cannot_be_ended(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A forged `sub` must not let one user end another's session.

        The token names a row; the row names its owner. If the two disagree we
        act on neither - which is what stops a token signed for Grace from
        revoking Ada's session, in the event our signing key ever leaked.
        """
        ada = await register(db_session)
        grace = await register(db_session, email="grace@example.com")
        ada_pair = await log_in(db_client)

        ada_row = await row_for(db_session, ada_pair["refresh_token"])
        mismatched = create_refresh_token(
            grace.id,  # a different owner than the row records
            token_id=ada_row.id,
            expires_at=ada_row.expires_at,
        )

        assert (await send_logout(db_client, mismatched)).status_code == 204

        assert await live_token_count(db_session, ada) == 1
        assert (await send_refresh(db_client, ada_pair["refresh_token"])).status_code == 200


@pytest.mark.integration
class TestAccessTokensOutliveLogout:
    """A documented, bounded gap - not an oversight."""

    async def test_the_access_token_still_works_after_logout(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Nothing can revoke an access token, and this pins that honestly.

        An access token carries no server-side state - that is what makes it
        cheap to verify - so it dies only when it expires, within
        ACCESS_TOKEN_EXPIRE_MINUTES. A client should discard it at logout, but
        an attacker holding a copy keeps it until then.

        The window is deliberate and bounded. /auth/logout-all is the answer
        when it is not acceptable.
        """
        await register(db_session)
        pair = await log_in(db_client)

        await send_logout(db_client, pair["refresh_token"])

        assert (await db_client.get(ME_URL, headers=auth(pair["access_token"]))).status_code == 200


@pytest.mark.integration
class TestRequestValidation:
    async def test_a_missing_token_is_a_422(self, db_client: AsyncClient) -> None:
        """A malformed request body is a validation error, not a silent success."""
        assert (await db_client.post(LOGOUT_URL, json={})).status_code == 422


@pytest.mark.integration
class TestServiceLayer:
    """The rule itself, without HTTP in the way."""

    async def test_log_out_never_raises_on_a_bad_token(self, db_session: AsyncSession) -> None:
        """The service, not the route, is what guarantees this cannot 500."""
        await log_out(db_session, "not-a-token")

    async def test_log_out_revokes_the_family(self, db_session: AsyncSession) -> None:
        user = await register(db_session)
        token = await issue_refresh_token(db_session, user.id)

        await log_out(db_session, token)

        assert await live_token_count(db_session, user) == 0
