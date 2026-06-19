# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Argon2id password hashing helpers (Plan 01-07 Task 1).

Wraps ``argon2.PasswordHasher`` with the project's password policy:

- Minimum length 12 (server-side enforced; client-side is UX-only).
- Hash output is the standard PHC string starting with ``$argon2id$``.
- ``verify_password`` returns ``bool`` instead of raising, matching the
  route handler's "valid / invalid" contract.

Phase 2b imports this module verbatim — do not re-implement password
hashing in the auth/tenancy plan.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

__all__ = ["MIN_PASSWORD_LEN", "hash_password", "verify_password"]


#: Minimum password length enforced by ``hash_password``.
#:
#: OWASP-2026 baseline is 8; the project's discipline is 12 to leave
#: headroom against dictionary attacks even with Argon2id's slow verify
#: time.
MIN_PASSWORD_LEN: int = 12


# argon2-cffi's default PasswordHasher is OWASP-aligned as of 2026
# (Argon2id, ~64 MiB memory, ~50ms verify). Reusing a single instance
# avoids re-deriving parameters per call.
_ph = PasswordHasher()


def hash_password(plaintext: str) -> str:
    """Hash a plaintext password with Argon2id.

    Raises:
        ValueError: if ``plaintext`` is shorter than :data:`MIN_PASSWORD_LEN`.
    """
    if not isinstance(plaintext, str):
        raise ValueError("password must be a string")
    if len(plaintext) < MIN_PASSWORD_LEN:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LEN} characters")
    return _ph.hash(plaintext)


def verify_password(hashed: str, plaintext: str) -> bool:
    """Verify ``plaintext`` against an Argon2id ``hashed`` string.

    Returns ``True`` on a match. Returns ``False`` for any mismatch,
    malformed hash, or empty input — never raises. Constant-time guarantees
    come from argon2-cffi's C implementation.
    """
    if not hashed or not isinstance(hashed, str):
        return False
    if not isinstance(plaintext, str):
        return False
    try:
        return _ph.verify(hashed, plaintext)
    except (VerifyMismatchError, InvalidHashError, VerificationError):
        return False
    except Exception:
        # Any other argon2 surface (e.g. memory allocation issue) is
        # treated as a verification failure rather than propagated.
        return False
