from __future__ import annotations

import json

import pytest

from multillm.evaluation.contracts import (
    PairwiseDecision,
    PairwiseJudgment,
    ResponseChoice,
)
from multillm.evaluation.judging import (
    build_blind_judge_prompt,
    parse_judgment,
    resolve_position_swaps,
)


def _judgment(decision: ResponseChoice) -> PairwiseJudgment:
    return PairwiseJudgment(
        decision=decision,
        correctness=0.9,
        completeness=0.8,
        depth=0.8,
        coherence=0.9,
        confidence=0.8,
        rationale="Response follows the evidence.",
    )


def test_judge_prompt_blinds_model_identity_and_requires_json():
    prompt = build_blind_judge_prompt(
        user_prompt="Explain the anomaly",
        response_a="Compare the current period with its baseline.",
        response_b="Investigate the change and assign an owner.",
        criteria=("correctness", "completeness"),
    )

    assert "codex" not in prompt.lower()
    assert "claude" not in prompt.lower()
    assert "Response A" in prompt
    assert '"decision"' in prompt


def test_judgment_parser_accepts_fenced_json_and_rejects_free_text():
    payload = {
        "decision": "response_a",
        "correctness": 0.9,
        "completeness": 0.8,
        "depth": 0.7,
        "coherence": 0.9,
        "confidence": 0.8,
        "rationale": "More complete and accurate.",
        "safety_flags": [],
    }
    parsed = parse_judgment(f"```json\n{json.dumps(payload)}\n```")
    assert parsed.decision is ResponseChoice.RESPONSE_A

    with pytest.raises(ValueError, match="structured JSON"):
        parse_judgment("Response 1 Wins")


def test_position_swap_resolution_abstains_on_bias():
    consistent = resolve_position_swaps(
        normal=_judgment(ResponseChoice.RESPONSE_A),
        swapped=_judgment(ResponseChoice.RESPONSE_B),
    )
    assert consistent is PairwiseDecision.CANDIDATE

    inconsistent = resolve_position_swaps(
        normal=_judgment(ResponseChoice.RESPONSE_A),
        swapped=_judgment(ResponseChoice.RESPONSE_A),
    )
    assert inconsistent is PairwiseDecision.ABSTAIN
