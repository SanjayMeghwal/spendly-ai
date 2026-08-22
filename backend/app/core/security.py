"""Security primitives: password hashing and JSON Web Tokens.

This module is deliberately free of FastAPI, SQLAlchemy, and any notion of a
"user". It turns strings into hashes and claims into tokens. That isolation is
what makes it testable without a database and reusable from a CLI or a worker.

Translating the failures here into HTTP status codes is the API layer's job.
"""

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import (
    HashingError,
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

from app.core.config import get_settings
from app.core.ids import uuid7

settings = get_settings()

# ==============================================================================
# Password hashing
# ==============================================================================
#
# WHY HASH AT ALL, AND WHY A *SLOW* HASH
#
# A password database will eventually leak - through SQL injection, a stolen
# backup, or a misconfigured bucket. Hashing means the attacker gets hashes
# rather than passwords, and the only way back to a password is to guess one,
# hash it, and compare.
#
# So the entire defence is: make each guess expensive. A general-purpose hash
# like SHA-256 is built to be FAST, which is exactly wrong here - commodity
# hardware computes billions of SHA-256 guesses per second. A password hash is
# built to be deliberately slow and to consume memory.
#
# argon2id (chosen over bcrypt) is *memory-hard*: verifying costs ~64 MiB of
# RAM by default. An attacker with a GPU has thousands of cores but nowhere
# near thousands x 64 MiB of fast memory, so the parallelism advantage that
# makes GPUs devastating against bcrypt largely evaporates.
#
# The defaults from argon2-cffi track the current OWASP recommendation. They
# are not hardcoded here: PasswordHasher stores its parameters inside each hash
# string, so raising them later automatically applies to new hashes, and
# password_needs_rehash() identifies old ones.
# ------------------------------------------------------------------------------

_password_hasher = PasswordHasher()

# Upper bound on accepted password length, enforced again in the Pydantic
# schema. argon2 has no 72-byte truncation problem the way bcrypt does, but an
# unbounded input is a denial-of-service lever: hashing a 10 MB "password"
# burns CPU and memory on demand, and the endpoint is unauthenticated.
MAX_PASSWORD_LENGTH = 128

# A real argon2 hash of a value nobody knows, computed once at import.
#
# Used to keep login timing constant. If a lookup for an unknown email returned
# immediately while a known email spent ~50 ms hashing, the response time alone
# would reveal which addresses have accounts - a user-enumeration oracle that
# needs no error message to work. Verifying against this hash makes the
# unknown-email path cost the same as the wrong-password path.
_DUMMY_PASSWORD_HASH = _password_hasher.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    """Hash a plaintext password for storage.

    The returned string is self-describing - it encodes the algorithm, the
    parameters, and a per-password random salt, so it carries everything
    verification needs.

    Because the salt is random and embedded, hashing the same password twice
    yields two different strings. That is correct and required: identical
    hashes would tell an attacker which users share a password, and would let
    one precomputed table crack all of them at once.
    """
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"Password exceeds {MAX_PASSWORD_LENGTH} characters")
    return _password_hasher.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Return whether a plaintext password matches a stored hash.

    Returns False rather than raising, for every failure mode - wrong password,
    corrupt hash, unreadable hash. The caller's question is "should this login
    succeed?", and the answer to all three is no.

    The comparison inside argon2-cffi is constant-time, so it does not leak how
    many leading characters of the digest matched.
    """
    try:
        _password_hasher.verify(hashed_password, plain_password)
    except VerifyMismatchError:
        # The ordinary case: a wrong password.
        return False
    except (VerificationError, InvalidHashError, HashingError):
        # A malformed or truncated hash in the database. Not the user's fault,
        # but it cannot authenticate them either.
        return False
    return True


def fake_verify_password() -> None:
    """Burn the same CPU time a real verification would, and discard the result.

    Called on the login path when no account matches the submitted email, so
    that a request for a non-existent address takes as long as one for a real
    address with the wrong password.
    """
    verify_password("dummy-password", _DUMMY_PASSWORD_HASH)


def password_needs_rehash(hashed_password: str) -> bool:
    """Return whether a stored hash used weaker parameters than we now require.

    Hashing parameters should rise as hardware gets faster. Old hashes cannot
    be upgraded in place - the plaintext is gone - so they can only be replaced
    at the one moment the plaintext is briefly available: a successful login.
    """
    try:
        return _password_hasher.check_needs_rehash(hashed_password)
    except InvalidHashError:
        # Unparseable hash. It cannot be verified against anyway, so there is
        # nothing useful to rehash.
        return False


# ==============================================================================
# JSON Web Tokens
# ==============================================================================
#
# WHAT A JWT IS - AND WHAT IT IS NOT
#
# A JWT is three base64url segments joined by dots: header.payload.signature.
# The payload is ENCODED, NOT ENCRYPTED. Anyone holding a token can paste it
# into a decoder and read every claim. Never put anything confidential in one.
#
# What the signature guarantees is integrity: it is an HMAC over the first two
# segments, keyed by SECRET_KEY. Change a single character of the payload and
# the signature no longer matches. So a client can read its token but cannot
# forge or edit one - which is what lets the server trust the claims without a
# database lookup.
#
# The cost of that statelessness: a token stays valid until it expires. There
# is no list to remove it from. That is the trade-off we accepted in choosing
# access-token-only, and why ACCESS_TOKEN_EXPIRE_MINUTES is short.
# ------------------------------------------------------------------------------

# The `typ` claim distinguishes this token from other kinds we may mint later
# (a refresh token, a password-reset token). Without it, any token signed by
# the same key is interchangeable, and a long-lived refresh token could be
# presented as an access token. Checking it costs one comparison and closes an
# entire class of token-confusion bugs.
ACCESS_TOKEN_TYPE = "access"


class InvalidTokenError(Exception):
    """A token was absent, malformed, expired, mis-signed, or the wrong type.

    One exception for every failure, on purpose. A caller must not be able to
    tell "this token expired" from "this signature is forged", because the
    distinction is useful only to an attacker probing the endpoint.
    """


@dataclass(frozen=True)
class TokenClaims:
    """The claims we care about, extracted and typed.

    A plain dataclass rather than a Pydantic model so that this module stays
    independent of the API schema layer - core/ must not depend on the shape of
    our HTTP contract.
    """

    subject: uuid.UUID
    expires_at: datetime
    issued_at: datetime
    token_id: str


def create_access_token(
    subject: uuid.UUID,
    expires_delta: timedelta | None = None,
) -> str:
    """Mint a signed access token identifying `subject`.

    `subject` is the user's ID, never their email: emails change, and a token
    keyed to a mutable value silently points at the wrong thing (or nothing)
    after an address update.
    """
    now = datetime.now(UTC)
    expire = now + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )

    payload = {
        # Registered claim names from RFC 7519. Using the standard names means
        # PyJWT validates `exp` for us rather than us remembering to.
        "sub": str(subject),
        "exp": expire,
        "iat": now,
        # A unique ID per token. Two tokens minted for the same user in the
        # same second would otherwise be byte-identical, which makes them
        # impossible to tell apart in a log - or in a test asserting that
        # logging in twice produces two distinct tokens.
        "jti": str(uuid7()),
        "typ": ACCESS_TOKEN_TYPE,
    }

    return jwt.encode(
        payload,
        settings.SECRET_KEY.get_secret_value(),
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_access_token(token: str) -> TokenClaims:
    """Verify a token's signature and claims, or raise InvalidTokenError.

    THE `algorithms` ARGUMENT IS A SECURITY CONTROL, NOT CONFIGURATION.

    A JWT's header names the algorithm it was signed with, and a naive verifier
    trusts it. That trust is the classic JWT attack: the attacker rewrites the
    header to `alg: none`, strips the signature, and the token verifies. A
    subtler variant rewrites it to HS256 on a service expecting RS256, so the
    *public* key - which the attacker has - gets used as the HMAC secret.

    Passing an explicit allow-list means the header is never consulted for that
    decision. PyJWT requires this argument precisely because omitting it was
    such a reliable source of vulnerabilities.
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY.get_secret_value(),
            algorithms=[settings.JWT_ALGORITHM],
            options={
                # Refuse a token missing any of these rather than treating an
                # absent claim as acceptable. A token with no `exp` would
                # otherwise be valid forever.
                "require": ["sub", "exp", "iat", "jti"],
                "verify_signature": True,
                "verify_exp": True,
            },
        )
    except jwt.PyJWTError as exc:
        # Covers ExpiredSignatureError, InvalidSignatureError, DecodeError,
        # MissingRequiredClaimError, and the rest. Collapsed into one error so
        # the caller cannot leak which it was.
        raise InvalidTokenError(str(exc)) from exc

    if payload.get("typ") != ACCESS_TOKEN_TYPE:
        raise InvalidTokenError("Token is not an access token")

    try:
        subject = uuid.UUID(payload["sub"])
    except (KeyError, ValueError, TypeError) as exc:
        # A correctly signed token whose `sub` is not a UUID means either our
        # own minting code is wrong or SECRET_KEY has leaked. Either way this
        # is not a usable identity.
        raise InvalidTokenError("Token subject is not a valid user ID") from exc

    return TokenClaims(
        subject=subject,
        expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        issued_at=datetime.fromtimestamp(payload["iat"], tz=UTC),
        token_id=str(payload["jti"]),
    )
