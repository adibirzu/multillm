# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Tests for entry_points()-based adapter discovery (Plan 02a-01)."""

from multillm.adapters import BaseAdapter, get_adapter, register_adapter, reset_for_tests
from multillm.adapters.registry import _adapters, list_adapters


class _StubAdapter(BaseAdapter):
    name = "stub-test-adapter"

    async def send(self, body: dict, model: str, model_alias: str) -> dict:
        return {"stub": True}

    async def stream(self, body: dict, model: str, model_alias: str):
        return None


def setup_function(_func) -> None:
    reset_for_tests()


def test_discovery_finds_ollama() -> None:
    """ollama is declared in pyproject.toml [project.entry-points."multillm.backends"]."""
    reset_for_tests()
    adapter = get_adapter("ollama")
    assert adapter is not None, "expected ollama adapter to be discovered via entry_points"
    assert adapter.name == "ollama"


def test_register_adapter_shim() -> None:
    """register_adapter() must insert directly into the cache (backward-compat shim)."""
    stub = _StubAdapter()
    register_adapter(stub)
    assert _adapters.get(stub.name) is stub


def test_unknown_backend_returns_none() -> None:
    """get_adapter() returns None for unregistered backends."""
    assert get_adapter("nonexistent-backend-xyz") is None


def test_list_adapters_returns_dict() -> None:
    """list_adapters() returns a dict mapping name -> is_configured."""
    stub = _StubAdapter()
    register_adapter(stub)
    result = list_adapters()
    assert isinstance(result, dict)
    assert stub.name in result
    assert isinstance(result[stub.name], bool)
