"""Password hashing primitives.

This module is the ONLY place in the application that knows what a password
hash is made of. Everything else calls these four functions. Concentrating it
here means that changing the algorithm - or its cost parameters - is a change
to one file rather than a hunt through the service layer.

Nothing here imports FastAPI or SQLAlchemy: `core/` holds primitives, not
plumbing. That is also what makes these functions trivially testable without a
database or an HTTP client.

WHY ARGON2ID
Argon2id won the 2015 Password Hashing Competition and is OWASP's first
recommendation. Its important property is that it is MEMORY-HARD: producing a
hash requires 64 MiB of working memory, not just CPU time. An attacker's
advantage in a brute-force attack comes from massive parallelism - thousands
of GPU cores - and while cores are cheap, giving every core 64 MiB of its own
is not. That is the property bcrypt lacks.

Argon2id specifically (rather than Argon2i or Argon2d) is the hybrid variant,
resistant to both side-channel attacks and GPU cracking. It is what
`PasswordHasher()` selects by default.

WHY NOT PASSLIB
The FastAPI tutorial still demonstrates `passlib`. It has had no release since
2020 and raises on modern bcrypt versions. argon2-cffi is actively maintained,
ships type hints, and needs no wrapper: it already provides hashing,
verification, and cost-upgrade detection.
"""

from contextlib import suppress
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

# ------------------------------------------------------------------------------
# The hasher
#
# Built once at import and reused. It is stateless and thread-safe, and
# constructing one per call would serve no purpose.
#
# The library's defaults are used deliberately rather than hand-tuned numbers:
#   time_cost=3, memory_cost=65536 KiB (64 MiB), parallelism=4,
#   hash_len=32, salt_len=16
# These meet or exceed the OWASP Argon2id recommendation, and the maintainers
# revise them as hardware improves - so inheriting the defaults means we get
# those revisions on upgrade instead of carrying today's numbers forward for
# a decade.
#
# COST OF THIS CHOICE, measured on this machine: ~64ms per hash and per
# verify. That is not a bug, it IS the security property - it caps an attacker
# at roughly 15 guesses per second per core instead of billions. It also means
# login is deliberately the slowest endpoint in the application, and that
# 64 MiB is allocated per concurrent login.
# ------------------------------------------------------------------------------
_hasher = PasswordHasher()

# ------------------------------------------------------------------------------
# Password policy
#
# Enforced by the Pydantic schema at the API boundary; repeated inside
# hash_password as defence in depth, so a service that bypasses the schema
# cannot store something outside the policy.
#
# MINIMUM: length is the single biggest contributor to password strength -
# far more than the mandatory-symbol rules that mostly produce "Password1!".
# NIST SP 800-63B now advises against composition rules for exactly that
# reason, and recommends length instead.
#
# MAXIMUM: a cap is a DENIAL-OF-SERVICE control, not a security requirement.
# Argon2 has no equivalent of bcrypt's 72-byte truncation, so a long password
# is genuinely used in full - but an unbounded field lets an attacker post a
# 10 MB "password" and make the server do 10 MB of hashing work per request.
# 128 characters is far above any real passphrase.
# ------------------------------------------------------------------------------
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128


def hash_password(password: str) -> str:
    """Hash a plaintext password for storage.

    Returns the PHC-format encoded string, which looks like:

        $argon2id$v=19$m=65536,t=3,p=4$<salt-b64>$<hash-b64>

    Everything needed to verify later is in that one string: the algorithm,
    its version, the cost parameters, and the RANDOM SALT. Nothing extra needs
    storing, and no separate salt column is required - a design that predates
    modern hashes and is a common source of mistakes.

    Because the salt is random and generated per call, hashing the same
    password twice returns DIFFERENT strings. Two hashes must therefore never
    be compared with `==`; use verify_password.

    Raises:
        ValueError: if the password violates the length policy. The API layer
            validates first, so reaching this means a caller bypassed the
            schema - which should fail loudly rather than silently store a
            password outside the policy.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise ValueError(f"password must be at most {MAX_PASSWORD_LENGTH} characters")

    return _hasher.hash(password)


def verify_password(password: str, hashed_password: str) -> bool:
    """Check a plaintext password against a stored hash.

    Returns a plain bool rather than raising, because the CALLER must not be
    able to distinguish the reasons for failure. argon2 raises three different
    exceptions here - wrong password, malformed hash, unsupported parameters -
    and letting those propagate would turn an implementation detail into an
    oracle for an attacker probing the login endpoint.

    Note the exception classes caught. `InvalidHashError` subclasses
    **ValueError**, not `Argon2Error`, so catching `Argon2Error` alone would
    let a corrupted hash in the database escape as an unhandled ValueError -
    a 500 from the login endpoint instead of a clean 401.

    The comparison inside argon2 is constant-time with respect to the hash, so
    an attacker cannot narrow down the correct value byte by byte from timing.
    """
    try:
        return _hasher.verify(hashed_password, password)
    except (VerificationError, InvalidHashError):
        # Deliberately no logging of the password or the hash. Both are
        # credentials; a log line containing either is a breach waiting to be
        # grepped.
        return False


def needs_rehash(hashed_password: str) -> bool:
    """Report whether a stored hash was made with outdated cost parameters.

    Cost parameters must rise as hardware gets faster, but existing hashes
    cannot be upgraded in bulk - we do not hold anyone's password, which is
    the entire point. The only moment the plaintext is available is during a
    successful login.

    So the upgrade path is: verify the password, and if this returns True,
    re-hash the same plaintext with current parameters and store it. Users are
    migrated silently as they log in, and nobody is forced to reset anything.

    Called only AFTER verify_password returns True. A malformed hash returns
    True here, which is harmless: that row needs replacing regardless.
    """
    try:
        return _hasher.check_needs_rehash(hashed_password)
    except InvalidHashError:
        return True


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """A throwaway hash of a fixed string, computed once on first use.

    Cached rather than computed at import so that startup, and any test not
    touching authentication, does not pay the ~64ms.

    The value hashed here is secret from nobody and is never compared against
    a real password. It exists only to consume a realistic amount of time.
    """
    return _hasher.hash("a-password-that-belongs-to-no-account")


def dummy_verify() -> None:
    """Burn the same work a real verification costs. Used for unknown users.

    THE ATTACK THIS PREVENTS - user enumeration by timing.

    A login handler written the obvious way leaks membership:

        user = await get_user_by_email(email)
        if user is None:
            return 401              # returns in ~1ms
        if not verify_password(pw, user.hashed_password):
            return 401              # returns in ~64ms

    Both responses say "invalid credentials", so the BODY leaks nothing. The
    CLOCK leaks everything: a ~1ms rejection means no such account, a ~64ms
    rejection means the account exists and only the password was wrong. An
    attacker holding a list of email addresses learns exactly which people
    bank with us - and for a finance product, "is this person a customer" is
    itself sensitive, quite apart from being the first half of a targeted
    attack.

    Calling this on the not-found path makes both branches cost the same.

    This equalises the dominant cost, not every last nanosecond; a perfectly
    constant-time handler in Python is not achievable. Closing a 60x gap down
    to noise is what defeats the practical attack.
    """
    # The exception is always raised - the point is the elapsed time, not the
    # result. `suppress` states that the failure is intentional far more
    # clearly than a bare `except: pass`, which usually signals a swallowed bug.
    with suppress(VerificationError, InvalidHashError):
        _hasher.verify(_dummy_hash(), "definitely-the-wrong-password")
