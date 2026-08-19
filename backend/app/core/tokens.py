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
from typing import Any

import jwt

from app.core.config import get_settings

# Marks a token as an ACCESS token.
#
# This costs one claim now and prevents a whole vulnerability class later.
# When refresh tokens arrive, both kinds will be signed by the same key - so
# without a type claim, a long-lived refresh token would verify perfectly as
# an access token, silently handing an attacker a 30-day session. Rejecting
# the wrong type is only possible if the type is recorded in the first place,
# and retrofitting a claim means invalidating every token already issued.
ACCESS_TOKEN_TYPE = "access"


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


def decode_access_token(token: str) -> uuid.UUID:
    """Verify a token and return the user id it identifies.

    Raises:
        TokenError: for every failure mode, without distinguishing them.

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
    """
    settings = get_settings()

    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.SECRET_KEY.get_secret_value(),
            algorithms=[settings.JWT_ALGORITHM],
            # Refuse a token that carries no expiry. Without this, a token
            # missing `exp` verifies happily and never expires - and the
            # tokens most likely to lack it are forged ones.
            options={"require": ["exp", "sub"]},
        )
    except jwt.InvalidTokenError as exc:
        # The base class for every PyJWT failure: expired, bad signature,
        # malformed, wrong algorithm, missing required claim. Catching the
        # base class means a new failure mode in a future version is handled
        # safely by default rather than escaping as a 500.
        raise TokenError("could not validate token") from exc

    if payload.get("type") != ACCESS_TOKEN_TYPE:
        # Presenting a refresh token where an access token is required. Not
        # yet reachable - refresh tokens do not exist - but the check must
        # exist BEFORE they do, or adding them introduces the hole.
        raise TokenError("wrong token type")

    subject = payload.get("sub")
    if not isinstance(subject, str):  # pragma: no cover - `require` guarantees it
        raise TokenError("malformed subject")

    try:
        return uuid.UUID(subject)
    except ValueError as exc:
        # A validly-signed token whose subject is not a UUID means our own
        # issuing code changed shape. Still a TokenError to the caller, who
        # can do nothing differently either way.
        raise TokenError("malformed subject") from exc
