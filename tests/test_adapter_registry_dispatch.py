# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Registry-dispatch tests (Plan 02a-02 Task 19).

Asserts that every declared backend resolves through the registry and that
route_request / route_streaming have ≤3 AST top-level statements per
ROADMAP success criterion #1.
"""

import ast
import inspect
import pathlib

import pytest

from multillm.adapters import get_adapter, reset_for_tests


_BACKEND_NAMES = [
    "anthropic",
    "azure_openai",
    "bedrock",
    "codex_cli",
    "deepseek",
    "fireworks",
    "gemini",
    "gemini_cli",
    "groq",
    "lmstudio",
    "mistral",
    "ollama",
    "openai",
    "openrouter",
    "together",
    "xai",
]


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_for_tests()
    yield


@pytest.mark.parametrize("backend", _BACKEND_NAMES)
def test_backend_resolves_through_registry(backend: str) -> None:
    adapter = get_adapter(backend)
    assert adapter is not None, f"registry did not resolve backend {backend!r}"
    assert callable(getattr(adapter, "send", None)), f"{backend}.send is not callable"
    assert callable(getattr(adapter, "stream", None)), (
        f"{backend}.stream is not callable"
    )
    sig = inspect.signature(adapter.send)
    params = list(sig.parameters)
    assert params == ["body", "model", "model_alias"], (
        f"{backend}.send signature mismatch: {params}"
    )


def test_route_functions_have_at_most_three_statements() -> None:
    """ROADMAP SC#1: route_request and route_streaming each ≤ 3 AST top-level statements."""
    src = pathlib.Path("multillm/gateway.py").read_text()
    tree = ast.parse(src)
    funcs = {
        n.name: n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name in ("route_request", "route_streaming")
    }
    assert set(funcs) == {"route_request", "route_streaming"}, f"missing: {set(funcs)}"
    for name, fn in funcs.items():
        body = fn.body
        # Strip a leading docstring expression if present
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body = body[1:]
        assert len(body) <= 3, (
            f"{name} has {len(body)} top-level statements (excluding docstring); "
            f"ROADMAP SC#1 requires ≤ 3"
        )


def test_gateway_has_no_if_elif_backend_dispatch_chain() -> None:
    """ROADMAP SC#3: no `elif backend == "..."` dispatch chain survives.

    Standalone `if backend == "..."` for non-dispatch concerns (auth status,
    discovery endpoints) is acceptable. Only the chain form (`elif backend ==`)
    is the regression we're guarding against.
    """
    src = pathlib.Path("multillm/gateway.py").read_text()
    occurrences = src.count('elif backend == "')
    assert occurrences == 0, (
        f'found {occurrences} `elif backend == "..."` chain usages in gateway.py; '
        "registry dispatch should leave zero"
    )
