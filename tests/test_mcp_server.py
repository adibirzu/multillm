# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Tests for MCP tool input contracts."""

import asyncio

import pytest
from pydantic import ValidationError

from multillm import mcp_server
from multillm.mcp_server import FusionInput, MoAInput, UsageInput


def test_usage_input_accepts_multi_year_windows():
    params = UsageInput(hours=17520)

    assert params.hours == 17520


def test_usage_input_rejects_windows_beyond_dashboard_cap():
    with pytest.raises(ValidationError):
        UsageInput(hours=43801)


def test_fusion_input_rejects_unknown_preset():
    with pytest.raises(ValidationError):
        FusionInput(prompt="Review this", preset="unbounded")


def test_call_fusion_returns_answer_and_decision_details(monkeypatch):
    captured = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "finalAnswer": "Use a transaction boundary.",
                "confidence": 0.92,
                "decision": {"earlyExitReason": "verified"},
                "totals": {"actualCostUSD": 0.013, "modelsQueried": 2},
            }

    class _Client:
        async def post(self, url, json, headers):
            captured["url"] = url
            captured["body"] = json
            captured["headers"] = headers
            return _Response()

    monkeypatch.setattr(mcp_server, "_get_gateway_client", lambda: _Client())

    result = asyncio.run(
        mcp_server._call_fusion(
            FusionInput(
                prompt="Review the migration plan.",
                preset="quality",
                models=["codex/gpt-5-5", "ollama/llama3"],
            )
        )
    )

    assert captured["url"].endswith("/api/fusion")
    assert captured["body"] == {
        "prompt": "Review the migration plan.",
        "preset": "quality",
        "models": ["codex/gpt-5-5", "ollama/llama3"],
    }
    assert "Use a transaction boundary." in result
    assert "confidence 0.92" in result
    assert "2 stages" in result


def test_moa_input_requires_two_models_and_an_aggregator():
    with pytest.raises(ValidationError):
        MoAInput(prompt="Review", models=["codex/a"], aggregator="gemini/c")


def test_call_moa_uses_canonical_endpoint_and_returns_trace_summary(monkeypatch):
    captured = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "finalAnswer": "Layered answer.",
                "confidence": 0.88,
                "status": "completed",
                "stages": [{"stage": "proposer"}, {"stage": "aggregator"}],
                "totals": {"actualCostUSD": 0.02, "modelsQueried": 3},
            }

    class _Client:
        async def post(self, url, json, headers):
            captured.update(url=url, body=json, headers=headers)
            return _Response()

    monkeypatch.setattr(mcp_server, "_get_gateway_client", lambda: _Client())
    result = asyncio.run(
        mcp_server._call_moa(
            MoAInput(
                prompt="Review the design.",
                preset="quality",
                models=["codex/a", "claude/b"],
                aggregator="gemini/c",
            )
        )
    )

    assert captured["url"].endswith("/api/moa")
    assert captured["body"]["aggregator"] == "gemini/c"
    assert "Layered answer." in result
    assert "3 model calls" in result


def test_gateway_key_is_forwarded_without_being_rendered(monkeypatch):
    monkeypatch.setenv("MULTILLM_API_KEY", "test-key")
    assert mcp_server._gateway_headers() == {"X-API-Key": "test-key"}


def test_moa_input_defaults_include_claude_agents():
    params = MoAInput(prompt="Review the design.")

    assert "claude-cli/sonnet" in params.models
    assert params.aggregator == "claude-cli/opus"


def test_new_tool_inputs_reject_invalid_values():
    with pytest.raises(ValidationError):
        mcp_server.TraceInput(run_id="not-a-run")
    with pytest.raises(ValidationError):
        mcp_server.ScorecardsInput(min_samples=0)
