# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""
Adapter registry — maps backend names to adapter instances.

Adapters are discovered lazily on first lookup via
`importlib.metadata.entry_points(group='multillm.backends')`. Built-in
adapters self-declare under `[project.entry-points."multillm.backends"]`
in pyproject.toml; third-party plugins can declare the same group to be
discovered identically (Phase 9 plugin SDK readiness).

`register_adapter()` is preserved as a backward-compat shim for tests
and the legacy `multillm/adapters/setup.py:register_all_adapters()`
codepath. Both routes converge on the same in-process cache.
"""

import inspect
import logging
from importlib.metadata import entry_points
from typing import Optional

from .base import BaseAdapter

logger = logging.getLogger(__name__)

_adapters: dict[str, BaseAdapter] = {}
_discovery_done: bool = False


def _discover_adapters() -> None:
    """Populate the cache by iterating the `multillm.backends` entry-point group."""
    global _discovery_done
    if _discovery_done:
        return
    _discovery_done = True
    try:
        eps = entry_points(group="multillm.backends")
    except Exception as exc:  # pragma: no cover - importlib edge cases
        logger.warning("entry_points lookup failed: %s", exc)
        return
    for ep in eps:
        try:
            obj = ep.load()
            if inspect.isclass(obj) and issubclass(obj, BaseAdapter):
                instance = obj()
            elif callable(obj):
                instance = obj()
            else:
                instance = obj
            if not isinstance(instance, BaseAdapter):
                logger.warning(
                    "entry point %s resolved to %r which is not a BaseAdapter; skipping",
                    ep.name,
                    instance,
                )
                continue
            # Entry-point name is authoritative — overrides adapter.name so that
            # factory-resolved entries (groq/deepseek/etc.) cache under the
            # correct backend key regardless of how the factory names itself.
            _adapters[ep.name] = instance
        except Exception as exc:
            logger.warning("failed to load entry point %s: %s", ep.name, exc)


def get_adapter(backend: str) -> Optional[BaseAdapter]:
    """Look up an adapter by backend name. Triggers discovery on first call."""
    if not _discovery_done:
        _discover_adapters()
    return _adapters.get(backend)


def list_adapters() -> dict[str, bool]:
    """Return a mapping of every discovered adapter name to its is_configured status."""
    if not _discovery_done:
        _discover_adapters()
    return {name: adapter.is_configured() for name, adapter in _adapters.items()}


def register_adapter(adapter: BaseAdapter) -> None:
    """Insert an adapter directly into the cache (backward-compat shim).

    Preserved for `multillm/adapters/setup.py:register_all_adapters()` and tests
    that construct synthetic adapters. Direct inserts win over entry-point-loaded
    entries with the same name.
    """
    _adapters[adapter.name] = adapter


def reset_for_tests() -> None:
    """Clear the cache and force re-discovery on next lookup (test-only helper)."""
    global _discovery_done
    _adapters.clear()
    _discovery_done = False
