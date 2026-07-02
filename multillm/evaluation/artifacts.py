"""Authenticated encryption for retained evaluation prompts and outputs."""

from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


_MAGIC = b"MLEV1"


class ArtifactCipher:
    """AES-256-GCM envelope with explicit associated-data binding."""

    def __init__(self, key: bytes):
        if len(key) != 32:
            raise ValueError("artifact encryption key must be exactly 32 bytes")
        self._cipher = AESGCM(key)

    @classmethod
    def from_base64(cls, encoded_key: str) -> "ArtifactCipher":
        try:
            key = base64.urlsafe_b64decode(encoded_key.encode("ascii"))
        except (ValueError, UnicodeError) as exc:
            raise ValueError("artifact encryption key is not valid base64") from exc
        return cls(key)

    def encrypt(self, plaintext: bytes, *, associated_data: bytes) -> bytes:
        nonce = os.urandom(12)
        return _MAGIC + nonce + self._cipher.encrypt(nonce, plaintext, associated_data)

    def decrypt(self, envelope: bytes, *, associated_data: bytes) -> bytes:
        if not envelope.startswith(_MAGIC) or len(envelope) < len(_MAGIC) + 13:
            raise ValueError("artifact decrypt failed: invalid envelope")
        nonce_start = len(_MAGIC)
        nonce = envelope[nonce_start : nonce_start + 12]
        ciphertext = envelope[nonce_start + 12 :]
        try:
            return self._cipher.decrypt(nonce, ciphertext, associated_data)
        except InvalidTag as exc:
            raise ValueError(
                "artifact decrypt failed: authentication mismatch"
            ) from exc
