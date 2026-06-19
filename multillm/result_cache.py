# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Result cache for council / fusion runs.

Council and fusion fan out to several models (and a judge), so an identical
repeat question is expensive. This is a small in-memory TTL cache keyed by a
hash of the request shape (prompt + participant models + judge + max_tokens), so
re-asking the same question returns instantly without re-querying the panel.

This is *exact-match* (deterministic key), which covers the common "ran it
twice" case at zero cost and zero external dependencies. Embedding-based
*semantic* matching (reworded repeats) is provided separately by the LangCache
layer in ``caching.py`` when ``LANGCACHE_ENABLED`` is set.
"""

from __future__ import annotations

import hashlib
import json
import time
from threading import RLock
from typing import Any, Optional

# Default time-to-live for a cached council/fusion result.
DEFAULT_TTL_SECONDS = 3600.0

_store: dict[str, tuple[float, Any]] = {}
_lock = RLock()
_hits = 0
_misses = 0


def make_key(
    *, kind: str, prompt: str, models: list[str], judge: Optional[str], max_tokens: int
) -> str:
    """Deterministic cache key for one council/fusion request shape."""
    payload = json.dumps(
        {
            "kind": kind,
            "prompt": prompt,
            "models": sorted(models or []),
            "judge": judge or "",
            "max_tokens": int(max_tokens),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get(key: str) -> Optional[Any]:
    """Return a cached result if present and unexpired, else None."""
    global _hits, _misses
    now = time.monotonic()
    with _lock:
        entry = _store.get(key)
        if entry and now < entry[0]:
            _hits += 1
            return entry[1]
        if entry:  # expired
            _store.pop(key, None)
        _misses += 1
    return None


def set(key: str, value: Any, ttl: float = DEFAULT_TTL_SECONDS) -> None:
    """Store a result under ``key`` with a TTL."""
    with _lock:
        _store[key] = (time.monotonic() + ttl, value)


def stats() -> dict:
    total = _hits + _misses
    return {
        "entries": len(_store),
        "hits": _hits,
        "misses": _misses,
        "hitRate": round(_hits / total, 3) if total else 0.0,
    }


def clear() -> None:
    global _hits, _misses
    with _lock:
        _store.clear()
        _hits = _misses = 0
