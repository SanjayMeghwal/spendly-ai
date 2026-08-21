"""Tests for GET /api/v1/auth/me, and for the CurrentUser dependency behind it.

/me is a thin handler; almost everything tested here belongs to the
dependency in app/api/deps.py. That is deliberate - the dependency is what
every future protected endpoint will reuse, so this file is really the test
suite for "what does it mean to be authenticated in this application".

Token FORGERY at the cryptographic level is covered in test_tokens.py. What
this file adds is everything that only exists once a token meets a request
and a database: header handling, an account deleted or deactivated after its
token was signed, and the guarantee that one user's token never yields
another user's data.
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.tokens import ACCESS_TOKEN_TYPE, create_access_token
from app.models import User
from app.services.user import create_user

ME_URL = "/api/v1/auth/me"
LOGIN_URL = "/api/v1/auth/login"
EMAIL = "ada@example.com"
PASSWORD = "correct-horse-battery-staple"

SECRET = get_settings().SECRET_KEY.get_secret_value()
ALGORITHM = get_settings().JWT_ALGORITHM


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


def auth(token: str) -> dict[str, str]:
    """An Authorization header carrying a bearer token."""
    return {"Authorization": f"Bearer {token}"}


def forge(key: str = SECRET, **overrides: object) -> str:
    """Sign a token our own code would not mint, to test what we refuse."""
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": str(uuid.uuid4()),
        "exp": now + timedelta(minutes=30),
        "iat": now,
        "type": ACCESS_TOKEN_TYPE,
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm=ALGORITHM)


@pytest.mark.integration
class TestAuthenticatedRequest:
    """The happy path: a token we issued identifies its owner."""

    async def test_returns_the_authenticated_account(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        user = await register(db_session)

        response = await db_client.get(ME_URL, headers=auth(create_access_token(user.id)))

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == str(user.id)
        assert body["email"] == EMAIL
        assert body["full_name"] == "Ada"
        assert body["is_active"] is True

    async def test_token_from_login_works_against_me(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The two endpoints must actually fit together.

        Every other test here mints its own token, which would keep passing
        even if /login started issuing something /me cannot read. This is the
        only test that exercises the real client journey: log in, then use
        what you were given.
        """
        await register(db_session)

        login = await db_client.post(LOGIN_URL, json={"email": EMAIL, "password": PASSWORD})
        response = await db_client.get(ME_URL, headers=auth(login.json()["access_token"]))

        assert response.status_code == 200
        assert response.json()["email"] == EMAIL

    async def test_response_never_contains_the_password_hash(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """UserRead has no hashed_password field, and this proves it holds.

        Checked against the raw response TEXT, not the parsed keys: a hash
        nested anywhere, under any name, still fails this.
        """
        user = await register(db_session)

        response = await db_client.get(ME_URL, headers=auth(create_access_token(user.id)))

        assert "hashed_password" not in response.text
        assert user.hashed_password not in response.text
        assert PASSWORD not in response.text

    async def test_one_users_token_never_returns_another_users_account(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """The single most important guarantee in this project.

        If this ever fails, one person's financial data is being served to
        another. The identity must come from the signed token and from
        nowhere else.
        """
        ada = await register(db_session, email="ada@example.com")
        grace = await register(db_session, email="grace@example.com")

        response = await db_client.get(ME_URL, headers=auth(create_access_token(grace.id)))

        assert response.status_code == 200
        assert response.json()["id"] == str(grace.id)
        assert response.json()["id"] != str(ada.id)


@pytest.mark.integration
class TestMissingOrMalformedHeader:
    """Everything that never gets as far as verifying a signature."""

    async def test_no_authorization_header_is_401(self, db_client: AsyncClient) -> None:
        """401 - "you have not authenticated" - not 403.

        403 would mean "you authenticated and still may not", which would
        tell a client to stop retrying rather than to log in.
        """
        response = await db_client.get(ME_URL)

        assert response.status_code == 401

    async def test_401_carries_the_www_authenticate_header(self, db_client: AsyncClient) -> None:
        """RFC 6750 requires it, and clients use it to know how to retry."""
        response = await db_client.get(ME_URL)

        assert response.headers["WWW-Authenticate"] == "Bearer"

    @pytest.mark.parametrize(
        ("header", "description"),
        [
            ("", "empty header"),
            ("Bearer", "scheme with no token"),
            ("Basic YWRhOnNlY3JldA==", "wrong scheme entirely"),
            ("Token abc.def.ghi", "unknown scheme"),
        ],
    )
    async def test_unusable_header_is_401(
        self, db_client: AsyncClient, header: str, description: str
    ) -> None:
        response = await db_client.get(ME_URL, headers={"Authorization": header})

        assert response.status_code == 401, description

    async def test_token_without_a_scheme_is_401(self, db_client: AsyncClient) -> None:
        """A bare token, no "Bearer " prefix - a very common client bug.

        It must be refused rather than accepted leniently: the scheme is what
        tells us how to interpret the credential, and guessing would mean
        accepting credentials of kinds we never issued.
        """
        response = await db_client.get(
            ME_URL, headers={"Authorization": create_access_token(uuid.uuid4())}
        )

        assert response.status_code == 401


@pytest.mark.integration
class TestRejectedTokens:
    """Tokens that parse as headers but must not authenticate anyone."""

    async def test_expired_token_is_401(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Expiry is the ONLY thing limiting a stolen token's usefulness.

        We hold no server-side session to revoke, so if an expired token were
        still accepted, a token leaked once would work forever.
        """
        user = await register(db_session)
        expired = forge(sub=str(user.id), exp=datetime.now(UTC) - timedelta(seconds=1))

        response = await db_client.get(ME_URL, headers=auth(expired))

        assert response.status_code == 401

    async def test_token_signed_with_another_key_is_401(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Someone who cannot sign with our secret cannot mint identities."""
        user = await register(db_session)
        forged = forge(key="not-our-secret-key-but-long-enough-xx", sub=str(user.id))

        response = await db_client.get(ME_URL, headers=auth(forged))

        assert response.status_code == 401

    async def test_wrong_token_type_is_401(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """A refresh token must not open an access-token door.

        Refresh tokens do not exist yet. This test exists so that the day
        they do - signed with the same key - they cannot be replayed here.
        """
        user = await register(db_session)
        refresh = forge(sub=str(user.id), type="refresh")

        response = await db_client.get(ME_URL, headers=auth(refresh))

        assert response.status_code == 401

    @pytest.mark.parametrize(
        "garbage",
        ["not-a-token", "a.b.c", "...", "eyJhbGciOiJIUzI1NiJ9"],
    )
    async def test_malformed_token_is_401_not_500(
        self, db_client: AsyncClient, garbage: str
    ) -> None:
        """Junk must be refused, never crash.

        A 500 here would leak a traceback naming our modules, and would mean
        an unauthenticated caller can reach an unhandled code path.
        """
        response = await db_client.get(ME_URL, headers=auth(garbage))

        assert response.status_code == 401

    async def test_token_for_a_deleted_user_is_401(self, db_client: AsyncClient) -> None:
        """A perfectly valid signature naming a row that is not there.

        This is exactly the case a token-only implementation gets wrong: the
        signature verifies, so a dependency that skipped the database would
        happily authenticate a ghost.
        """
        response = await db_client.get(ME_URL, headers=auth(create_access_token(uuid.uuid4())))

        assert response.status_code == 401

    async def test_every_rejection_looks_identical(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """No oracle: the failures must be indistinguishable to the caller.

        If "expired" read differently from "bad signature", an attacker
        probing with forged tokens would learn which part of the forgery to
        fix next - turning blind guessing into a guided search.

        THE MISSING-HEADER AND WRONG-SCHEME CASES ARE HERE ON PURPOSE.

        They are what pins down `auto_error=False` on the HTTPBearer scheme.
        Left at its default, FastAPI answers those two itself with
        {"detail": "Not authenticated"} while every other failure gets our
        message - same status, different body, for the same answer. This
        assertion is the only thing that would notice, so removing it would
        let that inconsistency back in silently.
        """
        user = await register(db_session)
        bad_tokens = [
            "garbage",
            forge(sub=str(user.id), exp=datetime.now(UTC) - timedelta(seconds=1)),
            forge(key="not-our-secret-key-but-long-enough-xx", sub=str(user.id)),
            forge(sub=str(user.id), type="refresh"),
            create_access_token(uuid.uuid4()),
        ]

        responses = [await db_client.get(ME_URL, headers=auth(token)) for token in bad_tokens]
        responses.append(await db_client.get(ME_URL))
        responses.append(await db_client.get(ME_URL, headers={"Authorization": "Basic abc"}))

        assert {r.status_code for r in responses} == {401}
        assert len({r.text for r in responses}) == 1
        assert {r.headers.get("WWW-Authenticate") for r in responses} == {"Bearer"}


@pytest.mark.integration
class TestDeactivatedAccount:
    """A valid token whose account has since been switched off."""

    async def test_deactivated_user_is_403(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """403, not 401, and the distinction is safe here.

        This is only reachable by someone holding a token we issued to this
        account, so they have already proved ownership - the fact reveals
        nothing new. Answering 401 would send them to log in again, which
        cannot possibly help.
        """
        user = await register(db_session, active=False)

        response = await db_client.get(ME_URL, headers=auth(create_access_token(user.id)))

        assert response.status_code == 403

    async def test_deactivation_takes_effect_before_the_token_expires(
        self, db_client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """THE REASON THE DEPENDENCY QUERIES THE DATABASE AT ALL.

        The token here is minted while the account is active and is never
        reissued - it stays cryptographically valid throughout. Only a fresh
        read of the row can notice the change.

        Without the lookup this test fails, and someone locked out of a
        finance account would keep full access for the rest of the token's
        lifetime.
        """
        user = await register(db_session)
        token = create_access_token(user.id)

        assert (await db_client.get(ME_URL, headers=auth(token))).status_code == 200

        user.is_active = False
        await db_session.commit()

        assert (await db_client.get(ME_URL, headers=auth(token))).status_code == 403
