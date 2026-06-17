# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""On-demand startup of installed-but-not-running local LLM backends.

When a request needs a local model but the daemon (Ollama / LM Studio) is not
running, the gateway can start it, wait for readiness, and then route — so the
"use the LLM the user installed" promise holds even if the daemon is down.

Spawning is gated on:
- the backend being *installed* (its CLI is on PATH), and
- the backend URL pointing at localhost (never start a remote host), and
- the ``local_autostart`` setting (default on).

Design notes:
- ``_probe_backend`` and ``_spawn_backend`` are module-level so tests can patch
  them without touching real processes.
- A per-backend ``asyncio.Lock`` prevents concurrent requests from launching the
  same daemon twice (thundering herd on a cold start).
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .config import DATA_DIR, LMSTUDIO_URL, OLLAMA_URL

log = logging.getLogger("multillm.local_launch")

# backend -> launch spec
_LAUNCHERS: dict[str, dict] = {
    "ollama": {
        "binary": "ollama",
        "command": ["ollama", "serve"],
        "readiness_url": f"{OLLAMA_URL}/api/tags",
        "url": OLLAMA_URL,
    },
    "lmstudio": {
        "binary": "lms",
        "command": ["lms", "server", "start"],
        "readiness_url": f"{LMSTUDIO_URL}/v1/models",
        "url": LMSTUDIO_URL,
        # LM Studio installs its CLI here and it is often not on PATH (especially
        # under launchd), so detection must look beyond PATH.
        "extra_paths": [str(Path.home() / ".lmstudio" / "bin")],
    },
}

LOCAL_LAUNCHABLE_BACKENDS = tuple(_LAUNCHERS.keys())

_START_TIMEOUT_S = 20.0
_POLL_INTERVAL_S = 0.5

_locks: dict[str, asyncio.Lock] = {b: asyncio.Lock() for b in _LAUNCHERS}


def _is_localhost(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def backend_binary(backend: str) -> str | None:
    """Resolve the launcher binary for ``backend``, or None.

    Checks PATH first, then any backend-specific ``extra_paths`` (e.g. LM
    Studio's ``~/.lmstudio/bin``) so detection works even when the CLI is not on
    the gateway process's PATH.
    """
    spec = _LAUNCHERS.get(backend)
    if not spec:
        return None
    found = shutil.which(spec["binary"])
    if found:
        return found
    for directory in spec.get("extra_paths", []):
        candidate = Path(directory) / spec["binary"]
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def is_backend_installed(backend: str) -> bool:
    """True when the backend's launcher CLI is installed on PATH."""
    return backend_binary(backend) is not None


def installed_local_backends() -> list[str]:
    """Launchable local backends whose CLI is installed."""
    return [b for b in LOCAL_LAUNCHABLE_BACKENDS if is_backend_installed(b)]


async def _probe_backend(backend: str) -> bool:
    """Return True if the backend's readiness endpoint responds 200."""
    spec = _LAUNCHERS.get(backend)
    if not spec:
        return False
    try:
        async with httpx.AsyncClient(timeout=2) as client:
            r = await client.get(spec["readiness_url"])
            return r.status_code == 200
    except Exception:
        return False


def _spawn_backend(backend: str) -> None:
    """Start the daemon as a detached background process (no wait)."""
    spec = _LAUNCHERS[backend]
    # Use the resolved absolute path so the spawn works even when the CLI is not
    # on the gateway's PATH (falls back to the bare command name otherwise).
    resolved = backend_binary(backend)
    command = [resolved, *spec["command"][1:]] if resolved else list(spec["command"])
    log_path = DATA_DIR / f"{backend}-autostart.log"
    with open(log_path, "ab") as logf:
        subprocess.Popen(  # noqa: S603 — fixed command, no user input
            command,
            stdout=logf,
            stderr=logf,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    log.info("Started local backend '%s' via %s", backend, " ".join(command))


async def ensure_local_backend(backend: str, *, timeout: float = _START_TIMEOUT_S) -> bool:
    """Ensure ``backend`` is reachable, starting it if installed but down.

    Returns True if the backend is reachable (already, or after a successful
    start within ``timeout``); False if it is not installed, not local, or did
    not come up in time.
    """
    if backend not in _LAUNCHERS:
        return False
    if await _probe_backend(backend):
        return True
    if not _is_localhost(_LAUNCHERS[backend]["url"]):
        log.debug("Not autostarting non-local backend '%s'", backend)
        return False
    if not is_backend_installed(backend):
        log.debug("Backend '%s' not installed; cannot autostart", backend)
        return False

    lock = _locks[backend]
    async with lock:
        # Another request may have started it while we waited for the lock.
        if await _probe_backend(backend):
            return True
        try:
            _spawn_backend(backend)
        except Exception as exc:  # pragma: no cover - spawn failure is rare
            log.warning("Failed to start local backend '%s': %s", backend, exc)
            return False

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(_POLL_INTERVAL_S)
            if await _probe_backend(backend):
                log.info("Local backend '%s' is ready", backend)
                return True
    log.warning("Local backend '%s' did not become ready within %.0fs", backend, timeout)
    return False


async def ensure_any_local_backend(*, timeout: float = _START_TIMEOUT_S) -> str | None:
    """Ensure at least one installed local backend is reachable.

    Tries each installed launchable backend in order and returns the name of the
    first one that is reachable (already-up or freshly started), or None.
    """
    for backend in installed_local_backends():
        if await ensure_local_backend(backend, timeout=timeout):
            return backend
    return None
