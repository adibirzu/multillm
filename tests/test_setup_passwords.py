# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Tests for ``multillm.setup.passwords`` (Argon2id hashing).

Covers behaviour required by Plan 01-07 Task 1:

- hash_password returns an Argon2id encoded string
- verify_password is True for matching plaintext and False for mismatches
- Length validation enforces MIN_PASSWORD_LEN
- Salt is random (two hashes of the same plaintext differ)
- Verification timing has bounded variation (sanity proxy for constant-time)
"""

from __future__ import annotations

import statistics
import time

import pytest


VALID_PASSWORD = "correct horse battery staple 9!"


def test_hash_password_returns_argon2id_string() -> None:
    from multillm.setup.passwords import hash_password

    h = hash_password(VALID_PASSWORD)

    assert isinstance(h, str)
    assert h.startswith("$argon2id$"), f"expected argon2id encoding, got: {h[:20]}"


def test_verify_password_accepts_matching_plaintext() -> None:
    from multillm.setup.passwords import hash_password, verify_password

    h = hash_password(VALID_PASSWORD)

    assert verify_password(h, VALID_PASSWORD) is True


def test_verify_password_rejects_mismatch() -> None:
    from multillm.setup.passwords import hash_password, verify_password

    h = hash_password(VALID_PASSWORD)

    assert verify_password(h, "wrong password indeed!") is False


def test_verify_password_rejects_invalid_hash_without_raising() -> None:
    from multillm.setup.passwords import verify_password

    assert verify_password("not-a-real-hash", VALID_PASSWORD) is False


def test_hash_password_rejects_short_password() -> None:
    from multillm.setup.passwords import MIN_PASSWORD_LEN, hash_password

    short = "x" * (MIN_PASSWORD_LEN - 1)
    with pytest.raises(ValueError):
        hash_password(short)


def test_hash_password_produces_random_salt() -> None:
    from multillm.setup.passwords import hash_password

    h1 = hash_password(VALID_PASSWORD)
    h2 = hash_password(VALID_PASSWORD)

    assert h1 != h2, (
        "consecutive hashes of the same plaintext must differ (random salt)"
    )


def test_verify_password_timing_has_bounded_variation() -> None:
    """Weak proxy for constant-time verification.

    We measure verify_password 100 times on a mismatch and assert the
    coefficient of variation of elapsed time stays under 0.5. The intent is
    to catch gross timing leaks (e.g. a naive ``hmac.compare_digest`` regression);
    a true constant-time guarantee comes from argon2-cffi's C implementation.
    """
    from multillm.setup.passwords import hash_password, verify_password

    h = hash_password(VALID_PASSWORD)
    elapsed: list[float] = []
    for _ in range(100):
        t0 = time.perf_counter()
        verify_password(h, "wrong password indeed!")
        elapsed.append(time.perf_counter() - t0)

    # Drop the slowest decile: GC pauses and scheduler jitter on shared/loaded
    # CI runners produce occasional spikes that are environmental, not a timing
    # leak. Trimming them keeps this a meaningful (if weak) constant-time proxy
    # without flaking. A real systematic leak still inflates the trimmed CV.
    elapsed.sort()
    trimmed = elapsed[: int(len(elapsed) * 0.9)]
    mean = statistics.mean(trimmed)
    stdev = statistics.pstdev(trimmed)
    cv = stdev / mean if mean else 0.0
    assert cv < 0.5, f"verify_password timing variation too high: cv={cv:.3f}"
