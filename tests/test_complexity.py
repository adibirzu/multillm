# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Tests for prompt-complexity estimation."""

from multillm import complexity


def test_simple_factual_prompt_scores_low():
    out = complexity.estimate_complexity("What is the capital of France?")
    assert out["score"] < 0.3


def test_long_analytical_prompt_scores_high():
    prompt = (
        "Analyze the trade-offs between REST and gRPC for our internal LLM gateway. "
        "Compare latency, streaming support, tooling, and developer experience. "
        "Why does one outperform the other under high concurrency? "
        "Recommend an approach and design a migration plan with pros and cons."
    )
    out = complexity.estimate_complexity(prompt)
    assert out["score"] >= 0.6
    assert out["reasons"]


def test_code_prompt_adds_signal():
    plain = complexity.estimate_complexity("rename this variable")
    coded = complexity.estimate_complexity(
        "```python\ndef f(x):\n    return x\n```\nrefactor this"
    )
    assert coded["score"] > plain["score"]
    assert "contains code" in coded["reasons"]


def test_empty_prompt_is_zero():
    out = complexity.estimate_complexity("")
    assert out["score"] == 0.0
    assert out["wordCount"] == 0


def test_score_is_bounded():
    huge = "analyze compare design evaluate optimize debug " * 200 + "? ? ? ?"
    out = complexity.estimate_complexity(huge)
    assert 0.0 <= out["score"] <= 1.0
