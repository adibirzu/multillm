# SPDX-License-Identifier: Apache-2.0

"""Tests for privacy-preserving local Codex identity detection."""

import base64
import json

from multillm import codex_identity


def _id_token(claims: dict) -> str:
    def encode(value: object) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{encode({'alg': 'none'})}.{encode(claims)}.signature"


def test_codex_identity_exposes_only_oracle_email_domain(tmp_path, monkeypatch):
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {"id_token": _id_token({"email": "dev@oracle.com"})},
            }
        )
    )
    monkeypatch.setattr(codex_identity, "CODEX_AUTH_FILE", auth_file)

    assert codex_identity.get_codex_login_identity() == {
        "authenticated": True,
        "authMode": "chatgpt",
        "emailDomain": "oracle.com",
    }


def test_codex_identity_does_not_treat_api_key_login_as_domain_identity(
    tmp_path, monkeypatch
):
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(
        json.dumps(
            {
                "auth_mode": "api_key",
                "tokens": {"id_token": _id_token({"email": "dev@oracle.com"})},
            }
        )
    )
    monkeypatch.setattr(codex_identity, "CODEX_AUTH_FILE", auth_file)

    assert codex_identity.get_codex_login_identity() == {"authenticated": False}
