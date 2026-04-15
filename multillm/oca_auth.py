"""
Oracle Code Assist (OCA) OAuth PKCE authentication.

Manages token caching, refresh, and bearer token retrieval.
Tokens are stored at ~/.oca/token.json (shared with the OCA VS Code extension).
"""

import json
import logging
import time
from pathlib import Path
from typing import Optional

import httpx

from .config import OCA_IDCS_URL, OCA_CLIENT_ID, OCA_TOKEN_CACHE

log = logging.getLogger("multillm.oca")

TOKEN_FILE = OCA_TOKEN_CACHE / "token.json"
REFRESH_THRESHOLD = 180  # Refresh if < 3 minutes remaining
OCA_LOGIN_COMMAND = "multillm-oca-login"
OCA_LOGIN_HINT = f"Run: {OCA_LOGIN_COMMAND}"


def _read_cached_token() -> Optional[dict]:
    """Read token from ~/.oca/token.json."""
    if not TOKEN_FILE.exists():
        return None
    try:
        with open(TOKEN_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _write_cached_token(token_data: dict) -> None:
    """Write token to ~/.oca/token.json with restricted permissions."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f, indent=2)
    TOKEN_FILE.chmod(0o600)


def _is_expired(token_data: dict) -> bool:
    """Check if access token is expired or about to expire."""
    expires_at = token_data.get("expiresAt", token_data.get("expires_at", 0))
    return time.time() > (expires_at - REFRESH_THRESHOLD)


async def _refresh_token(refresh_token: str) -> Optional[dict]:
    """Refresh the OAuth token using the refresh token."""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{OCA_IDCS_URL}/oauth2/v1/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": OCA_CLIENT_ID,
                    "refresh_token": refresh_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            r.raise_for_status()
            data = r.json()

        token_data = {
            "accessToken": data["access_token"],
            "refreshToken": data.get("refresh_token", refresh_token),
            "expiresAt": time.time() + data.get("expires_in", 3600),
        }
        _write_cached_token(token_data)
        log.info("OCA token refreshed successfully")
        return token_data
    except Exception as e:
        log.error("OCA token refresh failed: %s", e)
        return None


async def get_oca_bearer_token() -> Optional[str]:
    """
    Get a valid OCA bearer token.

    Reads from cache, refreshes if needed. Returns None if not authenticated.
    """
    token_data = _read_cached_token()
    if not token_data:
        log.warning(
            "No OCA token cached. Authenticate via OCA VS Code extension "
            "or run '%s'",
            OCA_LOGIN_COMMAND,
        )
        return None

    access_token = token_data.get("accessToken", token_data.get("access_token"))
    refresh_token = token_data.get("refreshToken", token_data.get("refresh_token"))

    if _is_expired(token_data):
        if refresh_token:
            refreshed = await _refresh_token(refresh_token)
            if refreshed:
                return refreshed["accessToken"]
        log.warning("OCA token expired and refresh failed")
        return None

    return access_token


def _load_cached_oca_models() -> list[dict]:
    """Load models from ~/.oca/models.json (maintained by OCA VS Code extension)."""
    cache_path = OCA_TOKEN_CACHE / "models.json"
    if not cache_path.exists():
        return []
    try:
        with open(cache_path) as f:
            data = json.load(f)
        models = data.get("models", [])
        if models:
            log.info("Loaded %d models from OCA cache (%s)", len(models), cache_path)
            return models
    except (json.JSONDecodeError, OSError) as e:
        log.debug("Failed to load cached OCA models: %s", e)
    return []


_HARDCODED_OCA_FALLBACK = [
    {"id": "oca/gpt5", "owned_by": "oracle-code-assist"},
    {"id": "oca/llama4", "owned_by": "oracle-code-assist"},
    {"id": "oca/grok4", "owned_by": "oracle-code-assist"},
    {"id": "oca/openai-o3", "owned_by": "oracle-code-assist"},
    {"id": "oca/gpt-4.1", "owned_by": "oracle-code-assist"},
    {"id": "oca/grok3", "owned_by": "oracle-code-assist"},
    {"id": "oca/grok4-fast-reasoning", "owned_by": "oracle-code-assist"},
    {"id": "oca/grok-code-fast-1", "owned_by": "oracle-code-assist"},
    {"id": "oca/gpt-oss-120b", "owned_by": "oracle-code-assist"},
]


async def get_oca_models() -> list[dict]:
    """Fetch available models from OCA endpoint, cache file, or hardcoded fallback."""
    from .config import OCA_ENDPOINT, OCA_API_VERSION

    token = await get_oca_bearer_token()
    if not token:
        # Try cache file first, then hardcoded fallback
        cached = _load_cached_oca_models()
        return cached if cached else _HARDCODED_OCA_FALLBACK

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{OCA_ENDPOINT}/{OCA_API_VERSION}/app/litellm/models",
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
            data = r.json()
        return data.get("data", data.get("models", []))
    except Exception as e:
        log.warning("Could not fetch OCA models from API: %s", e)
        cached = _load_cached_oca_models()
        return cached if cached else _HARDCODED_OCA_FALLBACK
