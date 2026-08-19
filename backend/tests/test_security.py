"""Tests for the password hashing primitives.

No database and no HTTP client here - `core/` holds pure functions, and that
is exactly what makes them cheap to test thoroughly.

These tests are slower than they look: each hash costs ~64ms by design. That
cost is the security property, so it is not something to optimise away.
"""

import time

import pytest
from argon2 import PasswordHasher

from app.core.security import (
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    dummy_verify,
    hash_password,
    needs_rehash,
    verify_password,
)

# A realistic passphrase that satisfies the length policy.
PASSWORD = "correct-horse-battery-staple"


class TestHashing:
    """hash_password produces a modern, salted, self-describing hash."""

    def test_produces_an_argon2id_phc_string(self) -> None:
        """The prefix encodes the algorithm and its parameters.

        Asserting on it catches a silent downgrade - if someone swapped the
        hasher for argon2i, or for a bare SHA-256, this fails immediately
        rather than in a breach report.
        """
        hashed = hash_password(PASSWORD)

        assert hashed.startswith("$argon2id$")
        assert "m=65536" in hashed, "memory cost was weakened"
        assert "t=3" in hashed, "time cost was weakened"

    def test_never_contains_the_plaintext(self) -> None:
        """The most basic property of a hash, and worth stating explicitly."""
        hashed = hash_password(PASSWORD)

        assert PASSWORD not in hashed

    def test_same_password_hashes_differently_every_time(self) -> None:
        """Proves a random salt is applied per call.

        Without a unique salt, identical passwords produce identical hashes,
        so cracking one common password once breaks every account that shares
        it - which is precisely what rainbow tables exploit.

        This is also why hashes must never be compared with `==`.
        """
        assert hash_password(PASSWORD) != hash_password(PASSWORD)


class TestVerification:
    """verify_password answers one question and leaks nothing else."""

    def test_accepts_the_correct_password(self) -> None:
        assert verify_password(PASSWORD, hash_password(PASSWORD)) is True

    def test_rejects_a_wrong_password(self) -> None:
        assert verify_password("not-the-right-password", hash_password(PASSWORD)) is False

    def test_rejects_a_password_differing_only_in_case(self) -> None:
        """Passwords are case-SENSITIVE, unlike the email address.

        Worth pinning down: we deliberately normalise email case, and it would
        be an easy mistake to extend that normalisation to passwords, which
        would quietly divide the search space an attacker has to cover.
        """
        assert verify_password(PASSWORD.upper(), hash_password(PASSWORD)) is False

    @pytest.mark.parametrize(
        "malformed",
        [
            pytest.param("", id="empty"),
            pytest.param("not-a-hash-at-all", id="plain-text"),
            pytest.param("$argon2id$truncated", id="truncated-phc"),
            pytest.param("$2b$12$abcdefghijklmnopqrstuv", id="a-bcrypt-hash"),
        ],
    )
    def test_returns_false_for_a_malformed_stored_hash(self, malformed: str) -> None:
        """A corrupt hash must be a clean False, never an exception.

        This guards a specific trap: argon2 raises `InvalidHashError`, which
        subclasses **ValueError**, NOT `Argon2Error`. Catching `Argon2Error`
        alone - the obvious thing to write - lets a corrupted database row
        escape as an unhandled ValueError, turning a login attempt into a 500
        with a stack trace instead of a clean 401.
        """
        assert verify_password(PASSWORD, malformed) is False


