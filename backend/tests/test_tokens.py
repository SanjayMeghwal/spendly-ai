"""Tests for JWT creation and verification.

No database and no HTTP - these are pure functions over a signed string.

Most of these tests are FORGERY ATTEMPTS. A token is a bearer credential:
whoever holds a valid one is the user until it expires. So the interesting
question is never "does a good token work" but "which bad tokens are
refused", and each test below is one way an attacker would try.
"""

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import get_settings
from app.core.tokens import (
    ACCESS_TOKEN_TYPE,
    TokenError,
    create_access_token,
    decode_access_token,
)

SECRET = get_settings().SECRET_KEY.get_secret_value()
ALGORITHM = get_settings().JWT_ALGORITHM


def forge(payload: dict[str, object], key: str = SECRET, algorithm: str = ALGORITHM) -> str:
    """Sign an arbitrary payload, to build tokens our own code would not mint."""
    return jwt.encode(payload, key, algorithm=algorithm)


def valid_claims(**overrides: object) -> dict[str, object]:
    """The claim set create_access_token produces, overridable per test."""
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": str(uuid.uuid4()),
        "exp": now + timedelta(minutes=30),
        "iat": now,
        "type": ACCESS_TOKEN_TYPE,
    }
    claims.update(overrides)
    return claims


class TestRoundTrip:
    """A token we issue must be a token we accept."""

    def test_returns_the_user_id_it_was_created_with(self) -> None:
        user_id = uuid.uuid4()

        assert decode_access_token(create_access_token(user_id)) == user_id

    def test_two_users_get_different_tokens(self) -> None:
        assert create_access_token(uuid.uuid4()) != create_access_token(uuid.uuid4())


class TestPayloadIsPublic:
    """A JWT is SIGNED, not encrypted. Its contents are readable by anyone."""

    @staticmethod
    def _decode_payload_without_the_key(token: str) -> dict[str, object]:
        """Read the claims with no secret at all - as any attacker can."""
        segment = token.split(".")[1]
        padded = segment + "=" * (-len(segment) % 4)
        result: dict[str, object] = json.loads(base64.urlsafe_b64decode(padded))
        return result

    def test_claims_are_readable_with_no_key(self) -> None:
        """Documents the property, so nobody later assumes tokens hide things.

        The signature proves we issued the token and that it is unaltered. It
        provides no confidentiality whatsoever - paste any JWT into jwt.io and
        it renders in full.
        """
        user_id = uuid.uuid4()

        claims = self._decode_payload_without_the_key(create_access_token(user_id))

        assert claims["sub"] == str(user_id)

    def test_payload_carries_nothing_sensitive(self) -> None:
        """The rule that follows from the test above.

        Since the payload is public, it must contain nothing we would not hand
        to the bearer. A user id is fine - they already know their own id. An
        email address, a password hash, or a balance would not be.

        This asserts an exact claim set, so ADDING a claim requires deciding,
        here, that the new value is safe to publish.
        """
        claims = self._decode_payload_without_the_key(create_access_token(uuid.uuid4()))

        assert set(claims) == {"sub", "exp", "iat", "type"}


