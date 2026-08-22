"""Tests for the password-hashing and token primitives.

No database and no HTTP here - these exercise app/core/security.py directly,
which is possible precisely because that module depends on neither.

Each test names the production consequence it protects against, rather than
merely asserting that a function returns something.
"""

import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import get_settings
from app.core.security import (
    MAX_PASSWORD_LENGTH,
    InvalidTokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    password_needs_rehash,
    verify_password,
)

settings = get_settings()

PASSWORD = "correct horse battery staple"


class TestPasswordHashing:
    """Storing and checking passwords."""

    def test_hash_does_not_contain_the_password(self) -> None:
        """The whole point: a stolen database must not yield passwords."""
        hashed = hash_password(PASSWORD)

        assert PASSWORD not in hashed
        # Nor any word of it - a hash that echoed even part of the input would
        # narrow an attacker's search space enormously.
        for word in PASSWORD.split():
            assert word not in hashed

    def test_uses_argon2id(self) -> None:
        """Guards against a future refactor silently downgrading the algorithm.

        argon2 hashes are self-describing, so the algorithm is readable from
        the stored string. If someone swaps the hasher for something faster,
        this fails rather than quietly weakening every password in the system.
        """
        assert hash_password(PASSWORD).startswith("$argon2id$")

    def test_same_password_hashes_differently_each_time(self) -> None:
        """Each hash must carry its own random salt.

        Without a per-password salt, two users with the same password store the
        same hash - which tells an attacker who to target together, and lets
        one precomputed table crack all of them at once.
        """
        assert hash_password(PASSWORD) != hash_password(PASSWORD)

    def test_verify_accepts_the_correct_password(self) -> None:
        assert verify_password(PASSWORD, hash_password(PASSWORD)) is True

    def test_verify_rejects_the_wrong_password(self) -> None:
        assert verify_password("not the password", hash_password(PASSWORD)) is False

    def test_verify_is_case_sensitive(self) -> None:
        """Case-insensitive comparison would shrink the keyspace dramatically."""
        assert verify_password(PASSWORD.upper(), hash_password(PASSWORD)) is False

    @pytest.mark.parametrize(
        "corrupt_hash",
        [
            pytest.param("", id="empty"),
            pytest.param("not-a-hash", id="not_a_hash"),
            pytest.param("$argon2id$truncated", id="truncated_argon2"),
            pytest.param("$2b$12$abcdefghijklmnopqrstuv", id="a_bcrypt_hash"),
        ],
    )
    def test_verify_returns_false_for_an_unusable_hash(self, corrupt_hash: str) -> None:
        """A corrupt stored hash must fail closed, not raise.

        An unhandled exception here would turn a single bad row into a 500 on
        the login endpoint. The user still must not be let in - False is the
        only safe answer - but the failure has to be a clean rejection.
        """
        assert verify_password(PASSWORD, corrupt_hash) is False

    def test_rejects_an_oversized_password(self) -> None:
        """An unbounded password is a CPU-exhaustion lever on a public endpoint.

        argon2 has no bcrypt-style truncation bug, so length is not a
        correctness problem - it is a denial-of-service one. Hashing is
        deliberately expensive, and registration is unauthenticated.
        """
        with pytest.raises(ValueError, match="exceeds"):
            hash_password("x" * (MAX_PASSWORD_LENGTH + 1))

    def test_accepts_a_password_at_the_limit(self) -> None:
        """The boundary itself must be allowed - off-by-one guard."""
        assert verify_password(
            "x" * MAX_PASSWORD_LENGTH,
            hash_password("x" * MAX_PASSWORD_LENGTH),
        )

    def test_accepts_unicode(self) -> None:
        """Passwords are not ASCII. Encoding bugs here lock people out."""
        password = "pässwörd-日本語-🔒"
        assert verify_password(password, hash_password(password))

    def test_fresh_hash_does_not_need_rehashing(self) -> None:
        assert password_needs_rehash(hash_password(PASSWORD)) is False

    def test_unparseable_hash_does_not_need_rehashing(self) -> None:
        """Nothing useful can be done with it, so it must not raise either."""
        assert password_needs_rehash("garbage") is False


