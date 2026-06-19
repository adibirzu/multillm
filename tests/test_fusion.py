# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Tests for the thought-level fusion pipeline."""

import asyncio

from multillm import fusion


def _result(alias, text, cost=0.0, error=None):
    return {
        "alias": alias,
        "backend": alias.split("/")[0],
        "text": text,
        "inputTokens": 5,
        "outputTokens": 10,
        "actualCostUSD": cost,
        "latencyMs": 1.0,
        "error": error,
    }


def _make_query_fn(table):
    """query_fn that returns table[alias] (or a default), recording calls."""
    calls = []

    async def query_fn(alias, prompt, max_tokens, temperature):
        calls.append((alias, prompt))
        if alias in table:
            r = dict(table[alias])
            r["alias"] = alias
            return r
        return _result(alias, f"answer from {alias}")

    query_fn.calls = calls
    return query_fn


def test_sanitize_panel_drops_recursive_aliases():
    panel, judge = fusion.sanitize_panel(
        ["fusion", "openai/gpt-4o", "auto", "fusion/x"], "openai/gpt-4o"
    )
    assert panel == ["openai/gpt-4o"]
    assert judge == "openai/gpt-4o"


def test_sanitize_clears_recursive_judge():
    _, judge = fusion.sanitize_panel(["a/b"], "fusion")
    assert judge == ""


def test_split_judge_output_extracts_final_answer():
    text = f"Consensus: x\nContradictions: y\n{fusion.FINAL_ANSWER_MARKER}\nThe real answer."
    analysis, answer = fusion.split_judge_output(text)
    assert "Consensus" in analysis
    assert answer == "The real answer."


def test_split_judge_output_falls_back_without_marker():
    analysis, answer = fusion.split_judge_output("just an answer, no marker")
    assert answer == "just an answer, no marker"


def test_build_judge_prompt_includes_all_panel_responses():
    p = fusion.build_judge_prompt(
        "Q?", [_result("a/m", "first"), _result("b/m", "second")]
    )
    assert "first" in p and "second" in p
    assert fusion.FINAL_ANSWER_MARKER in p
    assert "Blind spots" in p


def test_fusion_fuses_multiple_panel_responses():
    table = {
        "a/m": _result("a/m", "answer A", cost=0.01),
        "b/m": _result("b/m", "answer B", cost=0.02),
        "judge/m": _result(
            "judge/m",
            f"Consensus: agree\n{fusion.FINAL_ANSWER_MARKER}\nFused answer.",
            cost=0.03,
        ),
    }
    qf = _make_query_fn(table)
    out = asyncio.run(
        fusion.run_fusion(
            prompt="Q?", panel=["a/m", "b/m"], judge="judge/m", query_fn=qf
        )
    )
    assert out["status"] == "fused"
    assert out["finalAnswer"] == "Fused answer."
    assert "Consensus" in out["analysis"]
    # cost = panel A + panel B + judge
    assert out["totals"]["costUSD"] == 0.06
    assert out["totals"]["panelSucceeded"] == 2
    # judge was called once, after the panel
    assert qf.calls[-1][0] == "judge/m"


def test_fusion_single_success_skips_judge():
    table = {
        "a/m": _result("a/m", "only good answer", cost=0.01),
        "b/m": _result("b/m", "", error="429: quota"),
    }
    qf = _make_query_fn(table)
    out = asyncio.run(
        fusion.run_fusion(
            prompt="Q?", panel=["a/m", "b/m"], judge="judge/m", query_fn=qf
        )
    )
    assert out["status"] == "single"
    assert out["finalAnswer"] == "only good answer"
    # judge must NOT have been called
    assert all(c[0] != "judge/m" for c in qf.calls)


def test_fusion_no_panel_success_degrades():
    table = {
        "a/m": _result("a/m", "", error="500"),
        "b/m": _result("b/m", "", error="timeout"),
    }
    qf = _make_query_fn(table)
    out = asyncio.run(
        fusion.run_fusion(
            prompt="Q?", panel=["a/m", "b/m"], judge="judge/m", query_fn=qf
        )
    )
    assert out["status"] == "no_panel"
    assert out["finalAnswer"] == ""


def test_fusion_judge_failure_falls_back_to_best_panel_answer():
    table = {
        "a/m": _result("a/m", "short", cost=0.01),
        "b/m": _result("b/m", "a much longer and more detailed answer", cost=0.02),
        "judge/m": _result("judge/m", "", error="judge exploded"),
    }
    qf = _make_query_fn(table)
    out = asyncio.run(
        fusion.run_fusion(
            prompt="Q?", panel=["a/m", "b/m"], judge="judge/m", query_fn=qf
        )
    )
    assert out["status"] == "judge_failed"
    assert out["finalAnswer"] == "a much longer and more detailed answer"


def test_fusion_drops_recursive_panel_members():
    qf = _make_query_fn({})
    out = asyncio.run(
        fusion.run_fusion(
            prompt="Q?", panel=["fusion", "auto"], judge="judge/m", query_fn=qf
        )
    )
    assert out["status"] == "no_panel"  # nothing left to query
    assert qf.calls == []


def test_split_strips_analysis_block_when_marker_missing():
    text = (
        "## Brief Structured Analysis\n"
        "- Consensus: both agree gRPC is faster\n"
        "- Contradictions: none\n"
        "- Blind spots: none\n"
        "gRPC is the better choice when low latency matters."
    )
    analysis, answer = fusion.split_judge_output(text)
    assert answer == "gRPC is the better choice when low latency matters."
    assert "Consensus" in analysis


def test_split_returns_whole_when_no_analysis_and_no_marker():
    analysis, answer = fusion.split_judge_output("just a plain answer")
    assert answer == "just a plain answer"


def test_judge_receives_token_floor():
    captured = {}

    async def query_fn(alias, prompt, max_tokens, temperature):
        captured.setdefault(alias, []).append(max_tokens)
        marker = fusion.FINAL_ANSWER_MARKER
        if alias == "judge/m":
            return _result(alias, f"Consensus: x\n{marker}\nFinal.")
        return _result(alias, f"answer from {alias}")

    asyncio.run(
        fusion.run_fusion(
            prompt="Q?",
            panel=["a/m", "b/m"],
            judge="judge/m",
            query_fn=query_fn,
            max_tokens=50,
        )
    )
    # A small request budget (50) is lifted to the generous floors for both the
    # panel and the judge, so fusion is never starved.
    assert captured["a/m"][0] >= fusion.PANEL_TOKEN_FLOOR
    assert captured["judge/m"][0] >= fusion.JUDGE_TOKEN_FLOOR
    assert fusion.JUDGE_TOKEN_FLOOR >= fusion.PANEL_TOKEN_FLOOR
