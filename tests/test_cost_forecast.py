# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Tests for cost forecasting: burn-rate, projections, ETAs, pre-flight estimate."""

from multillm import cost_forecast


def test_estimate_tokens_nonempty():
    assert cost_forecast.estimate_tokens("hello world") > 0
    assert cost_forecast.estimate_tokens("") >= 0


def _unified():
    # Window = 168h (7 days). Per-source window aggregates live in 'sources';
    # quota items live under limits.items (matching the real payload shape).
    return {
        "sources": [
            {"source": "claude_code", "tokens": 16_800_000, "actualCostUSD": 84.0},
            {"source": "gemini_cli", "tokens": 3_360_000, "costUSD": 0.0},
        ],
        "limits": {
            "windowHours": 168,
            "items": [
                {
                    "label": "Claude Sonnet",
                    "source": "claude_code",
                    "remainingTokens": 6_000_000,
                    "limitTokens": 70_000_000,
                    "percentUsed": 1.4,
                },
                {
                    "label": "Gemini CLI",
                    "source": "gemini_cli",
                    "remainingTokens": 0,
                    "limitTokens": 14_000_000,
                    "percentUsed": 100.0,
                },
            ],
        },
    }


def test_burn_rate_per_source_uses_window_hours():
    fc = cost_forecast.build_cost_forecast(
        unified=_unified(),
        gw_recent_1h={"totals": {"total_input": 600, "total_output": 600}},
        gw_recent_24h={"totals": {"total_cost": 2.4}},
        window_hours=168,
    )
    # claude_code: 16,800,000 tokens over 168h = 100,000/h
    assert fc["burnRate"]["bySource"]["claude_code"]["tokensPerHour"] == 100_000.0
    # gateway: 1200 tokens last hour -> 20/min; 2.4 cost / 24h = 0.1/h
    assert fc["burnRate"]["gateway"]["tokensPerMin"] == 20.0
    assert fc["burnRate"]["gateway"]["costPerHourUSD"] == 0.1


def test_projection_extrapolates_window_daily_rate():
    fc = cost_forecast.build_cost_forecast(
        unified=_unified(),
        gw_recent_1h={"totals": {}},
        gw_recent_24h={"totals": {}},
        window_hours=168,
    )
    # $84 over 7 days -> $12/day -> $360 for a 30-day month
    assert fc["projected"]["perDayUSD"] == 12.0
    assert fc["projected"]["monthEndUSD"] == 360.0


def test_quota_eta_only_for_positive_remaining_and_rate():
    fc = cost_forecast.build_cost_forecast(
        unified=_unified(),
        gw_recent_1h={"totals": {}},
        gw_recent_24h={"totals": {}},
        window_hours=168,
    )
    etas = fc["quotaETAs"]
    # gemini has 0 remaining -> excluded; only claude_code remains
    assert len(etas) == 1
    eta = etas[0]
    assert eta["source"] == "claude_code"
    # 6,000,000 remaining / 100,000 per hour = 60h
    assert eta["etaHours"] == 60.0
    assert eta["exhaustsToday"] is False


def test_eta_exhausts_today_flag():
    unified = {
        "sources": [
            {"source": "claude_code", "tokens": 12_000_000, "actualCostUSD": 120.0}
        ],
        "limits": {
            "windowHours": 24,
            "items": [
                {
                    "label": "Claude Opus",
                    "source": "claude_code",
                    "remainingTokens": 500_000,
                    "limitTokens": 35_000_000,
                    "percentUsed": 97,
                }
            ],
        },
    }
    fc = cost_forecast.build_cost_forecast(
        unified=unified,
        gw_recent_1h={"totals": {}},
        gw_recent_24h={"totals": {}},
        window_hours=24,
    )
    # 12M over 24h = 500k/h; 500k remaining -> 1h -> exhausts within the day
    eta = fc["quotaETAs"][0]
    assert eta["etaHours"] == 1.0
    assert eta["exhaustsToday"] is True


_ROUTES = {
    "ollama/llama3": {"backend": "ollama", "model": "llama3"},
    "openai/gpt-4o": {"backend": "openai", "model": "gpt-4o"},
    "anthropic/sonnet": {"backend": "anthropic", "model": "claude-sonnet-4-6"},
}


def test_estimate_prompt_cost_sorts_cheapest_first_and_flags_free():
    out = cost_forecast.estimate_prompt_cost(
        prompt="Summarize the architecture of this gateway in detail.",
        routes=_ROUTES,
        expected_output_tokens=1000,
    )
    assert out["estimates"][0]["estimatedCostUSD"] == 0.0  # local ollama is free
    assert out["estimates"][0]["isFree"] is True
    assert out["freeOptionCount"] >= 1
    # cheapest paid should be a non-zero priced backend
    assert out["cheapestPaid"]["estimatedCostUSD"] > 0
    # estimates are non-decreasing
    costs = [e["estimatedCostUSD"] for e in out["estimates"]]
    assert costs == sorted(costs)


def test_estimate_prompt_cost_respects_candidate_filter():
    out = cost_forecast.estimate_prompt_cost(
        prompt="hello",
        routes=_ROUTES,
        candidates=["openai/gpt-4o"],
        expected_output_tokens=100,
    )
    assert len(out["estimates"]) == 1
    assert out["estimates"][0]["backend"] == "openai"


def test_estimate_dedupes_same_backend_model():
    routes = {
        "a": {"backend": "openai", "model": "gpt-4o"},
        "b": {"backend": "openai", "model": "gpt-4o"},
    }
    out = cost_forecast.estimate_prompt_cost(prompt="hi", routes=routes)
    assert len(out["estimates"]) == 1
