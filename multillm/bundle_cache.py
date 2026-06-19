# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Stale-while-revalidate cache for the expensive dashboard bundle.

The dashboard bundle aggregates fast gateway SQL stats with slow direct-history
scans (Claude Code / Codex / Gemini) that can take 20s+ cold. To keep the
dashboard instant we:

  - **persist** the last computed bundle to disk so a gateway restart starts warm
    (no 20s cold scan on first page load);
  - **serve immediately** from cache, even when the data is stale;
  - **revalidate in the background** (never block the request) when stale, running
    the blocking compute in a worker thread so the event loop stays responsive.

This is the classic stale-while-revalidate (SWR) pattern: the user always gets
an instant response, and freshness catches up out of band. Each response carries
``cacheState`` / ``ageSeconds`` / ``computedAt`` so the UI can show "updating…".
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .config import DATA_DIR

log = logging.getLogger("multillm.bundle_cache")

# Serve a cached bundle without revalidating while it is younger than this.
FRESH_TTL_SECONDS = 60.0
# Drop a persisted bundle entirely once it is older than this (a week) — beyond
# that it is more misleading than helpful and we prefer a cold recompute.
MAX_DISK_AGE_SECONDS = 7 * 24 * 3600

_CACHE_FILE = DATA_DIR / "dashboard-bundle-cache.json"

# A bundle compute function returns the raw payload dict for one parameter set.
ComputeFn = Callable[[], Awaitable[dict]]


@dataclass
class _Entry:
    data: dict
    wall_time: float  # epoch seconds when computed (for ageSeconds / persistence)


# key -> entry (in-memory authoritative copy)
_mem: dict[str, _Entry] = {}
# keys with a background refresh in flight (prevents duplicate concurrent scans)
_inflight: set[str] = set()
_lock = asyncio.Lock()


def make_key(
    *, hours: int, project: Optional[str], session_limit: int, direct_session_limit: int
) -> str:
    """Stable cache key for one bundle parameter set."""
    return f"h={hours}|p={project or ''}|s={session_limit}|d={direct_session_limit}"


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _with_meta(entry: _Entry, *, cache_state: str) -> dict:
    """Return a shallow copy of the payload with freshness metadata attached."""
    age = max(0.0, time.time() - entry.wall_time)
    data = dict(entry.data)
    perf = dict(data.get("performance") or {})
    perf.update(
        {
            "cacheState": cache_state,
            "ageSeconds": round(age, 1),
            "computedAt": _iso(entry.wall_time),
            "fresh": age < FRESH_TTL_SECONDS,
        }
    )
    data["performance"] = perf
    return data


def warm_load() -> int:
    """Load persisted bundles from disk into memory on startup.

    Returns the number of usable (non-expired) entries loaded. Failures are
    swallowed — a missing or corrupt cache file just means a cold first load.
    """
    if not _CACHE_FILE.exists():
        return 0
    try:
        raw = json.loads(_CACHE_FILE.read_text())
    except Exception as exc:  # corrupt file — start cold rather than crash
        log.warning("bundle cache: failed to read %s: %s", _CACHE_FILE, exc)
        return 0

    now = time.time()
    loaded = 0
    for key, blob in (raw.get("entries") or {}).items():
        try:
            wall_time = float(blob["wall_time"])
            if now - wall_time > MAX_DISK_AGE_SECONDS:
                continue
            _mem[key] = _Entry(data=blob["data"], wall_time=wall_time)
            loaded += 1
        except Exception:
            continue
    if loaded:
        log.info(
            "bundle cache: warm-loaded %d entr%s from disk",
            loaded,
            "y" if loaded == 1 else "ies",
        )
    return loaded


def _persist(snapshot: Optional[dict] = None) -> None:
    """Write entries to disk atomically.

    ``snapshot`` (a copy of ``_mem`` taken under the lock) lets the caller run
    this in a worker thread without holding the async lock during file I/O.
    """
    mem = snapshot if snapshot is not None else _mem
    try:
        payload = {
            "version": 1,
            "entries": {
                k: {"data": e.data, "wall_time": e.wall_time} for k, e in mem.items()
            },
        }
        tmp = _CACHE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload))
        tmp.replace(_CACHE_FILE)
    except Exception as exc:
        log.warning("bundle cache: failed to persist: %s", exc)


async def _compute_and_store(key: str, compute_fn: ComputeFn) -> _Entry:
    """Run the compute, store the result in memory + disk, and return the entry."""
    data = await compute_fn()
    entry = _Entry(data=data, wall_time=time.time())
    async with _lock:
        _mem[key] = entry
        snapshot = dict(_mem)  # copy under lock; persist outside it
    # Serialize + write off the event loop so we never block it holding state.
    await asyncio.to_thread(_persist, snapshot)
    return entry


async def _background_refresh(key: str, compute_fn: ComputeFn) -> None:
    try:
        await _compute_and_store(key, compute_fn)
        log.debug("bundle cache: background refresh complete for %s", key)
    except Exception as exc:
        log.warning("bundle cache: background refresh failed for %s: %s", key, exc)
    finally:
        _inflight.discard(key)


async def get_bundle(key: str, compute_fn: ComputeFn, *, force: bool = False) -> dict:
    """Return the dashboard bundle for ``key`` using stale-while-revalidate.

    - ``force=True`` always recomputes synchronously (the dashboard "Refresh"
      button) and waits for fresh data.
    - A fresh cached entry (< ``FRESH_TTL_SECONDS``) is returned immediately.
    - A stale cached entry is returned immediately and a background refresh is
      kicked off (deduplicated per key).
    - With no cached entry at all, we must compute synchronously (cold path) —
      this only happens on the very first request for a parameter set when no
      disk cache exists.
    """
    if force:
        entry = await _compute_and_store(key, compute_fn)
        return _with_meta(entry, cache_state="forced")

    entry = _mem.get(key)
    if entry is None:
        entry = await _compute_and_store(key, compute_fn)
        return _with_meta(entry, cache_state="cold")

    age = time.time() - entry.wall_time
    if age < FRESH_TTL_SECONDS:
        return _with_meta(entry, cache_state="fresh")

    # Stale: serve now, revalidate in the background (once per key).
    if key not in _inflight:
        _inflight.add(key)
        asyncio.create_task(_background_refresh(key, compute_fn))
    return _with_meta(entry, cache_state="stale-refreshing")


def cache_clear() -> None:
    """Drop all in-memory entries (used by tests)."""
    _mem.clear()
    _inflight.clear()
