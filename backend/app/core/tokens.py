"""JSON Web Token creation and verification.

Kept separate from security.py, which owns passwords. Passwords and tokens
solve different problems - proving who you are once, versus carrying that
proof around afterwards - and separating them keeps each file readable.

THE SINGLE MOST MISUNDERSTOOD THING ABOUT JWTs: THEY ARE SIGNED, NOT
ENCRYPTED.

A JWT is three base64url-encoded parts joined by dots:

    header.payload.signature

The payload is ENCODED, not hidden. Anyone holding a token can decode and
read every claim in it without any key at all - paste one into jwt.io and it
renders in full. What the signature provides is INTEGRITY and AUTHENTICITY:
proof that we issued this token and that nobody has altered it since. It
provides no confidentiality whatsoever.

The practical rule: never put anything in a payload you would not hand to the
bearer. No password hashes, no account balances, no personal data. A user id
is fine - the user already knows their own id.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple

import jwt

from app.core.config import get_settings

# THE `type` CLAIM, AND WHY IT WAS WRITTEN BEFORE IT WAS NEEDED.
#
# Both kinds of token are signed with the SAME key, so cryptography alone
# cannot tell them apart: a refresh token presented to a protected endpoint
# verifies perfectly. Without this claim it would be accepted, silently
# handing the bearer a 30-DAY session in place of a 15-minute one - the
# opposite of what the two lifetimes are for.
#
# The claim was added with access tokens, before refresh tokens existed,
# precisely because retrofitting it would have invalidated every token already
# issued. It now costs nothing to enforce in both directions.
ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"


class RefreshTokenClaims(NamedTuple):
    """The two identifiers a refresh token carries.

    A NamedTuple rather than a bare tuple so `claims.token_id` reads
    unambiguously at the call site. Both values are UUIDs, and returning them
    positionally would make transposing them a silent, type-correct bug.
    """

    user_id: uuid.UUID

    # The `jti` claim - the primary key of this token's row in
    # `refresh_tokens`. It is what makes a refresh token REVOCABLE while an
    # access token is not: the signature says "we issued this", and the row
    # says "and it is still valid".
    token_id: uuid.UUID


class TokenError(Exception):
    """Raised when a token is missing, malformed, expired, or not ours.

    Deliberately ONE exception for every failure. The caller must not be able
    to tell "expired" from "bad signature" from "wrong type", because that
    distinction is an oracle: an attacker probing with forged tokens would
    learn which part of their forgery to fix next. The API layer turns all of
    them into the same 401.
    """


def create_access_token(user_id: uuid.UUID) -> str:
    """Mint a signed access token for a user.

    The claims are standard (RFC 7519) rather than invented:

      sub  - the SUBJECT: who this token is about. Registered claim names are
             what lets any standard library validate our tokens.
      exp  - EXPIRY. The one claim that limits the damage of a stolen token,
             since we hold no server-side state to revoke against. Every
             compliant library rejects an expired token automatically.
      iat  - ISSUED AT. Supports "log out everywhere": store a
             `tokens_valid_after` timestamp per user and reject anything
             issued before it.
      type - ours, not standard. See ACCESS_TOKEN_TYPE above.

    `sub` is stringified because RFC 7519 requires it to be a string, and
    PyJWT enforces that on decode. A raw UUID object would also not be
    JSON-serialisable.
    """
    settings = get_settings()

    # Timezone-aware UTC, never datetime.utcnow(). utcnow() returns a NAIVE
    # datetime that merely happens to hold UTC, and comparing it against an
    # aware one raises. Worse, if it is ever treated as local time the expiry
    # silently shifts by hours - a token that expires in the past, or lives
    # far longer than intended.
    now = datetime.now(UTC)

    payload = {
        "sub": str(user_id),
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        "iat": now,
        "type": ACCESS_TOKEN_TYPE,
    }

    return jwt.encode(
        payload,
        settings.SECRET_KEY.get_secret_value(),
        algorithm=settings.JWT_ALGORITHM,
    )


def create_refresh_token(
    user_id: uuid.UUID,
    *,
    token_id: uuid.UUID,
    expires_at: datetime,
) -> str:
    """Mint a signed refresh token for one row in the `refresh_tokens` table.

    WHY THIS TAKES ITS ID AND EXPIRY INSTEAD OF CHOOSING THEM.

    Unlike an access token, a refresh token is only half a credential. The
    other half is the database row it names, and the two must agree exactly:
    if the `exp` claim outlived the row's `expires_at`, the token would look
    valid to any standard JWT library while being dead to us - and if it
    expired first, the row would linger as a session nobody can close.

    The service layer creates the row, so the service layer owns both values.
    This function's job is only to sign what it is given. Computing a fresh
    expiry here would quietly turn rotation into a SLIDING session that a
    stolen token could renew forever.

    `jti` is the RFC 7519 registered claim for a token identifier. Using the
    standard name rather than inventing one means any tooling that inspects
    JWTs - including jwt.io - labels it correctly.
    """
    settings = get_settings()

    payload = {
        "sub": str(user_id),
        "exp": expires_at,
        "iat": datetime.now(UTC),
        "type": REFRESH_TOKEN_TYPE,
        "jti": str(token_id),
    }

    return jwt.encode(
        payload,
        settings.SECRET_KEY.get_secret_value(),
        algorithm=settings.JWT_ALGORITHM,
    )


def _decode(token: str, *, expected_type: str, require: list[str]) -> dict[str, Any]:
    """Verify a signature and the claims every token of a given type must have.

    THE ALGORITHM ALLOWLIST IS THE SECURITY BOUNDARY HERE.

    `algorithms=[...]` tells PyJWT which algorithms are acceptable. It must
    come from OUR configuration and never from the token's own header - the
    header is attacker-controlled, and trusting it is the classic JWT
    vulnerability in two forms:

      alg: none      - the token claims it needs no signature at all, and a
                       naive verifier agrees. Instant forgery.
      alg confusion  - under RS256 the verifier holds a PUBLIC key. An
                       attacker re-signs the token with HS256 using that
                       public key as the HMAC secret. If the verifier honours
                       the header, it validates.

    PyJWT requires this argument explicitly, which is precisely why it is a
    better choice than a library that will happily infer it.

    `require` refuses a token that OMITS a claim rather than one that fails
    it. Without it, a token missing `exp` verifies happily and never expires -
    and the tokens most likely to lack a claim are forged ones.

    Raises:
        TokenError: for every failure mode, without distinguishing them.
    """
    settings = get_settings()

    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.SECRET_KEY.get_secret_value(),
            algorithms=[settings.JWT_ALGORITHM],
            options={"require": require},
        )
    except jwt.InvalidTokenError as exc:
        # The base class for every PyJWT failure: expired, bad signature,
        # malformed, wrong algorithm, missing required claim. Catching the
        # base class means a new failure mode in a future version is handled
        # safely by default rather than escaping as a 500.
        raise TokenError("could not validate token") from exc

    if payload.get("type") != expected_type:
        # An access token presented where a refresh token is required, or the
        # reverse. See ACCESS_TOKEN_TYPE above for why this matters more than
        # it looks like it should.
        raise TokenError("wrong token type")

    return payload


def _uuid_claim(payload: dict[str, Any], name: str) -> uuid.UUID:
    """Read one claim that must be a UUID.

    A validly-signed token whose `sub` or `jti` is not a UUID means our own
    issuing code changed shape - nobody else can produce a signature. It is
    still a TokenError to the caller, who can do nothing differently either
    way, but it must never escape as a 500.
    """
    value = payload.get(name)

    if not isinstance(value, str):  # pragma: no cover - `require` guarantees it
        raise TokenError(f"malformed {name}")

    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise TokenError(f"malformed {name}") from exc


def decode_access_token(token: str) -> uuid.UUID:
    """Verify an access token and return the user id it identifies.

    Raises:
        TokenError: for every failure mode, without distinguishing them.
    """
    payload = _decode(token, expected_type=ACCESS_TOKEN_TYPE, require=["exp", "sub"])
    return _uuid_claim(payload, "sub")


def decode_refresh_token(token: str) -> RefreshTokenClaims:
    """Verify a refresh token and return the ids it carries.

    A PASSING RETURN FROM THIS FUNCTION IS NOT SUFFICIENT TO REFRESH.

    It proves only that we signed this token and that it has not yet expired.
    It says nothing about whether the token has already been used, been
    revoked, or belongs to a session we tore down - all of which live in the
    database, not in the signature. `services/refresh.py` performs those
    checks, and this function must never be called without it.

    Requiring `jti` is what makes that possible: a refresh token with no id
    names no row, so there would be nothing to revoke.

    Raises:
        TokenError: for every failure mode, without distinguishing them.
    """
    payload = _decode(token, expected_type=REFRESH_TOKEN_TYPE, require=["exp", "sub", "jti"])

    return RefreshTokenClaims(
        user_id=_uuid_claim(payload, "sub"),
        token_id=_uuid_claim(payload, "jti"),
    )