class TestPasswordPolicy:
    """Length limits are enforced by the primitive, not only by the schema."""

    def test_policy_values_are_not_silently_weakened(self) -> None:
        """A guard on the constants themselves.

        Every other test in this class uses the constants, so lowering
        MIN_PASSWORD_LENGTH to 4 would leave them all green. This test is the
        one that notices.
        """
        assert MIN_PASSWORD_LENGTH >= 12
        assert MAX_PASSWORD_LENGTH <= 128

    def test_rejects_a_password_below_the_minimum(self) -> None:
        with pytest.raises(ValueError, match="at least"):
            hash_password("a" * (MIN_PASSWORD_LENGTH - 1))

    def test_rejects_a_password_above_the_maximum(self) -> None:
        """The cap is a denial-of-service control.

        Argon2 uses a long password in full - it has no equivalent of
        bcrypt's 72-byte truncation - so without a cap an attacker can post a
        multi-megabyte "password" and make the server hash all of it.
        """
        with pytest.raises(ValueError, match="at most"):
            hash_password("a" * (MAX_PASSWORD_LENGTH + 1))

    @pytest.mark.parametrize("length", [MIN_PASSWORD_LENGTH, MAX_PASSWORD_LENGTH])
    def test_accepts_the_exact_boundaries(self, length: int) -> None:
        """Off-by-one guard: the limits are inclusive.

        `<` versus `<=` here is the difference between a policy that says
        "at least 12" and one that silently demands 13.
        """
        assert hash_password("a" * length).startswith("$argon2id$")


class TestRehashDetection:
    """Cost parameters must be upgradable without anyone resetting a password."""

    def test_a_fresh_hash_does_not_need_rehashing(self) -> None:
        assert needs_rehash(hash_password(PASSWORD)) is False

    def test_a_hash_with_weaker_parameters_needs_rehashing(self) -> None:
        """This is the whole point of the function.

        We never hold anyone's password, so old hashes cannot be upgraded in
        bulk. The only moment the plaintext exists is during a successful
        login - so that is when a weak hash gets silently replaced.
        """
        weak = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)

        assert needs_rehash(weak.hash(PASSWORD)) is True

    def test_a_malformed_hash_needs_rehashing(self) -> None:
        """Harmless by construction: that row needs replacing regardless."""
        assert needs_rehash("not-a-hash") is True


class TestTimingEqualisation:
    """dummy_verify closes the timing side channel on unknown users."""

    @staticmethod
    def _median_ms(fn: object, runs: int = 3) -> float:
        """Median wall time of a callable, in milliseconds.

        Median rather than mean, so one scheduler hiccup on a loaded CI runner
        does not skew the result.
        """
        assert callable(fn)
        samples = []
        for _ in range(runs):
            start = time.perf_counter()
            fn()
            samples.append((time.perf_counter() - start) * 1000)
        return sorted(samples)[len(samples) // 2]

    def test_costs_about_as_much_as_a_real_failed_verification(self) -> None:
        """Prevents user enumeration by response timing.

        Rejecting an unknown email in ~1ms while rejecting a known email with
        a bad password in ~64ms tells an attacker which addresses have
        accounts - and for a finance product, "is this person a customer" is
        sensitive on its own.

        The assertion is a RATIO against a real verification rather than an
        absolute millisecond threshold, so it holds on a slow or loaded runner
        where both measurements scale together.

        The tolerance is deliberately wide. The attack needs a large,
        consistent gap - roughly 60x here. Anything within the same order of
        magnitude is lost in network jitter, and a no-op implementation would
        come back thousands of times faster, far outside this band.
        """
        real_hash = hash_password(PASSWORD)

        real_ms = self._median_ms(lambda: verify_password("wrong-password-here", real_hash))
        dummy_ms = self._median_ms(dummy_verify)

        assert dummy_ms > real_ms * 0.4, (
            f"dummy_verify took {dummy_ms:.1f}ms against a real {real_ms:.1f}ms - "
            "too fast to disguise an unknown user"
        )
        assert dummy_ms < real_ms * 4.0, (
            f"dummy_verify took {dummy_ms:.1f}ms against a real {real_ms:.1f}ms - "
            "conspicuously slow, which leaks just as much"
        )

    def test_returns_none_and_never_raises(self) -> None:
        """Called on the login failure path, so it must not add a failure mode."""
        assert dummy_verify() is None
