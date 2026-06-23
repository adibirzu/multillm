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