class TestAccessTokens:
    """Minting and verifying JWTs."""

    def test_round_trips_the_subject(self) -> None:
        user_id = uuid.uuid4()

        claims = decode_access_token(create_access_token(subject=user_id))

        assert claims.subject == user_id

    def test_two_tokens_for_one_user_are_distinct(self) -> None:
        """The jti claim exists so tokens are individually identifiable.

        Without it, two tokens minted in the same second are byte-identical and
        indistinguishable in a log.
        """
        user_id = uuid.uuid4()

        first = create_access_token(subject=user_id)
        second = create_access_token(subject=user_id)

        assert first != second
        assert decode_access_token(first).token_id != decode_access_token(second).token_id

    def test_payload_is_readable_by_anyone(self) -> None:
        """Documents a property people get wrong: a JWT is NOT encrypted.

        This test exists to make the fact undeniable in the codebase itself. If
        anyone ever puts something confidential in a token, this is the test
        that should have stopped them.
        """
        token = create_access_token(subject=uuid.uuid4())

        # No key, no verification - just base64 decoding, which any client can do.
        payload = jwt.decode(token, options={"verify_signature": False})

        assert "sub" in payload
        assert payload["typ"] == "access"

    def test_expiry_defaults_to_the_configured_lifetime(self) -> None:
        before = datetime.now(UTC)

        claims = decode_access_token(create_access_token(subject=uuid.uuid4()))

        expected = before + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        # A couple of seconds of slack: `before` is sampled just before the
        # token is minted, so the two clocks are close but not identical.
        assert abs((claims.expires_at - expected).total_seconds()) < 5

    def test_rejects_an_expired_token(self) -> None:
        """The only thing limiting a stolen token's usefulness.

        Access tokens cannot be revoked, so if expiry were not enforced a
        leaked token would grant access forever.
        """
        token = create_access_token(subject=uuid.uuid4(), expires_delta=timedelta(seconds=-1))

        with pytest.raises(InvalidTokenError):
            decode_access_token(token)

    def test_rejects_a_token_signed_with_a_different_key(self) -> None:
        """A forged token must not be accepted.

        This is the property the whole scheme rests on: without the signing
        key, nobody can mint a token that verifies.
        """
        forged = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "exp": datetime.now(UTC) + timedelta(minutes=30),
                "iat": datetime.now(UTC),
                "jti": "forged",
                "typ": "access",
            },
            "an-attackers-own-key-which-is-not-ours",
            algorithm="HS256",
        )

        with pytest.raises(InvalidTokenError):
            decode_access_token(forged)

    def test_rejects_an_unsigned_token(self) -> None:
        """The classic `alg: none` attack.

        An attacker rewrites the header to claim the token is unsigned and
        strips the signature. A verifier that trusts the header's algorithm
        accepts it. Ours passes an explicit algorithms allow-list, so the
        header is never consulted for that decision.
        """
        unsigned = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "exp": datetime.now(UTC) + timedelta(minutes=30),
                "iat": datetime.now(UTC),
                "jti": "unsigned",
                "typ": "access",
            },
            key="",
            algorithm="none",
        )

        with pytest.raises(InvalidTokenError):
            decode_access_token(unsigned)

    def test_rejects_a_token_of_the_wrong_type(self) -> None:
        """Guards the token-confusion class of bug.

        When refresh tokens arrive they will be signed with the same key. The
        `typ` claim is what stops a long-lived refresh token being presented
        where a short-lived access token is required.
        """
        refresh_shaped = jwt.encode(
            {
                "sub": str(uuid.uuid4()),
                "exp": datetime.now(UTC) + timedelta(days=30),
                "iat": datetime.now(UTC),
                "jti": str(uuid.uuid4()),
                "typ": "refresh",
            },
            settings.SECRET_KEY.get_secret_value(),
            algorithm=settings.JWT_ALGORITHM,
        )

        with pytest.raises(InvalidTokenError, match="not an access token"):
            decode_access_token(refresh_shaped)

    @pytest.mark.parametrize(
        "missing",
        [
            pytest.param("exp", id="no_expiry"),
            pytest.param("sub", id="no_subject"),
            pytest.param("jti", id="no_token_id"),
        ],
    )
    def test_rejects_a_token_missing_a_required_claim(self, missing: str) -> None:
        """A token with no `exp` would otherwise never expire."""
        payload: dict[str, object] = {
            "sub": str(uuid.uuid4()),
            "exp": datetime.now(UTC) + timedelta(minutes=30),
            "iat": datetime.now(UTC),
            "jti": str(uuid.uuid4()),
            "typ": "access",
        }
        del payload[missing]

        token = jwt.encode(
            payload,
            settings.SECRET_KEY.get_secret_value(),
            algorithm=settings.JWT_ALGORITHM,
        )

        with pytest.raises(InvalidTokenError):
            decode_access_token(token)

    def test_rejects_a_token_whose_subject_is_not_a_uuid(self) -> None:
        """Correctly signed but unusable as an identity."""
        token = jwt.encode(
            {
                "sub": "ada@example.com",
                "exp": datetime.now(UTC) + timedelta(minutes=30),
                "iat": datetime.now(UTC),
                "jti": str(uuid.uuid4()),
                "typ": "access",
            },
            settings.SECRET_KEY.get_secret_value(),
            algorithm=settings.JWT_ALGORITHM,
        )

        with pytest.raises(InvalidTokenError, match="not a valid user ID"):
            decode_access_token(token)

    @pytest.mark.parametrize(
        "garbage",
        [
            pytest.param("", id="empty"),
            pytest.param("not.a.token", id="three_junk_segments"),
            pytest.param("onlyonesegment", id="no_segments"),
        ],
    )
    def test_rejects_malformed_input(self, garbage: str) -> None:
        """Must raise our error, not a PyJWT error.

        The API layer catches InvalidTokenError. Anything else escapes as an
        unhandled exception and becomes a 500 where a 401 was intended.
        """
        with pytest.raises(InvalidTokenError):
            decode_access_token(garbage)
