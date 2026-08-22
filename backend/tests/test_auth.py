"""Tests for registration, login, and the authenticated-user endpoint.

Every test here talks to the real PostgreSQL container through the transactional
fixtures in conftest.py, so what is exercised is the same stack that ships:
real constraints, real types, real SQL.

Each test names the production consequence it protects against.
"""

import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.models.user import User
from tests.conftest import TEST_EMAIL, TEST_PASSWORD

# Everything in this module needs a live database.
pytestmark = pytest.mark.integration

REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
ME_URL = "/api/v1/users/me"


def _registration(**overrides: object) -> dict[str, object]:
    """A valid registration payload, with individual fields overridable.

    Keeps each test's payload down to the one field it is actually about,
    instead of six lines of boilerplate that obscure the point.
    """
    payload: dict[str, object] = {
        "email": "grace@example.com",
        "password": "a sufficiently long password",
        "full_name": "Grace Hopper",
    }
    payload.update(overrides)
    return payload


class TestRegistration:
    """POST /api/v1/auth/register"""

    async def test_creates_an_account(self, client: AsyncClient) -> None:
        response = await client.post(REGISTER_URL, json=_registration())

        # 201, not 200: a resource was created. Clients and caches behave
        # differently based on this, so it is part of the contract.
        assert response.status_code == 201
        body = response.json()
        assert body["email"] == "grace@example.com"
        assert body["full_name"] == "Grace Hopper"
        assert body["is_active"] is True
        # A UUID, not a sequential integer - the choice that stops anyone
        # enumerating accounts or reading our signup rate off the URLs.
        assert uuid.UUID(body["id"])

    async def test_response_never_contains_the_password_or_its_hash(
        self, client: AsyncClient
    ) -> None:
        """The single most important assertion in this file.

        UserRead is an allow-list, so this holds even as columns are added to
        the model later. If someone ever returns the ORM object directly, or
        adds `hashed_password` to the schema, this fails.
        """
        response = await client.post(REGISTER_URL, json=_registration())

        raw = response.text.lower()
        assert "password" not in raw
        assert "argon2" not in raw
        assert "hash" not in raw

    async def test_stores_a_hash_and_not_the_plaintext(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Verified against the database itself, not the API response.

        An endpoint can look perfectly correct while writing plaintext to a
        column. The only way to know is to read the row back.
        """
        password = "a sufficiently long password"
        await client.post(REGISTER_URL, json=_registration(password=password))

        result = await db_session.execute(select(User).where(User.email == "grace@example.com"))
        user = result.scalar_one()

        assert user.hashed_password != password
        assert user.hashed_password.startswith("$argon2id$")

    async def test_rejects_a_duplicate_email(self, client: AsyncClient) -> None:
        """409 Conflict, not 400.

        The request is well-formed; it conflicts with existing state. That
        distinction is what lets a client tell "you sent me nonsense" from
        "try a different address".
        """
        await client.post(REGISTER_URL, json=_registration())

        response = await client.post(REGISTER_URL, json=_registration())

        assert response.status_code == 409

    async def test_rejects_a_duplicate_email_differing_only_in_case(
        self, client: AsyncClient
    ) -> None:
        """The bug that case normalisation exists to prevent.

        Without it, "Grace@example.com" and "grace@example.com" are two rows -
        the same person with two accounts, each holding half their financial
        history, and a login that lands in whichever they happened to type.
        """
        await client.post(REGISTER_URL, json=_registration(email="grace@example.com"))

        response = await client.post(REGISTER_URL, json=_registration(email="GRACE@Example.COM"))

        assert response.status_code == 409

    async def test_stores_the_email_lowercased(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Also proves the model's CHECK constraint is not being violated."""
        await client.post(REGISTER_URL, json=_registration(email="  GRACE@Example.COM  "))

        result = await db_session.execute(select(User).where(User.email == "grace@example.com"))
        assert result.scalar_one() is not None

    @pytest.mark.parametrize(
        "bad_email",
        [
            pytest.param("not-an-email", id="no_at_sign"),
            pytest.param("@example.com", id="no_local_part"),
            pytest.param("grace@", id="no_domain"),
            pytest.param("", id="empty"),
        ],
    )
    async def test_rejects_a_malformed_email(self, client: AsyncClient, bad_email: str) -> None:
        response = await client.post(REGISTER_URL, json=_registration(email=bad_email))

        assert response.status_code == 422

    async def test_rejects_a_short_password(self, client: AsyncClient) -> None:
        """Length is the only password rule we impose - so it must hold."""
        response = await client.post(REGISTER_URL, json=_registration(password="short"))

        assert response.status_code == 422

    async def test_rejects_an_oversized_password(self, client: AsyncClient) -> None:
        """Rejected at the schema, BEFORE any hashing happens.

        That ordering is the point: argon2 is deliberately expensive, and this
        endpoint is unauthenticated. Hashing first and validating afterwards
        would hand anyone a CPU-exhaustion lever.
        """
        response = await client.post(REGISTER_URL, json=_registration(password="x" * 129))

        assert response.status_code == 422

    async def test_accepts_a_registration_without_a_name(self, client: AsyncClient) -> None:
        """full_name is optional - requiring it collects data we do not need."""
        response = await client.post(
            REGISTER_URL,
            json={"email": "anon@example.com", "password": "a sufficiently long password"},
        )

        assert response.status_code == 201
        assert response.json()["full_name"] is None

    async def test_treats_a_whitespace_only_name_as_absent(self, client: AsyncClient) -> None:
        """Otherwise every `if user.full_name:` check renders an invisible name."""
        response = await client.post(REGISTER_URL, json=_registration(full_name="   "))

        assert response.status_code == 201
        assert response.json()["full_name"] is None


class TestLogin:
    """POST /api/v1/auth/login"""

    async def test_returns_a_usable_token_for_valid_credentials(
        self, client: AsyncClient, registered_user: User
    ) -> None:
        response = await client.post(
            LOGIN_URL,
            data={"username": TEST_EMAIL, "password": TEST_PASSWORD},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["expires_in"] > 0

        # The real proof is not the shape of the response but that the token
        # actually opens a protected endpoint.
        me = await client.get(ME_URL, headers={"Authorization": f"Bearer {body['access_token']}"})
        assert me.status_code == 200
        assert me.json()["id"] == str(registered_user.id)

    async def test_login_is_case_insensitive_in_the_email(
        self, client: AsyncClient, registered_user: User
    ) -> None:
        """Someone whose keyboard capitalised the first letter must still get in."""
        response = await client.post(
            LOGIN_URL,
            data={"username": TEST_EMAIL.upper(), "password": TEST_PASSWORD},
        )

        assert response.status_code == 200

    async def test_rejects_a_wrong_password(
        self, client: AsyncClient, registered_user: User
    ) -> None:
        response = await client.post(
            LOGIN_URL,
            data={"username": TEST_EMAIL, "password": "not the right password"},
        )

        assert response.status_code == 401

    async def test_does_not_reveal_whether_an_account_exists(
        self, client: AsyncClient, registered_user: User
    ) -> None:
        """The anti-enumeration guarantee, asserted directly.

        If a wrong password and an unknown address produced different
        responses, anyone could test whether a given person banks here - one
        request per address, no account needed. For a finance product, mere
        membership is worth protecting.
        """
        wrong_password = await client.post(
            LOGIN_URL,
            data={"username": TEST_EMAIL, "password": "not the right password"},
        )
        unknown_email = await client.post(
            LOGIN_URL,
            data={"username": "nobody@example.com", "password": "not the right password"},
        )

        assert wrong_password.status_code == unknown_email.status_code == 401
        assert wrong_password.json() == unknown_email.json()

    async def test_a_deactivated_account_cannot_log_in(
        self, client: AsyncClient, inactive_user: User
    ) -> None:
        """Correct credentials, but the account is switched off.

        Note this returns the same 401 as a wrong password - deliberately.
        Saying "this account is deactivated" would confirm the address exists.
        """
        response = await client.post(
            LOGIN_URL,
            data={"username": "deactivated@example.com", "password": TEST_PASSWORD},
        )

        assert response.status_code == 401

    async def test_401_carries_the_www_authenticate_header(
        self, client: AsyncClient, registered_user: User
    ) -> None:
        """RFC 7235 requires it on every 401. Clients use it to know how to retry."""
        response = await client.post(
            LOGIN_URL,
            data={"username": TEST_EMAIL, "password": "wrong"},
        )

        assert response.headers["www-authenticate"] == "Bearer"

    async def test_failure_response_does_not_leak_internals(
        self, client: AsyncClient, registered_user: User
    ) -> None:
        """A public endpoint must not describe the system behind it."""
        response = await client.post(
            LOGIN_URL,
            data={"username": TEST_EMAIL, "password": "wrong"},
        )

        text = response.text.lower()
        for leak in ("traceback", "argon2", "sql", "asyncpg", "select"):
            assert leak not in text, f"response leaked {leak!r}: {response.text}"


class TestCurrentUser:
    """GET /api/v1/users/me - and, through it, the get_current_user dependency."""

    async def test_returns_the_authenticated_user(
        self, client: AsyncClient, registered_user: User, auth_headers: dict[str, str]
    ) -> None:
        response = await client.get(ME_URL, headers=auth_headers)

        assert response.status_code == 200
        body = response.json()
        assert body["id"] == str(registered_user.id)
        assert body["email"] == TEST_EMAIL

    async def test_never_exposes_the_password_hash(
        self, client: AsyncClient, auth_headers: dict[str, str]
    ) -> None:
        response = await client.get(ME_URL, headers=auth_headers)

        assert "hashed_password" not in response.json()
        assert "argon2" not in response.text

    async def test_requires_a_token(self, client: AsyncClient) -> None:
        """The endpoint must be closed by default.

        A protected route that answers anonymously is the most consequential
        bug this file can catch.
        """
        response = await client.get(ME_URL)

        assert response.status_code == 401

    @pytest.mark.parametrize(
        "header",
        [
            pytest.param("", id="empty"),
            pytest.param("garbage", id="no_scheme"),
            pytest.param("Basic dXNlcjpwYXNz", id="wrong_scheme"),
            pytest.param("Bearer", id="scheme_only"),
            pytest.param("Bearer not.a.jwt", id="malformed_token"),
        ],
    )
    async def test_rejects_a_bad_authorization_header(
        self, client: AsyncClient, header: str
    ) -> None:
        response = await client.get(ME_URL, headers={"Authorization": header})

        assert response.status_code == 401

    async def test_rejects_an_expired_token(
        self, client: AsyncClient, registered_user: User
    ) -> None:
        """Expiry is the only limit on a stolen token's usefulness.

        Access tokens cannot be revoked, so if this check failed, a token that
        leaked once would work forever.
        """
        expired = create_access_token(
            subject=registered_user.id, expires_delta=timedelta(seconds=-1)
        )

        response = await client.get(ME_URL, headers={"Authorization": f"Bearer {expired}"})

        assert response.status_code == 401

    async def test_rejects_a_valid_token_for_a_user_who_no_longer_exists(
        self, client: AsyncClient, token_for_unknown_user: str
    ) -> None:
        """Proves the dependency verifies the user, not just the signature.

        A deleted account's token stays cryptographically valid until it
        expires. Trusting the token's claims without a lookup would let a
        deleted user keep acting - and attach writes to an owner ID that no
        longer exists.
        """
        response = await client.get(
            ME_URL, headers={"Authorization": f"Bearer {token_for_unknown_user}"}
        )

        assert response.status_code == 401

    async def test_a_deactivated_user_gets_403_not_401(
        self, client: AsyncClient, inactive_user: User
    ) -> None:
        """403, because re-authenticating would not help.

        401 means "I do not know who you are" and tells a client to go and log
        in. For a deactivated account that produces an endless login loop: the
        credentials are fine, the account simply is not allowed to act. 403
        says so.
        """
        token = create_access_token(subject=inactive_user.id)

        response = await client.get(ME_URL, headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 403

    async def test_401_carries_the_www_authenticate_header(self, client: AsyncClient) -> None:
        response = await client.get(ME_URL)

        assert response.headers["www-authenticate"] == "Bearer"

    async def test_rejection_does_not_say_why(self, client: AsyncClient) -> None:
        """Expired, forged, and malformed must be indistinguishable.

        "Signature verification failed" versus "token expired" tells an
        attacker which part of a forgery attempt to fix next.
        """
        expired = create_access_token(subject=uuid.uuid4(), expires_delta=timedelta(seconds=-1))
        forged = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.wrongsignature"

        first = await client.get(ME_URL, headers={"Authorization": f"Bearer {expired}"})
        second = await client.get(ME_URL, headers={"Authorization": f"Bearer {forged}"})

        assert first.status_code == second.status_code == 401
        assert first.json() == second.json()
