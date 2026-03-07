"""
Gateway authentication — API key validation middleware.

Set MULTILLM_API_KEY to require authentication on all /v1/* endpoints.
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

# Endpoints that never require auth (health, dashboard, static)
PUBLIC_PREFIXES = ("/health", "/dashboard", "/static/", "/docs", "/openapi.json", "/api/")


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


class AuthMiddleware(BaseHTTPMiddleware):
    """Validates API key on protected endpoints when MULTILLM_API_KEY is set."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not API_KEY:
            return await call_next(request)

        path = request.url.path
        # Skip auth for public endpoints
        if any(path.startswith(p) for p in PUBLIC_PREFIXES):
            return await call_next(request)

        provided = _extract_key(request)
        if not provided:
            log.warning("Auth: missing key for %s %s from %s", request.method, path, request.client.host if request.client else "unknown")
            return JSONResponse(status_code=401, content={"detail": "API key required. Set X-API-Key header."})

        if not secrets.compare_digest(provided, API_KEY):
            log.warning("Auth: invalid key for %s %s from %s", request.method, path, request.client.host if request.client else "unknown")
            return JSONResponse(status_code=403, content={"detail": "Invalid API key."})

        return await call_next(request)
