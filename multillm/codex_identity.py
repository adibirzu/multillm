# SPDX-License-Identifier: Apache-2.0

"""Privacy-preserving local identity signal for Codex-gated operator features."""

from __future__ import annotations

import base64
import json
from pathlib import Path


CODEX_AUTH_FILE = Path.home() / ".codex" / "auth.json"


def _jwt_claims(token: object) -> dict:
    """Decode an untrusted local JWT payload without logging or returning it."""
    if not isinstance(token, str):
        return {}
    parts = token.split(".")
    if len(parts) != 3 or not parts[1]:
        return {}
    try:
        encoded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return {}
    return claims if isinstance(claims, dict) else {}


def get_codex_login_identity() -> dict:
    """Return only a ChatGPT-authenticated Codex email domain, if available.

    The raw email and bearer tokens never leave this module. The result is an
    UI eligibility signal, not an authorization mechanism for remote clients.
    """
    try:
        with open(CODEX_AUTH_FILE) as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {"authenticated": False}

    if not isinstance(payload, dict) or payload.get("auth_mode") != "chatgpt":
        return {"authenticated": False}
    tokens = payload.get("tokens")
    if not isinstance(tokens, dict):
        return {"authenticated": False}
    email = _jwt_claims(tokens.get("id_token")).get("email")
    if not isinstance(email, str) or "@" not in email:
        return {"authenticated": False}
    domain = email.rsplit("@", 1)[1].strip().lower()
    if not domain or len(domain) > 253:
        return {"authenticated": False}
    return {"authenticated": True, "authMode": "chatgpt", "emailDomain": domain}
