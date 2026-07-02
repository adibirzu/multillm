# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Tests for CLI-agent detection + discovery (claude / codex / gemini / agy)."""

from __future__ import annotations

import multillm.cli_discovery as cd


# ── registry ────────────────────────────────────────────────────────────────


def test_registry_covers_the_four_cli_tools():
    backends = {a.backend for a in cd.CLI_AGENTS}
    assert backends == {"claude_cli", "codex_cli", "gemini_cli", "antigravity"}


def test_registry_binaries_match_expected_executables():
    by_backend = {a.backend: a.binary for a in cd.CLI_AGENTS}
    assert by_backend["claude_cli"] == "claude"
    assert by_backend["codex_cli"] == "codex"
    assert by_backend["gemini_cli"] == "gemini"
    assert by_backend["antigravity"] == "agy"


# ── detection ───────────────────────────────────────────────────────────────


def test_detect_returns_path_when_binary_resolves(monkeypatch):
    monkeypatch.setattr(
        cd, "resolve_cli_binary", lambda *a, **k: "/usr/local/bin/codex"
    )
    agent = next(a for a in cd.CLI_AGENTS if a.backend == "codex_cli")
    det = cd.detect_cli_agent(agent)
    assert det == {"installed": True, "path": "/usr/local/bin/codex"}


def test_detect_reports_not_installed_when_binary_absent(monkeypatch):
    monkeypatch.setattr(cd, "resolve_cli_binary", lambda *a, **k: None)
    agent = next(a for a in cd.CLI_AGENTS if a.backend == "claude_cli")
    det = cd.detect_cli_agent(agent)
    assert det == {"installed": False, "path": None}


# ── discovery summary shape ─────────────────────────────────────────────────


def _routes():
    return {
        "claude-cli/fable": {
            "backend": "claude_cli",
            "model": "claude:claude-fable-5",
        },
        "claude-cli/sonnet": {"backend": "claude_cli", "model": "claude:sonnet"},
        "claude-cli/opus": {"backend": "claude_cli", "model": "claude:opus"},
        "codex/gpt-5-5": {"backend": "codex_cli", "model": "codex:gpt-5-5"},
        "ollama/llama3": {"backend": "ollama", "model": "llama3"},  # ignored
    }


def test_discovery_lists_routes_for_installed_backend(monkeypatch):
    # all four installed
    monkeypatch.setattr(cd, "resolve_cli_binary", lambda binary, **k: f"/bin/{binary}")
    summary = cd.discover_cli_agents(_routes())

    claude = summary["claude_cli"]
    assert claude["installed"] is True
    assert claude["available"] is True
    assert claude["kind"] == "cli_agent"
    assert claude["model_count"] == 3
    ids = sorted(m["id"] for m in claude["models"])
    assert ids == ["claude-cli/fable", "claude-cli/opus", "claude-cli/sonnet"]
    # only this backend's routes leak in (ollama excluded)
    assert all(m["model"].startswith("claude:") for m in claude["models"])


def test_discovery_marks_uninstalled_backend(monkeypatch):
    monkeypatch.setattr(cd, "resolve_cli_binary", lambda *a, **k: None)
    summary = cd.discover_cli_agents(_routes())
    agy = summary["antigravity"]
    assert agy["installed"] is False
    assert agy["available"] is False
    assert agy["status"] == "not_installed"
    assert agy["note"]  # install hint present
    assert agy["model_count"] == 0


def test_installed_backend_without_routes_is_detected_not_available(monkeypatch):
    monkeypatch.setattr(cd, "resolve_cli_binary", lambda binary, **k: f"/bin/{binary}")
    # gemini_cli has no routes in this map
    summary = cd.discover_cli_agents(
        {"codex/x": {"backend": "codex_cli", "model": "c"}}
    )
    gem = summary["gemini_cli"]
    assert gem["installed"] is True
    assert gem["available"] is False
    assert gem["status"] == "detected"


def test_models_carry_cli_catalog_source(monkeypatch):
    monkeypatch.setattr(cd, "resolve_cli_binary", lambda binary, **k: f"/bin/{binary}")
    summary = cd.discover_cli_agents(_routes())
    for m in summary["codex_cli"]["models"]:
        assert m["catalog_source"] == "cli"


def test_fusion_capability_reports_ready_when_model_routes_are_available():
    capability = cd.fusion_capability(
        {
            "ollama": {
                "available": True,
                "models": [{"id": "ollama/llama3"}],
            },
            "codex_cli": {
                "available": True,
                "models": [{"id": "codex/gpt-5-5"}],
            },
            "gemini": {"available": False, "models": []},
        }
    )

    assert capability["available"] is True
    assert capability["status"] == "available"
    assert capability["eligible_model_count"] == 2
    assert [model["id"] for model in capability["models"]] == [
        "fusion/economy",
        "fusion/balanced",
        "fusion/quality",
        "fusion/critical",
    ]


def test_fusion_capability_reports_not_ready_without_available_models():
    capability = cd.fusion_capability(
        {"ollama": {"available": False, "models": [{"id": "ollama/llama3"}]}}
    )

    assert capability["available"] is False
    assert capability["status"] == "not_ready"
    assert capability["eligible_model_count"] == 0


def test_moa_capability_uses_canonical_names_and_reports_eligible_routes():
    capability = cd.moa_capability(
        {
            "codex_cli": {
                "available": True,
                "models": [{"id": "codex/gpt-5-5"}],
            },
            "claude_cli": {
                "available": True,
                "models": [{"id": "claude-cli/sonnet"}],
            },
        }
    )

    assert capability["label"] == "Mixture of Agents"
    assert capability["available"] is True
    assert capability["eligible_model_count"] == 2
    assert "claude-cli/sonnet" in capability["default_proposer_models"]
    assert capability["default_aggregator_model"] == "claude-cli/opus"
    assert [model["id"] for model in capability["models"]] == [
        "moa/economy",
        "moa/balanced",
        "moa/quality",
        "moa/critical",
    ]
