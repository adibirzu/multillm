"""
Adapter registry — maps backend names to adapter instances.

Adapters are registered at import time and looked up during routing.
"""

from typing import Optional
from .base import BaseAdapter

_adapters: dict[str, BaseAdapter] = {}


def register_adapter(adapter: BaseAdapter):
    """Register an adapter instance by its name."""
    _adapters[adapter.name] = adapter


def get_adapter(backend: str) -> Optional[BaseAdapter]:
    """Look up an adapter by backend name."""
    return _adapters.get(backend)


def list_adapters() -> dict[str, bool]:
    """List all registered adapters and their configuration status."""
    return {name: adapter.is_configured() for name, adapter in _adapters.items()}
