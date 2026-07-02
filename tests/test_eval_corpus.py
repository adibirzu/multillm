# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path

from multillm.adaptive_orchestration import classify_task


CORPUS = (
    Path(__file__).resolve().parents[1]
    / "evals"
    / "adaptive_fusion"
    / "v1"
    / "corpus.jsonl"
)


def test_adaptive_eval_corpus_is_versioned_and_covers_required_journeys():
    cases = [json.loads(line) for line in CORPUS.read_text().splitlines() if line]
    categories = {case["category"] for case in cases}

    assert len(cases) >= 10
    assert len({case["id"] for case in cases}) == len(cases)
    assert {
        "factual_lookup",
        "research",
        "coding",
        "debugging",
        "architecture",
        "summarization",
        "extraction",
        "multimodal",
        "tool_use",
        "adversarial_routing",
    }.issubset(categories)


def test_adversarial_eval_cannot_override_server_policy_features():
    adversarial = next(
        json.loads(line)
        for line in CORPUS.read_text().splitlines()
        if "adversarial-001" in line
    )

    task = classify_task(adversarial["prompt"])

    assert task.prompt_injection_signals
    assert "max" not in task.required_capabilities
    assert "ultra" not in task.required_capabilities
