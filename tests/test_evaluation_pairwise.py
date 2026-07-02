from __future__ import annotations

import asyncio
import json

from multillm.evaluation.artifacts import ArtifactCipher
from multillm.evaluation.contracts import EvaluationCase, EvaluationRunRequest
from multillm.evaluation.runner import EvaluationResponse, EvaluationRunner
from multillm.evaluation.store import EvaluationStore


def _store(tmp_path):
    store = EvaluationStore(
        tmp_path / "eval.db", artifact_cipher=ArtifactCipher(bytes(range(32)))
    )
    store.upsert_suite(
        "tenant-a",
        suite_id="pairwise-v1",
        name="Pairwise",
        version="1",
        source="owned",
        license_id="Apache-2.0",
        cases=(
            EvaluationCase(id="case-1", prompt="Explain", category="general"),
        ),
    )
    return store


def _judgment(choice: str) -> str:
    return json.dumps(
        {
            "decision": choice,
            "correctness": 0.9,
            "completeness": 0.9,
            "depth": 0.8,
            "coherence": 0.9,
            "confidence": 0.85,
            "rationale": "The selected response is better supported.",
            "safety_flags": [],
        }
    )


def test_dual_judge_position_swaps_create_auditable_pairwise_result(tmp_path):
    store = _store(tmp_path)

    async def execute(target, case, request):
        text = "MoA superior answer" if target == "moa/quality" else "Base incomplete answer"
        return EvaluationResponse(text=text, input_tokens=2, output_tokens=3, total_ms=5)

    async def judge(alias, prompt, request):
        response_a = prompt.split("Response A:\n", 1)[1].split("\n\nResponse B:", 1)[0]
        return _judgment("response_a" if "MoA superior" in response_a else "response_b")

    run_id = store.create_run(
        "tenant-a",
        EvaluationRunRequest(
            suite_id="pairwise-v1",
            candidate_scope="explicit",
            candidates=("base/model",),
            moa_variants=("moa/quality",),
            judge_pool=("judge/one", "judge/two"),
        ),
    )
    runner = EvaluationRunner(
        store=store,
        execute=execute,
        judge=judge,
        worker_id="worker-pairwise",
    )
    assert asyncio.run(runner.run_once()) == run_id

    comparisons = store.list_comparisons("tenant-a", run_id)
    assert len(comparisons) == 1
    assert comparisons[0]["decision"] == "candidate"
    assert comparisons[0]["needsHumanReview"] is False
    assert len(comparisons[0]["judgments"]) == 4
    assert {item["ordering"] for item in comparisons[0]["judgments"]} == {
        "normal",
        "swapped",
    }
    detail = store.get_run("tenant-a", run_id)
    assert detail["summary"]["pairwise"][0]["winRate"] == 1.0
    assert detail["summary"]["releaseGate"] == "not_evaluated"

    bundle = store.export_run("tenant-a", run_id)
    assert bundle["comparisons"][0]["decision"] == "candidate"
    assert len(bundle["comparisons"][0]["judgments"]) == 4


def test_pairwise_summary_applies_holm_correction_for_release_claims():
    comparisons = []
    for baseline in ("base/a", "base/b"):
        comparisons.extend(
            {
                "candidateTarget": "moa/quality",
                "baselineTarget": baseline,
                "decision": "candidate",
                "humanDecision": None,
            }
            for _ in range(40)
        )

    summary = EvaluationRunner._pairwise_summary(comparisons)

    assert all(item["lower95"] > 0.5 for item in summary)
    assert all(item["adjustedPValue"] <= 0.05 for item in summary)


def test_judge_disagreement_enters_blinded_human_review_queue(tmp_path):
    store = _store(tmp_path)

    async def execute(target, case, request):
        return EvaluationResponse(text=f"answer {target}")

    async def judge(alias, prompt, request):
        return _judgment("response_a" if alias == "judge/one" else "response_b")

    run_id = store.create_run(
        "tenant-a",
        EvaluationRunRequest(
            suite_id="pairwise-v1",
            candidate_scope="explicit",
            candidates=("base/model",),
            moa_variants=("moa/quality",),
            judge_pool=("judge/one", "judge/two"),
        ),
    )
    asyncio.run(
        EvaluationRunner(
            store=store,
            execute=execute,
            judge=judge,
            worker_id="worker-disagree",
        ).run_once()
    )

    queue = store.review_queue("tenant-a", run_id=run_id)
    assert len(queue) == 1
    comparison_id = queue[0]["id"]
    assert queue[0]["responseA"] != queue[0]["responseB"]
    assert "judge" not in json.dumps(queue[0]).lower()

    assert store.add_review(
        "tenant-a",
        comparison_id=comparison_id,
        reviewer_id="reviewer-1",
        decision="candidate",
        rationale="The candidate is more complete.",
    )
    assert store.review_queue("tenant-a", run_id=run_id) == []
    resolved = store.list_comparisons("tenant-a", run_id)[0]
    assert resolved["humanDecision"] == "candidate"
