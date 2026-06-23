# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Tests for unified usage report shaping."""

from multillm.usage_reports import build_usage_report


def _bundle() -> dict:
    return {
        "stats": {
            "daily": [
                {
                    "day": "2026-06-01",
                    "requests": 2,
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "cost_usd": 0.1,
                },
                {
                    "day": "2026-06-08",
                    "requests": 1,
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "cost_usd": 0.01,
                },
            ]
        },
        "sessions": [
            {
                "id": "gw-1",
                "project": "multillm",
                "started_at": 1_780_300_000,
                "total_requests": 2,
                "total_input_tokens": 100,
                "total_output_tokens": 50,
                "total_cost_usd": 0.1,
                "models_used": ["openai/gpt-4o"],
            }
        ],
        "claudeStats": {
            "dailyActivity": [
                {"date": "2026-06-01", "messageCount": 3, "sessionCount": 1}
            ],
            "dailyModelTokens": [
                {
                    "date": "2026-06-01",
                    "tokensByModel": {"claude-sonnet-4-6": 500},
                }
            ],
            "sessionHistory": [
                {
                    "sessionId": "claude-1",
                    "project": "multillm",
                    "timestamp": "2026-06-01T01:10:00+00:00",
                    "messageCount": 3,
                    "inputTokens": 300,
                    "outputTokens": 200,
                    "estimatedCostUSD": 0.02,
                    "models_used": ["claude-sonnet-4-6"],
                }
            ],
        },
        "codexStats": {
            "daily": [
                {
                    "date": "2026-06-01",
                    "tokens": 300,
                    "inputTokens": 200,
                    "outputTokens": 100,
                    "cachedTokens": 25,
                    "sessions": 1,
                    "actualCostUSD": 0.2,
                    "listPriceUSD": 0.3,
                    "models": ["gpt-5.4"],
                }
            ],
            "sessions": [
                {
                    "sessionId": "codex-1",
                    "project": "multillm",
                    "createdAt": "2026-06-01T02:00:00",
                    "tokensUsed": 300,
                    "actualCostUSD": 0.2,
                    "model": "gpt-5.4",
                }
            ],
        },
        "geminiStats": {
            "daily": [
                {
                    "date": "2026-06-08",
                    "totalTokens": 120,
                    "inputTokens": 80,
                    "outputTokens": 40,
                    "cachedTokens": 10,
                    "sessions": 1,
                    "costUSD": 0.03,
                }
            ],
            "sessions": [],
        },
        "unified": {"hours": 720, "project": "multillm"},
    }


def test_daily_report_merges_sources_by_day():
    report = build_usage_report(_bundle(), kind="daily")

    assert report["kind"] == "daily"
    assert len(report["rows"]) == 2
    june_1 = report["rows"][0]
    assert june_1["period"] == "2026-06-01"
    assert june_1["tokens"] == 950
    assert june_1["requests"] == 2
    assert june_1["sessions"] == 2
    assert june_1["messages"] == 3
    assert june_1["actualCostUSD"] == 0.3
    assert june_1["sources"] == ["claude_code", "codex_cli", "gateway"]
    assert "codex_cli" in report["bySource"]


def test_weekly_report_groups_iso_weeks():
    report = build_usage_report(_bundle(), kind="weekly")

    periods = {row["period"]: row for row in report["rows"]}
    assert set(periods) == {"2026-W23", "2026-W24"}
    assert periods["2026-W23"]["tokens"] == 950
    assert periods["2026-W24"]["tokens"] == 135


def test_session_report_normalizes_gateway_and_direct_sessions():
    report = build_usage_report(_bundle(), kind="session")

    rows = report["rows"]
    assert {row["source"] for row in rows} == {"gateway", "claude_code", "codex_cli"}
    codex = next(row for row in rows if row["source"] == "codex_cli")
    assert codex["tokens"] == 300
    assert codex["models"] == ["gpt-5.4"]


def test_blocks_report_groups_claude_sessions_into_five_hour_windows():
    report = build_usage_report(_bundle(), kind="blocks")

    assert report["kind"] == "blocks"
    assert report["rows"][0]["startsAt"] == "2026-06-01T00:00:00+00:00"
    assert report["rows"][0]["tokens"] == 500
    assert report["rows"][0]["messages"] == 3
