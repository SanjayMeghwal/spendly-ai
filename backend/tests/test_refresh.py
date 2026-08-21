"""Tests for POST /api/v1/auth/refresh, and the session machinery behind it.

Cryptographic forgery is covered in test_tokens.py. What this file adds is
everything that only exists once a token meets a DATABASE ROW: rotation,
single use, reuse detection, absolute session lifetime, and the guarantee
that one user's session never yields another user's tokens.

The organising question is not "does refreshing work" but "what happens when
two people hold the same refresh token". That case is unavoidable - tokens
get stolen, and clients retry - and the answer to it is the design.
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tokens import create_refresh_token, decode_access_token, decode_refresh_token
from app.models import RefreshToken, User
from app.models.refresh_token import REASON_REUSE_DETECTED, REASON_ROTATED
from app.services.refresh import (
    InvalidRefreshToken,
    issue_refresh_token,
    rotate_refresh_token,
)
from app.services.user import create_user

LOGIN_URL = "/api/v1/auth/login"
REFRESH_URL = "/api/v1/auth/refresh"
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


async def send_refresh(client: AsyncClient, token: str) -> Response:
    return await client.post(REFRESH_URL, json={"refresh_token": token})


def auth(token: str) -> dict[str, str]:
    """An Authorization header carrying a bearer token."""
    return {"Authorization": f"Bearer {token}"}


async def stored_tokens(session: AsyncSession, user: User) -> list[RefreshToken]:
    """Every refresh-token row belonging to a user, oldest first.

    `populate_existing=True` is load-bearing. The endpoint under test writes
    through this very session, and revocation is a bulk UPDATE - which does
    not necessarily refresh objects already in the identity map. Without this,
    a plain SELECT returns the CACHED objects and the test asserts against a
    stale copy of the row rather than what the database actually holds.

    Note what is NOT used here: `session.expire_all()`. It looks like the
    obvious fix and is a trap under async SQLAlchemy - it marks attributes
    expired, so the next plain attribute access tries to reload them with
    synchronous IO and raises MissingGreenlet. `populate_existing` refreshes
    them as part of a query we are already awaiting.
    """
    result = await session.execute(
        select(RefreshToken)
        .where(RefreshToken.user_id == user.id)
        .order_by(RefreshToken.created_at, RefreshToken.id)
        .execution_options(populate_existing=True)
    )
    return list(result.scalars().all())


async def row_for(session: AsyncSession, token: str) -> RefreshToken:
    """The database row a given refresh token names.

    Looked up by the token's own `jti` rather than by position in a list.
    Ordering these rows by `created_at` does NOT work: the column is filled by
    PostgreSQL's now(), which returns the TRANSACTION timestamp - identical
    for every row written inside one transaction, which is exactly what the
    rolled-back test session provides. Sorting by it silently degrades to
    sorting by random UUID, and the test then asserts against whichever row it
    happened to get.

    `populate_existing` for the same reason as in stored_tokens: refresh the
    identity map from the database rather than trusting what it cached.
    """
    row = await session.get(
        RefreshToken,
        decode_refresh_token(token).token_id,
        populate_existing=True,
    )
    assert row is not None
    return row


@pytest.mark.integration
class TestRotation:
    """A refresh token is single-use: spending one returns its replacement."""

    async def test_returns_a_new_pair(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await register(db_session)
        original = await log_in(db_client)

        response = await send_refresh(db_client, original["refresh_token"])

        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["expires_in"] == 15 * 60
        assert body["access_token"]
        # The refresh token MUST be a new one. Handing the same token back
        # would be the easiest possible implementation of this endpoint and
        # would silently delete rotation, turning a single-use credential into
        # a permanent one.
        assert body["refresh_token"] != original["refresh_token"]

        # NOTE deliberately no assertion that the ACCESS token differs. A JWT
        # is a pure function of its claims, and two access tokens minted for
        # the same user within the same second carry identical claims - `iat`
        # and `exp` are whole seconds - so they are byte-identical. That is
        # correct behaviour, not a bug, and asserting otherwise would produce
        # a test that passes or fails depending on how fast the machine is.
        # Refresh tokens escape this because each carries a unique `jti`.

    async def test_the_new_access_token_works_and_names_the_same_user(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The point of the endpoint: a usable access token without a password."""
        user = await register(db_session)
        original = await log_in(db_client)

        body = (await send_refresh(db_client, original["refresh_token"])).json()

        assert decode_access_token(body["access_token"]) == user.id

        me = await db_client.get(ME_URL, headers=auth(body["access_token"]))
        assert me.status_code == 200
        assert me.json()["id"] == str(user.id)

    async def test_the_new_refresh_token_can_itself_be_rotated(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A session must survive many refreshes, not just one.

        Three hops, because two would not distinguish "rotation works" from
        "the second token happens to be the one the row was created for".
        """
        await register(db_session)
        pair = await log_in(db_client)

        for _ in range(3):
            response = await send_refresh(db_client, pair["refresh_token"])
            assert response.status_code == 200
            pair = response.json()

    async def test_the_spent_token_is_refused(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """SINGLE USE. This is the property everything else here rests on."""
        await register(db_session)
        original = await log_in(db_client)

        await send_refresh(db_client, original["refresh_token"])
        replay = await send_refresh(db_client, original["refresh_token"])

        assert replay.status_code == 401

    async def test_the_spent_row_records_that_it_was_rotated(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The audit trail: why each token died, and when."""
        user = await register(db_session)
        original = await log_in(db_client)

        issued = (await send_refresh(db_client, original["refresh_token"])).json()

        spent = await row_for(db_session, original["refresh_token"])
        replacement = await row_for(db_session, issued["refresh_token"])
        assert spent.revoked_at is not None
        assert spent.revoked_reason == REASON_ROTATED
        assert replacement.revoked_at is None
        assert replacement.revoked_reason is None
        assert len(await stored_tokens(db_session, user)) == 2

    async def test_the_replacement_stays_in_the_same_family(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Rotation continues a session; it does not start a new one.

        If each rotation began a new family, reuse detection could only ever
        revoke a single token - the lineage that ties a replayed token to the
        session it belongs to would not exist.
        """
        await register(db_session)
        original = await log_in(db_client)

        issued = (await send_refresh(db_client, original["refresh_token"])).json()

        spent = await row_for(db_session, original["refresh_token"])
        replacement = await row_for(db_session, issued["refresh_token"])
        assert replacement.family_id == spent.family_id

    async def test_rotation_does_not_extend_the_session(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The session lifetime is ABSOLUTE, not sliding.

        The replacement inherits the original expiry to the microsecond. If it
        were recomputed, an attacker holding a stolen token could refresh it
        every few minutes forever and the session would never end - and the
        user would never be asked for their password again.
        """
        await register(db_session)
        original = await log_in(db_client)

        issued = (await send_refresh(db_client, original["refresh_token"])).json()

        spent = await row_for(db_session, original["refresh_token"])
        replacement = await row_for(db_session, issued["refresh_token"])
        assert replacement.expires_at == spent.expires_at


@pytest.mark.integration
class TestReuseDetection:
    """What happens when two parties hold the same refresh token.

    We cannot tell a stolen copy from an over-eager client retry, so both are
    answered the same way: end the session. The cost of being wrong in the
    other direction - leaving a thief's session live - is unbounded.
    """

    async def test_replaying_a_spent_token_kills_the_live_one_too(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """THE central security test in this file.

        Refusing only the replayed token would not be enough. If the ATTACKER
        rotated first, they now hold the current token and the honest user is
        the one being refused - so refusing quietly would hand over the
        session. Revoking the whole family means the attacker's token dies
        with everyone else's.
        """
        await register(db_session)
        original = await log_in(db_client)

        current = (await send_refresh(db_client, original["refresh_token"])).json()

        # The replay - either a thief or a retry. Refused either way.
        assert (await send_refresh(db_client, original["refresh_token"])).status_code == 401

        # And the token that was still perfectly good a moment ago is now dead.
        assert (await send_refresh(db_client, current["refresh_token"])).status_code == 401

    async def test_the_revocation_is_recorded_as_reuse(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A spike of these rows is an attack signal, so it must be legible."""
        await register(db_session)
        original = await log_in(db_client)
        current = (await send_refresh(db_client, original["refresh_token"])).json()

        await send_refresh(db_client, original["refresh_token"])

        spent = await row_for(db_session, original["refresh_token"])
        replacement = await row_for(db_session, current["refresh_token"])
        assert replacement.revoked_reason == REASON_REUSE_DETECTED
        # The already-spent token keeps its ORIGINAL reason. Overwriting it
        # would erase the fact that this token died by normal rotation before
        # anything suspicious happened - and with it, the ability to tell when
        # the session was actually compromised.
        assert spent.revoked_reason == REASON_ROTATED

    async def test_a_revoked_session_cannot_be_revived_by_replaying_further(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Every token in a dead family stays dead, however many times it is tried."""
        await register(db_session)
        original = await log_in(db_client)
        current = (await send_refresh(db_client, original["refresh_token"])).json()
        await send_refresh(db_client, original["refresh_token"])

        for token in (original["refresh_token"], current["refresh_token"]):
            for _ in range(2):
                assert (await send_refresh(db_client, token)).status_code == 401

    async def test_other_sessions_survive(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """One compromised device must not sign the user out everywhere.

        Two logins are two families, so revoking one leaves the other intact.
        That is exactly why `family_id` exists rather than revoking by user id
        - "log out of this device" and "log out everywhere" have to be
        different operations.
        """
        await register(db_session)
        phone = await log_in(db_client)
        laptop = await log_in(db_client)

        await send_refresh(db_client, phone["refresh_token"])
        await send_refresh(db_client, phone["refresh_token"])  # replay: phone is dead

        assert (await send_refresh(db_client, laptop["refresh_token"])).status_code == 200


@pytest.mark.integration
class TestRejectedTokens:
    """Every way a refresh token can fail, and the one answer to all of them."""

    async def test_rejects_garbage(self, db_client: AsyncClient) -> None:
        assert (await send_refresh(db_client, "not-a-token")).status_code == 401

    async def test_rejects_a_token_signed_with_another_key(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        forged = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "exp": datetime.now(UTC) + timedelta(days=30),
                "iat": datetime.now(UTC),
                "type": "refresh",
                "jti": str(uuid.uuid4()),
            },
            "a" * 64,
            algorithm="HS256",
        )

        assert (await send_refresh(db_client, forged)).status_code == 401

    async def test_rejects_an_access_token(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """TOKEN-TYPE CONFUSION, the direction that upgrades a credential.

        An access token accepted here would be laundered into a brand-new
        30-day session, so stealing one would stop being time-limited.
        """
        await register(db_session)
        pair = await log_in(db_client)

        assert (await send_refresh(db_client, pair["access_token"])).status_code == 401

    async def test_a_refresh_token_is_refused_as_an_access_token(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """And the direction that lengthens a session.

        Verified through the real endpoint rather than the decoder, because
        this is the mistake that actually costs something: /me accepting a
        refresh token would give every protected endpoint a 30-day credential.
        """
        await register(db_session)
        pair = await log_in(db_client)

        response = await db_client.get(ME_URL, headers=auth(pair["refresh_token"]))

        assert response.status_code == 401

    async def test_rejects_an_unknown_token_id(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Correctly signed, correctly typed, and naming no row at all.

        This is what a token from a wiped database looks like - and what a
        forgery would look like if our signing key ever leaked. The row, not
        the signature, is the final authority.
        """
        user = await register(db_session)
        token = create_refresh_token(
            user.id,
            token_id=uuid.uuid4(),
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )

        assert (await send_refresh(db_client, token)).status_code == 401

    async def test_rejects_a_token_whose_subject_does_not_match_its_row(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A token claiming one user while its row belongs to another.

        Unreachable without our signing key, which is exactly why it is worth
        pinning: if the key ever leaked, the row would still refuse to hand
        one person's session to another. The claim never outranks the row.
        """
        owner = await register(db_session)
        intruder = await register(db_session, email="grace@example.com")
        await log_in(db_client)

        (row,) = await stored_tokens(db_session, owner)
        mismatched = create_refresh_token(
            intruder.id,  # a different `sub` than the row records
            token_id=row.id,
            expires_at=row.expires_at,
        )

        assert (await send_refresh(db_client, mismatched)).status_code == 401

    async def test_rejects_an_expired_token(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        expired = create_refresh_token(
            user.id,
            token_id=uuid.uuid4(),
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )

        assert (await send_refresh(db_client, expired)).status_code == 401

    async def test_rejects_a_live_claim_over_an_expired_row(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """When the claim and the column disagree, the column wins.

        Only reachable by signing a token whose `exp` outlives the row it
        names - which needs our key - but it is the difference between a
        session that ends and one that quietly does not. Defence in depth,
        tested rather than assumed.
        """
        user = await register(db_session)
        await log_in(db_client)

        (row,) = await stored_tokens(db_session, user)
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await db_session.commit()

        still_signed = create_refresh_token(
            user.id,
            token_id=row.id,
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )

        assert (await send_refresh(db_client, still_signed)).status_code == 401

    async def test_rejects_a_token_whose_account_was_deleted(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Deleting a user takes their sessions with them.

        Also proves the ON DELETE CASCADE on `refresh_tokens.user_id` really
        fires: the rows are gone, not orphaned, so the token names nothing.
        """
        user = await register(db_session)
        pair = await log_in(db_client)

        await db_session.delete(user)
        await db_session.commit()

        assert (await send_refresh(db_client, pair["refresh_token"])).status_code == 401
        assert await stored_tokens(db_session, user) == []

    async def test_every_rejection_looks_identical(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """No oracle. The body must not say WHICH check failed.

        "Already used" is the answer worth hiding: it would confirm to an
        attacker that a token they found was genuine, and that they were one
        step behind rather than wrong entirely.
        """
        user = await register(db_session)
        spent = await log_in(db_client)
        await send_refresh(db_client, spent["refresh_token"])

        responses = [
            await send_refresh(db_client, "not-a-token"),
            await send_refresh(db_client, spent["refresh_token"]),
            await send_refresh(
                db_client,
                create_refresh_token(
                    user.id,
                    token_id=uuid.uuid4(),
                    expires_at=datetime.now(UTC) + timedelta(days=30),
                ),
            ),
            await send_refresh(
                db_client,
                create_refresh_token(
                    user.id,
                    token_id=uuid.uuid4(),
                    expires_at=datetime.now(UTC) - timedelta(seconds=1),
                ),
            ),
        ]

        assert {r.status_code for r in responses} == {401}
        assert len({r.text for r in responses}) == 1
        assert all(r.headers["WWW-Authenticate"] == "Bearer" for r in responses)

    async def test_a_missing_token_is_a_422_not_a_500(self, db_client: AsyncClient) -> None:
        """A malformed request body is a validation error, not a crash."""
        response = await db_client.post(REFRESH_URL, json={})

        assert response.status_code == 422


@pytest.mark.integration
class TestDeactivatedAccounts:
    """Deactivation must end the ability to mint new access tokens."""

    async def test_a_deactivated_user_cannot_refresh(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Otherwise deactivation would only take effect in up to 30 days.

        The CurrentUser dependency already blocks a deactivated user's
        requests, but without this check they could keep minting fresh access
        tokens indefinitely - and every new endpoint would depend on that one
        dependency never being forgotten.
        """
        user = await register(db_session)
        pair = await log_in(db_client)

        user.is_active = False
        await db_session.commit()

        response = await send_refresh(db_client, pair["refresh_token"])

        # 403, not 401 - and safe to distinguish, because only someone holding
        # a live token we issued to this account can reach it. Answering
        # "invalid token" would send them to log in again, which cannot work.
        assert response.status_code == 403
        assert response.json()["detail"] == "This account has been deactivated."

    async def test_a_deactivated_user_cannot_log_in_to_get_a_new_session(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The other door into the same room, closed in the same way."""
        await register(db_session, active=False)

        response = await db_client.post(LOGIN_URL, json={"email": EMAIL, "password": PASSWORD})

        assert response.status_code == 403


@pytest.mark.integration
class TestUserIsolation:
    """One user's session must never produce another user's credentials."""

    async def test_a_refresh_token_only_ever_names_its_own_owner(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await register(db_session)
        other = await register(db_session, email="grace@example.com")
        ada = await log_in(db_client)

        body = (await send_refresh(db_client, ada["refresh_token"])).json()

        assert decode_access_token(body["access_token"]) != other.id

    async def test_revoking_one_users_session_leaves_another_untouched(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await register(db_session)
        await register(db_session, email="grace@example.com")
        ada = await log_in(db_client)
        grace = await log_in(db_client, email="grace@example.com")

        await send_refresh(db_client, ada["refresh_token"])
        await send_refresh(db_client, ada["refresh_token"])  # replay kills Ada's family

        assert (await send_refresh(db_client, grace["refresh_token"])).status_code == 200


@pytest.mark.integration
class TestServiceLayer:
    """The rules themselves, without HTTP in the way."""

    async def test_issuing_creates_exactly_one_live_row(self, db_session: AsyncSession) -> None:
        user = await register(db_session)

        await issue_refresh_token(db_session, user.id)

        (row,) = await stored_tokens(db_session, user)
        assert row.user_id == user.id
        assert row.revoked_at is None
        assert row.expires_at > datetime.now(UTC)

    async def test_each_login_starts_its_own_family(self, db_session: AsyncSession) -> None:
        """One family per sign-in is what makes per-device logout possible."""
        user = await register(db_session)

        await issue_refresh_token(db_session, user.id)
        await issue_refresh_token(db_session, user.id)

        first, second = await stored_tokens(db_session, user)
        assert first.family_id != second.family_id

    async def test_rotation_returns_the_user_and_a_new_token(
        self, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)
        token = await issue_refresh_token(db_session, user.id)

        rotated = await rotate_refresh_token(db_session, token)

        assert rotated.user.id == user.id
        assert rotated.refresh_token != token

    async def test_rotation_raises_on_a_spent_token(self, db_session: AsyncSession) -> None:
        """The domain exception, before the API layer turns it into a 401."""
        user = await register(db_session)
        token = await issue_refresh_token(db_session, user.id)
        await rotate_refresh_token(db_session, token)

        with pytest.raises(InvalidRefreshToken):
            await rotate_refresh_token(db_session, token)


class TestRepr:
    """__repr__ output reaches logs and error trackers. It must stay useful.

    No database needed - this is pure formatting.
    """

    @staticmethod
    def _token(**overrides: object) -> RefreshToken:
        values: dict[str, object] = {
            "id": uuid.uuid4(),
            "user_id": uuid.uuid4(),
            "family_id": uuid.uuid4(),
            "expires_at": datetime.now(UTC) + timedelta(days=30),
        }
        values.update(overrides)
        return RefreshToken(**values)

    def test_shows_whether_the_token_is_live(self) -> None:
        """The one fact worth reading at a glance in a log line."""
        assert "live" in repr(self._token())

    def test_shows_when_a_token_has_been_revoked(self) -> None:
        revoked = self._token(revoked_at=datetime.now(UTC), revoked_reason=REASON_ROTATED)

        assert "revoked" in repr(revoked)

    def test_carries_no_credential(self) -> None:
        """Unlike User.__repr__, there is nothing here to redact - by design.

        The row holds the token's ID, never the token. This pins that: if
        somebody ever adds a column holding token material, the credential
        would start appearing in logs, and this test is the reminder that the
        row is deliberately not a secret.
        """
        rendered = repr(self._token())

        assert "eyJ" not in rendered  # the leading bytes of every JWT header
