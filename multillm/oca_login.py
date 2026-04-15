"""
Oracle Code Assist (OCA) PKCE login helper for MultiLLM.

This command opens the Oracle login flow in a browser, listens for the
OAuth callback on ``http://127.0.0.1:48801/auth/oca``, and writes the shared
token cache to ``~/.oca/token.json``.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import http.server
import os
import secrets
import threading
import time
import webbrowser
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from .config import OCA_CLIENT_ID, OCA_IDCS_URL
from .oca_auth import OCA_LOGIN_COMMAND, TOKEN_FILE, _read_cached_token, _write_cached_token

SCOPES = "openid offline_access"
CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = int(os.getenv("OCA_CALLBACK_PORT", "48801"))
CALLBACK_PATH = "/auth/oca"


def _token_expiry(token_data: dict[str, Any]) -> float:
    """Return the token expiry epoch timestamp."""
    raw = token_data.get("expiresAt", token_data.get("expires_at", 0)) or 0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _print_status() -> int:
    """Print the current token cache status."""
    token_data = _read_cached_token()
    print(f"OCA token cache: {TOKEN_FILE}")
    if not token_data:
        print("Status: missing")
        return 0

    expires_at = _token_expiry(token_data)
    seconds_left = int(expires_at - time.time()) if expires_at else 0
    if seconds_left > 0:
        print(f"Status: valid ({seconds_left // 60}m remaining)")
    else:
        print("Status: expired")
    return 0


def _clear_token() -> int:
    """Remove the cached token file if present."""
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
        print(f"Removed {TOKEN_FILE}")
    else:
        print(f"No cached token at {TOKEN_FILE}")
    return 0


class OCALoginFlow:
    """Run the OCA PKCE browser login flow."""

    def __init__(self) -> None:
        self.auth_code: str | None = None
        self.auth_error: tuple[str, str] | None = None
        self.code_verifier = ""
        self.code_challenge = ""
        self.state = ""

    @property
    def redirect_uri(self) -> str:
        return f"http://{CALLBACK_HOST}:{CALLBACK_PORT}{CALLBACK_PATH}"

    def _ensure_config(self) -> None:
        if OCA_IDCS_URL and OCA_CLIENT_ID:
            return
        raise RuntimeError(
            "Missing OCA OAuth configuration. Set OCA_IDCS_URL + OCA_CLIENT_ID "
            "or OCA_IDCS_OAUTH_URL + OCA_IDCS_CLIENT_ID."
        )

    def _generate_pkce(self) -> None:
        verifier = secrets.token_urlsafe(32)
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        self.code_verifier = verifier
        self.code_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        self.state = secrets.token_urlsafe(16)

    def _authorization_url(self) -> str:
        params = {
            "response_type": "code",
            "client_id": OCA_CLIENT_ID,
            "redirect_uri": self.redirect_uri,
            "scope": SCOPES,
            "state": self.state,
            "code_challenge": self.code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{OCA_IDCS_URL}/oauth2/v1/authorize?{urlencode(params)}"

    def _callback_handler(self):
        flow = self

        class CallbackHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return None

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                if parsed.path != CALLBACK_PATH:
                    self.send_error(404)
                    return

                query = parse_qs(parsed.query)
                if query.get("state", [None])[0] != flow.state:
                    self.send_error(400, "Invalid state")
                    return

                if "code" in query:
                    flow.auth_code = query["code"][0]
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.end_headers()
                    self.wfile.write(
                        (
                            "<html><body style='font-family: sans-serif; padding: 2rem;'>"
                            "<h1>OCA login complete</h1>"
                            "<p>You can close this tab and return to the terminal.</p>"
                            "</body></html>"
                        ).encode("utf-8")
                    )
                    return

                error = query.get("error", ["unknown"])[0]
                description = query.get("error_description", [""])[0]
                flow.auth_error = (error, description)
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    (
                        "<html><body style='font-family: sans-serif; padding: 2rem;'>"
                        "<h1>OCA login failed</h1>"
                        f"<p>{error}</p><p>{description}</p>"
                        "</body></html>"
                    ).encode("utf-8")
                )

        return CallbackHandler

    async def _exchange_code(self) -> dict[str, Any]:
        token_url = f"{OCA_IDCS_URL}/oauth2/v1/token"
        data = {
            "grant_type": "authorization_code",
            "code": self.auth_code,
            "redirect_uri": self.redirect_uri,
            "client_id": OCA_CLIENT_ID,
            "code_verifier": self.code_verifier,
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                token_url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        response.raise_for_status()
        return response.json()

    def _cache_token(self, token_response: dict[str, Any]) -> None:
        access_token = token_response.get("access_token", token_response.get("accessToken"))
        refresh_token = token_response.get("refresh_token", token_response.get("refreshToken"))
        expires_in = int(token_response.get("expires_in", 3600))
        expires_at = time.time() + expires_in
        token_data = {
            **token_response,
            "accessToken": access_token,
            "access_token": access_token,
            "refreshToken": refresh_token,
            "refresh_token": refresh_token,
            "expiresAt": expires_at,
            "expires_at": expires_at,
        }
        _write_cached_token(token_data)

    async def run(self) -> int:
        self._ensure_config()
        self._generate_pkce()

        try:
            server = http.server.ThreadingHTTPServer(
                (CALLBACK_HOST, CALLBACK_PORT),
                self._callback_handler(),
            )
        except OSError as exc:
            print(f"Cannot listen on {self.redirect_uri}: {exc}")
            return 1

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        auth_url = self._authorization_url()
        print(f"Opening browser for OCA login on {self.redirect_uri}")
        print(auth_url)
        webbrowser.open(auth_url)

        try:
            deadline = time.time() + 300
            while time.time() < deadline:
                if self.auth_error:
                    error, description = self.auth_error
                    print(f"Login failed: {error} {description}".strip())
                    return 1
                if self.auth_code:
                    token_response = await self._exchange_code()
                    self._cache_token(token_response)
                    print(f"Token cached at {TOKEN_FILE}")
                    return 0
                await asyncio.sleep(0.25)
        except httpx.HTTPError as exc:
            print(f"Token exchange failed: {exc}")
            return 1
        finally:
            server.shutdown()
            server.server_close()

        print("Timed out waiting for the OCA OAuth callback.")
        return 1


def main() -> int:
    """CLI entry point for OCA login and token management."""
    parser = argparse.ArgumentParser(description="Authenticate Oracle Code Assist for MultiLLM.")
    parser.add_argument("--status", action="store_true", help="Show cached token status")
    parser.add_argument("--clear", action="store_true", help="Remove the cached token")
    args = parser.parse_args()

    if args.status:
        return _print_status()
    if args.clear:
        return _clear_token()

    try:
        return asyncio.run(OCALoginFlow().run())
    except RuntimeError as exc:
        print(exc)
        print(f"Hint: configure the OCA OAuth env vars, then run {OCA_LOGIN_COMMAND}.")
        return 1
    except KeyboardInterrupt:
        print("\nLogin cancelled.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
