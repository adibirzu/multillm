# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""HTTP routes for the first-run setup wizard (Plan 01-07 Task 2).

Routes are mounted by ``gateway.py`` with prefix ``/setup``:

- ``GET /setup``                       — render the wizard HTML
- ``POST /setup/admin``                — pane 1: create admin user
- ``POST /setup/backends``             — pane 2: persist optional API keys
- ``GET /setup/probe-local``           — pane 3: detect local backends
- ``POST /setup/observability``        — pane 4: persist observability prefs
- ``POST /setup/complete``             — flip ``setup_complete`` and clear state
- ``GET /setup/static/*``              — bundled CSS/JS (StaticFiles)

After completion, ``GET /setup`` returns ``410 Gone`` per D-13.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from multillm.setup.passwords import MIN_PASSWORD_LEN, hash_password
from multillm.setup.state import (
    advance,
    complete as state_complete,
    get_state,
    is_complete,
)

log = logging.getLogger("multillm.setup.routes")

__all__ = ["router"]

_PACKAGE_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"
_STATIC_DIR = _PACKAGE_DIR / "static"

_templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# RFC 5322 simplified: one ``@``, at least one ``.`` in the domain.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Local backend probe targets. Cloud backends are not probed by the
# wizard — they require keys the user is in the middle of providing.
_LOCAL_PROBES: dict[str, dict[str, Any]] = {
    "ollama": {
        "kind": "http",
        "default_url": "http://localhost:11434/api/tags",
        "models_key": "models",
        "model_name_field": "name",
    },
    "lmstudio": {
        "kind": "http",
        "default_url": "http://localhost:1234/v1/models",
        "models_key": "data",
        "model_name_field": "id",
    },
    "codex_cli": {
        "kind": "binary",
        "binary": "codex",
    },
    "gemini_cli": {
        "kind": "binary",
        "binary": "gemini",
    },
}


router = APIRouter(tags=["setup"])


# ── Helpers ──────────────────────────────────────────────────────────────────


def _open_conn() -> sqlite3.Connection:
    from multillm.migrations.runner import db_path

    return sqlite3.connect(db_path())


def _gone_if_complete() -> JSONResponse | None:
    """Return a 410 JSON envelope iff setup is already complete."""
    conn = _open_conn()
    try:
        if is_complete(conn):
            return JSONResponse(
                status_code=410,
                content={"error": "setup_already_complete"},
            )
    finally:
        conn.close()
    return None


async def _probe_http(url: str, models_key: str, name_field: str) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(url)
            if r.status_code >= 400:
                return {"reachable": False, "models": [], "error": f"HTTP {r.status_code}"}
            data = r.json()
            entries = data.get(models_key, []) if isinstance(data, dict) else []
            models = [e.get(name_field, "") for e in entries if isinstance(e, dict)]
            return {"reachable": True, "models": [m for m in models if m]}
    except Exception as exc:  # noqa: BLE001 — probe must never propagate
        return {"reachable": False, "models": [], "error": f"{type(exc).__name__}"}


async def _probe_binary(binary: str) -> dict[str, Any]:
    """Probe a CLI backend by asking the OS where the binary lives."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "which",
            binary,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=2.0)
        if proc.returncode == 0 and stdout.strip():
            return {"reachable": True, "models": [], "path": stdout.decode().strip()}
        return {"reachable": False, "models": []}
    except Exception as exc:  # noqa: BLE001
        return {"reachable": False, "models": [], "error": f"{type(exc).__name__}"}


async def _probe_one(name: str, spec: dict[str, Any]) -> dict[str, Any]:
    if spec["kind"] == "http":
        return await _probe_http(
            spec["default_url"], spec["models_key"], spec["model_name_field"]
        )
    if spec["kind"] == "binary":
        return await _probe_binary(spec["binary"])
    return {"reachable": False, "models": [], "error": "unknown_probe_kind"}


# ── Routes ───────────────────────────────────────────────────────────────────


@router.get("", include_in_schema=False, response_model=None)
@router.get("/", include_in_schema=False, response_model=None)
async def wizard(request: Request):
    gone = _gone_if_complete()
    if gone is not None:
        return JSONResponse(status_code=410, content={"error": "setup_already_complete"})

    conn = _open_conn()
    try:
        current_state = get_state(conn).value
    finally:
        conn.close()

    return _templates.TemplateResponse(
        request,
        "wizard.html",
        {"current_state": current_state, "min_password_len": MIN_PASSWORD_LEN},
    )


@router.post("/admin")
async def post_admin(payload: dict[str, Any]) -> JSONResponse:
    gone = _gone_if_complete()
    if gone is not None:
        return gone

    email = (payload.get("email") or "").strip()
    password = payload.get("password") or ""

    if not _EMAIL_RE.match(email):
        return JSONResponse(
            status_code=400, content={"error": "invalid_email"}
        )

    if len(password) < MIN_PASSWORD_LEN:
        return JSONResponse(
            status_code=400,
            content={
                "error": "password_too_short",
                "min_length": MIN_PASSWORD_LEN,
            },
        )

    password_hash = hash_password(password)

    conn = _open_conn()
    try:
        advance(
            conn,
            "admin",
            {"email": email, "password_hash": password_hash},
        )
    finally:
        conn.close()

    return JSONResponse(content={"state": "admin_created"})


@router.post("/backends")
async def post_backends(payload: dict[str, str]) -> JSONResponse:
    gone = _gone_if_complete()
    if gone is not None:
        return gone

    # D-15: every key is optional. Filter empty values BEFORE persisting
    # so we never store empty strings as if they were configured keys.
    filtered = {
        k: v for k, v in (payload or {}).items() if isinstance(v, str) and v.strip()
    }

    conn = _open_conn()
    try:
        advance(conn, "backends", filtered)
    finally:
        conn.close()

    return JSONResponse(content={"state": "backends_configured", "configured": list(filtered)})


@router.get("/probe-local")
async def probe_local() -> JSONResponse:
    gone = _gone_if_complete()
    if gone is not None:
        return gone

    names = list(_LOCAL_PROBES.keys())
    results = await asyncio.gather(
        *(_probe_one(n, _LOCAL_PROBES[n]) for n in names), return_exceptions=True
    )
    payload: dict[str, Any] = {}
    for name, result in zip(names, results, strict=True):
        if isinstance(result, BaseException):
            payload[name] = {
                "reachable": False,
                "models": [],
                "error": f"{type(result).__name__}",
            }
        else:
            payload[name] = result

    # Stash the probe result in setup_state so pane 3 can show it on a
    # refresh, but only if admin has already been created.
    conn = _open_conn()
    try:
        try:
            advance(conn, "local_probe", payload)
        except Exception:  # noqa: BLE001 — non-fatal
            log.debug("probe-local: could not persist (admin not yet created)")
    finally:
        conn.close()

    return JSONResponse(content=payload)


@router.post("/observability")
async def post_observability(payload: dict[str, Any]) -> JSONResponse:
    gone = _gone_if_complete()
    if gone is not None:
        return gone

    clean: dict[str, Any] = {
        "prometheus_enabled": bool(payload.get("prometheus_enabled", False)),
    }
    otel_endpoint = payload.get("otel_endpoint")
    if otel_endpoint:
        clean["otel_endpoint"] = str(otel_endpoint)

    conn = _open_conn()
    try:
        advance(conn, "observability", clean)
    finally:
        conn.close()

    return JSONResponse(content={"state": "observability_set"})


@router.post("/complete")
async def post_complete() -> JSONResponse:
    gone = _gone_if_complete()
    if gone is not None:
        return gone

    conn = _open_conn()
    try:
        state_complete(conn)
    finally:
        conn.close()

    return JSONResponse(content={"status": "complete", "redirect": "/dashboard"})


# ── Static files ─────────────────────────────────────────────────────────────

STATIC_DIR: Path = _STATIC_DIR
"""Absolute path to the wizard's bundled CSS/JS.

Mount on the parent app:

    app.mount("/setup/static", StaticFiles(directory=str(STATIC_DIR)), name="setup-static")

This is done in ``gateway.py`` and in the test harness's ``_build_app()``.
``APIRouter.mount`` is not honoured by ``app.include_router`` so the mount
MUST live on the FastAPI app itself.
"""


def mount_static(app) -> None:  # type: ignore[no-untyped-def]
    """Mount the wizard's static dir at ``/setup/static`` on ``app``."""
    app.mount(
        "/setup/static",
        StaticFiles(directory=str(STATIC_DIR)),
        name="setup-static",
    )
