# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Tests for log-driven query routing."""

from multillm import router


def _stats():
    return {
        "fast-cheap/m": {
            "backend": "ollama",
            "avgLatencyMs": 800,
            "avgCostUSD": 0.0,
            "errorRate": 0.10,
            "requests": 50,
        },
        "slow-reliable/m": {
            "backend": "oci_genai",
            "avgLatencyMs": 6000,
            "avgCostUSD": 0.02,
            "errorRate": 0.00,
            "requests": 80,
        },
        "flaky/m": {
            "backend": "openai",
            "avgLatencyMs": 1500,
            "avgCostUSD": 0.05,
            "errorRate": 0.40,
            "requests": 20,
        },
    }


def _health(_backend):
    return 1.0


def _cost(_alias):
    return 0.0


def test_low_bias_prefers_fast_cheap():
    d = router.choose_model(
        prompt_complexity=0.0,
        pool=list(_stats()),
        stats=_stats(),
        health_fn=_health,
        cost_fn=_cost,
        bias=0.0,
    )
    assert d["model"] == "fast-cheap/m"


def test_high_bias_prefers_reliable():
    d = router.choose_model(
        prompt_complexity=0.0,
        pool=list(_stats()),
        stats=_stats(),
        health_fn=_health,
        cost_fn=_cost,
        bias=1.0,
    )
    assert d["model"] == "slow-reliable/m"


def test_complexity_nudges_toward_quality():
    # A complex prompt raises the effective bias and lifts the reliable model's
    # score (quality weighted higher), even if it doesn't always win outright.
    low = router.choose_model(
        prompt_complexity=0.0,
        pool=list(_stats()),
        stats=_stats(),
        health_fn=_health,
        cost_fn=_cost,
        bias=0.5,
    )
    high = router.choose_model(
        prompt_complexity=1.0,
        pool=list(_stats()),
        stats=_stats(),
        health_fn=_health,
        cost_fn=_cost,
        bias=0.5,
    )
    assert high["effectiveBias"] > low["effectiveBias"]

    def score_of(d, model):
        return next(c["score"] for c in d["candidates"] if c["model"] == model)

    assert score_of(high, "slow-reliable/m") > score_of(low, "slow-reliable/m")


def test_flaky_model_penalized_by_error_rate():
    d = router.choose_model(
        prompt_complexity=0.0,
        pool=list(_stats()),
        stats=_stats(),
        health_fn=_health,
        cost_fn=_cost,
        bias=1.0,
    )
    # flaky (40% errors) must never win on a quality-biased route
    assert d["model"] != "flaky/m"
    flaky = next(c for c in d["candidates"] if c["model"] == "flaky/m")
    assert flaky["reliability"] == 0.6


def test_unhealthy_backend_demoted():
    def health(backend):
        return 0.0 if backend == "oci_genai" else 1.0

    d = router.choose_model(
        prompt_complexity=0.0,
        pool=list(_stats()),
        stats=_stats(),
        health_fn=health,
        cost_fn=_cost,
        bias=1.0,
    )
    # the otherwise-reliable model loses its health component
    assert d["model"] != "slow-reliable/m"


def test_no_history_uses_neutral_defaults():
    d = router.choose_model(
        prompt_complexity=0.0,
        pool=["new/model"],
        stats={},
        health_fn=_health,
        cost_fn=_cost,
        bias=0.5,
        routes={"new/model": {"backend": "groq"}},
    )
    assert d["model"] == "new/model"
    assert d["candidates"][0]["reliability"] == 0.8  # default optimism
    assert d["candidates"][0]["backend"] == "groq"


def test_empty_pool_returns_none():
    d = router.choose_model(
        prompt_complexity=0.5, pool=[], stats={}, health_fn=_health, cost_fn=_cost
    )
    assert d["model"] is None
