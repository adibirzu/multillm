"""
Gateway authentication — API key validation middleware.

Set MULTILLM_API_KEY to require authentication on protected endpoints.
When unset, the gateway runs in open mode (localhost-only recommended).

Keys can be passed via:
  - Header: X-API-Key: <key>
  - Header: Authorization: Bearer <key>
"""

import logging
import os
import secrets

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

log = logging.getLogger("multillm.auth")

# Load API key from env. If not set, auth is disabled (open mode).
API_KEY = os.getenv("MULTILLM_API_KEY", "")

PUBLIC_DASHBOARD_API = os.getenv("MULTILLM_PUBLIC_DASHBOARD_API", "false").lower() in ("true", "1", "yes")
PUBLIC_EXACT_PATHS = {"/", "/health", "/dashboard"}
PUBLIC_PREFIXES = ("/static/",)
OPTIONAL_PUBLIC_READONLY_API_PREFIXES = (
    "/api/dashboard",
    "/api/sessions",
    "/api/active-sessions",
    "/api/backends",
    "/api/claude-stats",
    "/api/codex-stats",
    "/api/gemini-stats",
    "/api/all-llm-usage",
    "/api/cache",
    "/api/otel",
    "/api/rate-limit",
    "/api/health",
    "/api/routing/scores",
    "/api/auth",
    "/api/status",
)
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def auth_enabled() -> bool:
    return bool(API_KEY)


def _extract_key(request: Request) -> str:
    """Extract API key from request headers."""
    # Check X-API-Key header first
    key = request.headers.get("x-api-key", "")
    if key:
        return key
    # Fall back to Authorization: Bearer <key>
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _is_public_request(request: Request) -> bool:
    path = request.url.path
    if path in PUBLIC_EXACT_PATHS:
        return True
    if any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES):
        return True
    if PUBLIC_DASHBOARD_API and request.method in SAFE_METHODS and any(
        path == prefix or path.startswith(f"{prefix}/")
        for prefix in OPTIONAL_PUBLIC_READONLY_API_PREFIXES
    ):
        return True
    return False


class AuthMiddleware(BaseHTTPMiddleware):
    """Validates API key on protected endpoints when MULTILLM_API_KEY is set."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not API_KEY:
            return await call_next(request)

        # Skip auth for public endpoints.
        if _is_public_request(request):
            return await call_next(request)

        provided = _extract_key(request)
        if not provided:
            path = request.url.path
            log.warning("Auth: missing key for %s %s from %s", request.method, path, request.client.host if request.client else "unknown")
            return JSONResponse(status_code=401, content={"detail": "API key required. Set X-API-Key header."})

        if not secrets.compare_digest(provided, API_KEY):
            path = request.url.path
            log.warning("Auth: invalid key for %s %s from %s", request.method, path, request.client.host if request.client else "unknown")
            return JSONResponse(status_code=403, content={"detail": "Invalid API key."})

        return await call_next(request)
