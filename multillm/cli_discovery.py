# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Detection + discovery for the local CLI-agent backends the gateway shells out to.

Unlike the HTTP backends in ``discovery.py`` (probed over the network), the CLI
agents — Claude Code (``claude``), Codex (``codex``), Gemini (``gemini``), and
Antigravity (``agy``) — are local executables. Discovery here is a cheap PATH
lookup via :func:`cli_tools.resolve_cli_binary`; **no subprocess is spawned**, so
it is safe to call on every ``/api/backends`` request.

The summary shape mirrors the HTTP backend summary in ``backends_api`` so the
``/llm-discover`` command and the dashboard can render CLI agents uniformly.
"""

from __future__ import annotations

from dataclasses import dataclass

from .cli_tools import resolve_cli_binary


@dataclass(frozen=True)
class CLIAgent:
    """A local CLI tool the gateway can invoke as a backend."""

    backend: str  # internal backend key (matches route["backend"])
    binary: str  # executable name resolved on PATH
    env_var: str  # env var that can pin an explicit binary path
    label: str  # human-friendly name
    install_hint: str  # shown when the tool is not installed


# Order is display order in discovery output.
CLI_AGENTS: tuple[CLIAgent, ...] = (
    CLIAgent(
        "claude_cli",
        "claude",
        "CLAUDE_CLI_PATH",
        "Claude Code CLI",
        "Install: npm i -g @anthropic-ai/claude-code",
    ),
    CLIAgent(
        "codex_cli",
        "codex",
        "CODEX_CLI_PATH",
        "Codex CLI",
        "Install: npm i -g @openai/codex",
    ),
    CLIAgent(
        "gemini_cli",
        "gemini",
        "GEMINI_CLI_PATH",
        "Gemini CLI",
        "Install: npm i -g @google/gemini-cli",
    ),
    CLIAgent(
        "antigravity",
        "agy",
        "ANTIGRAVITY_CLI_PATH",
        "Antigravity (agy)",
        "Install Antigravity, then run `agy install`",
    ),
)

_FUSION_PRESETS: tuple[tuple[str, str], ...] = (
    ("fusion/economy", "Fusion · economy"),
    ("fusion/balanced", "Fusion · balanced"),
    ("fusion/quality", "Fusion · quality"),
    ("fusion/critical", "Fusion · critical"),
)

_MOA_PRESETS: tuple[tuple[str, str], ...] = (
    ("moa/economy", "MoA · economy"),
    ("moa/balanced", "MoA · balanced"),
    ("moa/quality", "MoA · quality"),
    ("moa/critical", "MoA · critical"),
)


def detect_cli_agent(agent: CLIAgent) -> dict:
    """Resolve the agent's binary on PATH. Cheap — no process is spawned."""
    path = resolve_cli_binary(agent.binary, env_var=agent.env_var)
    return {"installed": path is not None, "path": path}


def _routes_for_backend(backend: str, routes: dict) -> list[dict]:
    """Configured route aliases for a backend, as discovery model entries."""
    models: list[dict] = []
    for alias, route in sorted((routes or {}).items()):
        if route.get("backend") == backend:
            model = route.get("model", "")
            models.append(
                {
                    "id": alias,
                    "name": model or alias,
                    "model": model,
                    "catalog_source": "cli",
                }
            )
    return models


def discover_cli_agents(routes: dict) -> dict[str, dict]:
    """Return a ``backends_api``-compatible summary for each CLI-agent backend.

    A backend is ``available`` only when the binary is installed *and* at least
    one route targets it. An installed tool with no routes is surfaced as
    ``detected`` (visible, but nothing to call yet); a missing tool as
    ``not_installed`` with an install hint.
    """
    summary: dict[str, dict] = {}
    for agent in CLI_AGENTS:
        det = detect_cli_agent(agent)
        installed = det["installed"]
        models = _routes_for_backend(agent.backend, routes)

        if not installed:
            status, note = "not_installed", agent.install_hint
        elif models:
            status, note = "available", f"Uses local {agent.label} login"
        else:
            status, note = "detected", f"{agent.label} installed; no routes configured"

        summary[agent.backend] = {
            "available": installed and bool(models),
            "installed": installed,
            "kind": "cli_agent",
            "binary": agent.binary,
            "binary_path": det["path"],
            "label": agent.label,
            "catalog_available": bool(models),
            "catalog_source": "cli" if models else None,
            "status": status,
            "requires_auth": False,
            # CLI tools carry their own login; we do not spawn them to verify it.
            "authenticated": None,
            "auth_mode": "local_cli",
            "note": note,
            "model_count": len(models),
            "models": models,
        }
    return summary


def fusion_capability(backends: dict[str, dict]) -> dict:
    """Describe the gateway's built-in adaptive Fusion capability.

    Fusion is an orchestrator rather than a provider, so it has no binary or
    credentials of its own. It becomes usable when at least one routed model
    is currently available. Keeping this in the backend shape lets the
    dashboard show it alongside the models it can actually use.
    """
    eligible_models = sorted(
        {
            str(model.get("id"))
            for backend in backends.values()
            if backend.get("available")
            for model in backend.get("models", [])
            if isinstance(model, dict) and model.get("id")
        }
    )
    ready = bool(eligible_models)
    return {
        "available": ready,
        "kind": "orchestrator",
        "label": "Adaptive Fusion",
        "catalog_available": True,
        "catalog_source": "orchestrator",
        "status": "available" if ready else "not_ready",
        "requires_auth": False,
        "authenticated": None,
        "eligible_model_count": len(eligible_models),
        "eligible_models": eligible_models,
        "note": (
            f"Ready to select from {len(eligible_models)} detected model route(s)"
            if ready
            else "Configure or start at least one model backend to enable Fusion"
        ),
        "model_count": len(_FUSION_PRESETS),
        "models": [
            {
                "id": alias,
                "name": label,
                "model": alias,
                "catalog_source": "orchestrator",
            }
            for alias, label in _FUSION_PRESETS
        ],
    }


def moa_capability(backends: dict[str, dict]) -> dict:
    """Describe canonical layered Mixture of Agents availability."""
    eligible_models = sorted(
        {
            str(model.get("id"))
            for backend in backends.values()
            if backend.get("available")
            for model in backend.get("models", [])
            if isinstance(model, dict) and model.get("id")
        }
    )
    ready = len(eligible_models) >= 2
    return {
        "available": ready,
        "kind": "orchestrator",
        "label": "Mixture of Agents",
        "catalog_available": True,
        "catalog_source": "orchestrator",
        "status": "available" if ready else "not_ready",
        "requires_auth": False,
        "authenticated": None,
        "eligible_model_count": len(eligible_models),
        "eligible_models": eligible_models,
        "note": (
            f"Ready to layer {len(eligible_models)} detected model route(s)"
            if ready
            else "At least two model routes are required to enable MoA"
        ),
        "model_count": len(_MOA_PRESETS),
        "models": [
            {
                "id": alias,
                "name": label,
                "model": alias,
                "catalog_source": "orchestrator",
            }
            for alias, label in _MOA_PRESETS
        ],
    }
