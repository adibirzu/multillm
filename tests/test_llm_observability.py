# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Tests for cross-LLM observability summaries."""

from datetime import date

from multillm.llm_observability import build_llm_observability_summary


def test_build_summary_includes_direct_limits_and_statuses():
    summary = build_llm_observability_summary(
        hours=168,
        gateway_stats={
            "totals": {
                "total_input": 100,
                "total_output": 50,
                "total_cost": 1.25,
                "total_requests": 2,
            },
            "session_count": 1,
            "by_model": [],
        },
        claude_stats={
            "available": True,
            "totalSessions": 3,
            "totalMessages": 18,
            "dailyModelTokens": [
                {
                    "date": "2026-04-06",
                    "tokensByModel": {
                        "claude-sonnet-4-6": 6_000,
                        "claude-opus-4-6": 1_000,
                    },
                },
                {
                    "date": "2026-03-15",
                    "tokensByModel": {"claude-sonnet-4-6": 999_999},
                },
            ],
        },
        codex_stats={
            "available": True,
            "totalSessions": 2,
            "byModel": {
                "gpt-5.4": {
                    "tokens": 1_200,
                    "sessions": 2,
                    "externalTokens": 1_200,
                    "externalSessions": 2,
                    "providers": ["openai"],
                },
            },
            "byProvider": {
                "openai": {
                    "tokens": 1_200,
                    "sessions": 2,
                    "actualCostUSD": 3.5,
                    "listPriceUSD": 3.5,
                },
            },
        },
        gemini_stats={
            "available": True,
            "totalSessions": 5,
            "totalTokens": 9_000,
            "model": "gemini-2.5-pro",
            "byModel": {
                "gemini-2.5-pro": {
                    "sessions": 4,
                    "totalTokens": 8_000,
                },
                "gemini-2.5-flash": {
                    "sessions": 1,
                    "totalTokens": 1_000,
                },
            },
        },
        settings={
            "usage_limits": {
                "claude_opus": 5_000,
                "claude_sonnet": 10_000,
                "gemini_cli": 20_000,
                "codex_cli_external": 5_000,
            },
        },
        today=date(2026, 4, 6),
    )

    statuses = summary["statusBySource"]
    assert statuses["claude_code"]["status"] == "active"
    assert statuses["codex_cli"]["status"] == "external_usage"
    assert statuses["gemini_cli"]["status"] == "active"

    items = {item["id"]: item for item in summary["limits"]["items"]}
    assert items["claude_sonnet"]["usedTokens"] == 6_000
    assert items["claude_sonnet"]["limitTokens"] == 10_000
    assert items["claude_sonnet"]["remainingTokens"] == 4_000
    assert items["claude_opus"]["usedTokens"] == 1_000
    assert items["codex_cli_external"]["usedTokens"] == 1_200
    assert items["codex_cli_external"]["providers"] == ["openai"]
    assert items["codex_cli_external"]["remainingTokens"] == 3_800
    assert items["gemini_cli"]["usedTokens"] == 9_000
    assert items["gemini_cli"]["remainingTokens"] == 11_000

    model_items = {item["model"]: item for item in summary["limits"]["modelItems"]}
    assert model_items["claude-sonnet-4-6"]["remainingTokens"] == 4_000
    assert model_items["claude-sonnet-4-6"]["scope"] == "family"
    assert model_items["gpt-5.4"]["remainingTokens"] == 3_800
    assert model_items["gpt-5.4"]["usedTokens"] == 1_200
    assert model_items["gpt-5.4"]["scope"] == "shared_provider"
    assert model_items["gemini-2.5-pro"]["remainingTokens"] == 11_000
    assert model_items["gemini-2.5-pro"]["usedTokens"] == 8_000
    assert model_items["gemini-2.5-pro"]["scope"] == "shared_provider"