class TestForgeryIsRejected:
    """Every one of these is a real attack, not a hypothetical."""

    def test_rejects_the_alg_none_attack(self) -> None:
        """THE classic JWT vulnerability.

        The attacker rewrites the header to `{"alg": "none"}` and strips the
        signature, asserting the token needs no verification. A library that
        takes the algorithm from the token's own header - which is
        attacker-controlled - agrees, and the forgery is accepted.

        Passing `algorithms=` explicitly from OUR config is what refuses it.
        """
        unsigned = jwt.encode(valid_claims(), key="", algorithm="none")

        with pytest.raises(TokenError):
            decode_access_token(unsigned)

    def test_rejects_a_token_signed_with_a_different_key(self) -> None:
        """A token minted by anyone but us.

        This is what the signature is FOR, and it is why SECRET_KEY has no
        default: a published fallback key would let anyone produce tokens that
        pass this check.
        """
        forged = forge(valid_claims(), key="a" * 64)

        with pytest.raises(TokenError):
            decode_access_token(forged)

    def test_rejects_a_tampered_payload(self) -> None:
        """Editing the claims invalidates the signature over them.

        Swapping `sub` for another user's id is the obvious privilege
        escalation, and it is exactly what signing prevents.
        """
        token = create_access_token(uuid.uuid4())
        header, _, signature = token.split(".")

        # Built by hand with integer timestamps, because that is what actually
        # travels on the wire - PyJWT converts datetimes to NumericDate before
        # encoding, and json.dumps cannot serialise a datetime.
        now = int(datetime.now(UTC).timestamp())
        swapped_claims = {
            "sub": str(uuid.uuid4()),  # a DIFFERENT user - the escalation
            "exp": now + 1800,
            "iat": now,
            "type": ACCESS_TOKEN_TYPE,
        }
        other_payload = (
            base64.urlsafe_b64encode(json.dumps(swapped_claims).encode()).rstrip(b"=").decode()
        )

        with pytest.raises(TokenError):
            decode_access_token(f"{header}.{other_payload}.{signature}")

    def test_rejects_an_expired_token(self) -> None:
        """Expiry is the ONLY thing limiting a stolen token.

        Nothing is stored server-side to revoke against, so `exp` is the
        entire containment strategy until refresh tokens exist.
        """
        past = datetime.now(UTC) - timedelta(minutes=1)
        expired = forge(valid_claims(exp=past, iat=past - timedelta(minutes=30)))

        with pytest.raises(TokenError):
            decode_access_token(expired)

    def test_rejects_a_token_with_no_expiry(self) -> None:
        """A token without `exp` would be valid forever.

        PyJWT does not require the claim by default - a token lacking it
        simply never expires. Since a forged token is exactly the kind that
        would omit it, `options={"require": [...]}` makes it mandatory.
        """
        claims = valid_claims()
        del claims["exp"]

        with pytest.raises(TokenError):
            decode_access_token(forge(claims))

    def test_rejects_a_token_of_the_wrong_type(self) -> None:
        """Guards against token confusion before refresh tokens exist.

        Both kinds will be signed with the same key, so without a type claim a
        long-lived refresh token would verify perfectly as an access token -
        silently granting a 30-day session. The check has to be in place
        BEFORE refresh tokens ship, since adding the claim later invalidates
        every token already issued.
        """
        with pytest.raises(TokenError):
            decode_access_token(forge(valid_claims(type="refresh")))

    def test_rejects_a_token_with_no_type(self) -> None:
        claims = valid_claims()
        del claims["type"]

        with pytest.raises(TokenError):
            decode_access_token(forge(claims))

    def test_rejects_a_non_uuid_subject(self) -> None:
        """A validly-signed token whose subject we cannot use."""
        with pytest.raises(TokenError):
            decode_access_token(forge(valid_claims(sub="not-a-uuid")))

    @pytest.mark.parametrize(
        "garbage",
        [
            pytest.param("", id="empty"),
            pytest.param("not-a-token", id="not-jwt-shaped"),
            pytest.param("a.b.c", id="three-junk-segments"),
            pytest.param("Bearer eyJhbGc.eyJzdWI.sig", id="scheme-not-stripped"),
        ],
    )
    def test_rejects_malformed_input(self, garbage: str) -> None:
        """Never an unhandled exception - a 500 here would be a bug report.

        The last case matters in practice: forgetting to strip the "Bearer "
        prefix before decoding is an easy mistake, and it must fail cleanly.
        """
        with pytest.raises(TokenError):
            decode_access_token(garbage)


class TestErrorsDoNotDiscriminate:
    """Every failure looks identical to the caller."""

    def test_all_failures_raise_the_same_exception_type(self) -> None:
        """Distinguishable errors would be an oracle.

        If "expired" were a different exception from "bad signature", an
        attacker probing with forged tokens would learn which part of the
        forgery to fix next. One exception type means one 401, and no
        feedback.
        """
        failures = [
            forge(valid_claims(), key="a" * 64),
            forge(valid_claims(exp=datetime.now(UTC) - timedelta(minutes=1))),
            forge(valid_claims(type="refresh")),
            "not-a-token",
        ]

        for token in failures:
            with pytest.raises(TokenError):
                decode_access_token(token)
