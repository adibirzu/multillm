from __future__ import annotations

import asyncio
import json

import pytest

from multillm.moa import MoAConfig, build_layer_prompt, run_moa


def test_moa_config_is_immutable_and_rejects_recursive_or_duplicate_roles():
    config = MoAConfig(
        proposer_models=("codex/a", "claude/b"),
        refiner_layers=(("gemini/c",),),
        aggregator_model="codex/judge",
    )
    with pytest.raises(Exception):
        config.aggregator_model = "other"

    with pytest.raises(ValueError, match="recursive"):
        MoAConfig(
            proposer_models=("moa/quality", "claude/b"),
            aggregator_model="codex/a",
        )

    with pytest.raises(ValueError, match="at least two"):
        MoAConfig(proposer_models=("codex/a",), aggregator_model="codex/judge")


def test_layer_prompt_anonymizes_sources_and_enforces_context_budget():
    prompt = build_layer_prompt(
        user_prompt="Explain the anomaly",
        responses=(
            {"alias": "codex/secret", "text": "A" * 80},
            {"alias": "claude/secret", "text": "B" * 80},
        ),
        role="refiner",
        max_context_chars=100,
    )

    assert "codex/secret" not in prompt
    assert "claude/secret" not in prompt
    assert "Response 1" in prompt and "Response 2" in prompt
    response_section = prompt.split("ANONYMOUS RESPONSES:\n", 1)[1]
    assert len(response_section) <= 180  # labels plus the 100-character content budget


def test_layered_moa_runs_parallel_proposers_then_refiner_and_aggregator():
    calls: list[tuple[str, str]] = []

    async def query(alias, prompt, max_tokens, temperature):
        calls.append((alias, prompt))
        if alias in {"codex/a", "claude/b"}:
            return {
                "alias": alias,
                "text": f"draft from {alias}",
                "inputTokens": 10,
                "outputTokens": 5,
                "actualCostUSD": 0.01,
                "latencyMs": 20,
            }
        if alias == "gemini/c":
            assert "Response 1" in prompt and "Response 2" in prompt
            assert "codex/a" not in prompt and "claude/b" not in prompt
            return {"alias": alias, "text": "refined evidence", "inputTokens": 20, "outputTokens": 6}
        return {
            "alias": alias,
            "text": json.dumps(
                {
                    "analysis": {
                        "consensus": ["cost increased"],
                        "contradictions": [],
                        "blind_spots": ["owner missing"],
                    },
                    "final_answer": "Compare the baseline, validate usage, and assign an owner.",
                    "confidence": 0.87,
                }
            ),
            "inputTokens": 30,
            "outputTokens": 12,
        }

    result = asyncio.run(
        run_moa(
            prompt="Explain the anomaly",
            config=MoAConfig(
                proposer_models=("codex/a", "claude/b"),
                refiner_layers=(("gemini/c",),),
                aggregator_model="codex/judge",
                max_context_chars=4_000,
            ),
            query_fn=query,
        )
    )

    assert result["status"] == "completed"
    assert result["kind"] == "moa"
    assert result["finalAnswer"].startswith("Compare the baseline")
    assert result["confidence"] == pytest.approx(0.87)
    assert [stage["stage"] for stage in result["stages"]] == [
        "proposer",
        "refiner_1",
        "aggregator",
    ]
    assert result["totals"]["modelsQueried"] == 4
    assert result["totals"]["inputTokens"] == 70
    assert result["totals"]["criticalPathMs"] > 0
    assert len(calls) == 4


def test_aggregator_failure_uses_quality_score_not_longest_answer():
    async def query(alias, prompt, max_tokens, temperature):
        if alias == "codex/a":
            return {"alias": alias, "text": "short correct", "qualityScore": 0.9}
        if alias == "claude/b":
            return {"alias": alias, "text": "long " * 100, "qualityScore": 0.2}
        return {"alias": alias, "error": "aggregator failed", "text": ""}

    result = asyncio.run(
        run_moa(
            prompt="Question",
            config=MoAConfig(
                proposer_models=("codex/a", "claude/b"),
                aggregator_model="gemini/judge",
            ),
            query_fn=query,
        )
    )

    assert result["status"] == "degraded"
    assert result["finalAnswer"] == "short correct"
    assert result["degradedReason"] == "aggregator_failed"
