# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""``SetupRedirectMiddleware`` (Plan 01-07 Task 2).

When the first-run wizard is incomplete, every non-allowlisted request is
302-redirected to ``/setup``. The allowlist is intentionally minimal —
only ``/health`` (liveness probe for container orchestrators) and the
``/setup`` surface itself (the wizard's own HTML, static assets, and
form endpoints) are exempt.

This middleware MUST be the outermost layer (added last to the FastAPI
app) so that the gateway's existing ``AuthMiddleware`` does not block the
wizard with a 401 before the user has had a chance to configure auth.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from multillm.setup.state import is_complete

log = logging.getLogger("multillm.setup.middleware")

__all__ = ["ALLOWLIST", "ALLOWLIST_PREFIXES", "SetupRedirectMiddleware"]


#: Exact-match paths that bypass the redirect even when setup is incomplete.
ALLOWLIST: frozenset[str] = frozenset({"/health", "/setup", "/setup/"})

#: Path prefixes that bypass the redirect (wizard sub-routes + static assets).
ALLOWLIST_PREFIXES: tuple[str, ...] = ("/setup/",)


def _open_conn() -> sqlite3.Connection:
    """Open a short-lived sqlite3 connection to the active DB."""
    from multillm.migrations.runner import db_path

    return sqlite3.connect(db_path())


def _is_allowlisted(path: str) -> bool:
    if path in ALLOWLIST:
        return True
    return any(path.startswith(prefix) for prefix in ALLOWLIST_PREFIXES)


class SetupRedirectMiddleware(BaseHTTPMiddleware):
    """Redirect to ``/setup`` while ``system.setup_complete != '1'``."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path

        if _is_allowlisted(path):
            return await call_next(request)

        try:
            conn = _open_conn()
            try:
                complete = is_complete(conn)
            finally:
                conn.close()
        except sqlite3.Error as exc:
            # No DB yet (e.g. tests that didn't migrate) → fail-open so the
            # wizard is reachable rather than hard-blocking the gateway.
            log.warning("setup-redirect: DB error %s; passing through", exc)
            return await call_next(request)

        if complete:
            return await call_next(request)

        return RedirectResponse(url="/setup", status_code=302)
