from __future__ import annotations

import math

import pytest

from multillm.evaluation.artifacts import ArtifactCipher
from multillm.evaluation.contracts import (
    EvaluationCase,
    EvaluationProfile,
    EvaluationRunRequest,
    PairwiseDecision,
)
from multillm.evaluation.metrics import token_amplification
from multillm.evaluation.runner import evaluate_release_gate, select_human_calibration
from multillm.evaluation.statistics import (
    bootstrap_win_rate,
    holm_bonferroni,
    one_sided_sign_test,
    pass_at_k,
    pass_power_k,
)


def test_run_request_is_immutable_and_validates_live_host_controls():
    request = EvaluationRunRequest(
        suite_id="finops-v1",
        profile=EvaluationProfile.CI,
        candidate_scope="core",
        execution_mode="fixture",
    )

    with pytest.raises(Exception):
        request.profile = EvaluationProfile.RELEASE

    with pytest.raises(ValueError, match="live_host"):
        EvaluationRunRequest(
            suite_id="finops-v1",
            profile=EvaluationProfile.CI,
            candidate_scope="core",
            execution_mode="live_host",
            live_authorized=False,
        )

    with pytest.raises(ValueError, match="preflight"):
        EvaluationRunRequest(
            suite_id="finops-v1",
            execution_mode="live_host",
            live_authorized=True,
        )

    with pytest.raises(ValueError, match="live candidate_scope"):
        EvaluationRunRequest(
            suite_id="finops-v1",
            candidate_scope="live",
        )


def test_case_contract_rejects_unbounded_or_empty_prompts():
    with pytest.raises(ValueError):
        EvaluationCase(id="empty", prompt="   ", category="general")

    with pytest.raises(ValueError):
        EvaluationCase(id="large", prompt="x" * 100_001, category="general")


def test_bootstrap_win_rate_is_tie_aware_and_deterministic():
    decisions = [
        PairwiseDecision.CANDIDATE,
        PairwiseDecision.CANDIDATE,
        PairwiseDecision.BASELINE,
        PairwiseDecision.TIE,
    ]

    first = bootstrap_win_rate(decisions, samples=2_000, seed=17)
    second = bootstrap_win_rate(decisions, samples=2_000, seed=17)

    assert first == second
    assert first.win_rate == pytest.approx(0.625)
    assert 0 <= first.lower_95 <= first.win_rate <= first.upper_95 <= 1
    assert first.wins == 2
    assert first.losses == 1
    assert first.ties == 1


def test_pass_metrics_distinguish_any_success_from_all_success():
    assert pass_at_k(successes=7, attempts=10, k=3) == pytest.approx(0.991666, abs=1e-6)
    assert pass_power_k(success_probability=0.7, k=3) == pytest.approx(0.343)

    assert pass_at_k(successes=0, attempts=10, k=3) == 0
    assert pass_at_k(successes=10, attempts=10, k=3) == 1


def test_holm_bonferroni_controls_multiple_pairwise_claims():
    adjusted = holm_bonferroni({"a": 0.01, "b": 0.03, "c": 0.20})

    assert adjusted["a"] == pytest.approx(0.03)
    assert adjusted["b"] == pytest.approx(0.06)
    assert adjusted["c"] == pytest.approx(0.20)


def test_one_sided_sign_test_quantifies_candidate_superiority():
    assert one_sided_sign_test(wins=8, losses=2) == pytest.approx(56 / 1024)
    assert one_sided_sign_test(wins=9, losses=1) == pytest.approx(11 / 1024)
    assert one_sided_sign_test(wins=0, losses=0) == 1.0


def test_release_human_calibration_uses_minimum_thirty_and_ten_percent_floor():
    comparisons = [{"id": f"cmp_{index:04d}"} for index in range(400)]

    small = select_human_calibration(comparisons[:35], fraction=0.1, seed=17)
    large = select_human_calibration(comparisons, fraction=0.1, seed=17)

    assert len(small) == 30
    assert len(large) == 40
    assert small == select_human_calibration(comparisons[:35], fraction=0.1, seed=17)
    assert select_human_calibration(comparisons, fraction=0, seed=17) == frozenset()


def test_release_gate_waits_for_human_calibration_before_claiming_superiority():
    pairwise = [{"lower95": 0.8, "adjustedPValue": 0.01}]

    assert evaluate_release_gate("ci", pairwise, pending_reviews=False) == "not_evaluated"
    assert (
        evaluate_release_gate("release", pairwise, pending_reviews=True)
        == "pending_human_review"
    )
    assert evaluate_release_gate("release", pairwise, pending_reviews=False) == "pass"


def test_token_amplification_reports_context_and_compression_without_division_errors():
    metrics = token_amplification(
        prompt_tokens=100,
        proposer_input_tokens=300,
        proposer_output_tokens=600,
        aggregator_input_tokens=700,
        final_output_tokens=140,
        total_moa_tokens=1_740,
        baseline_total_tokens=240,
    )

    assert metrics.total_amplification == pytest.approx(7.25)
    assert metrics.aggregator_context_expansion == pytest.approx(7.0)
    assert metrics.synthesis_compression == pytest.approx(140 / 600)
    assert math.isfinite(metrics.total_amplification)

    empty = token_amplification(
        prompt_tokens=0,
        proposer_input_tokens=0,
        proposer_output_tokens=0,
        aggregator_input_tokens=0,
        final_output_tokens=0,
        total_moa_tokens=0,
        baseline_total_tokens=0,
    )
    assert empty.total_amplification is None


def test_artifact_cipher_encrypts_with_authenticated_context():
    cipher = ArtifactCipher(bytes(range(32)))
    encrypted = cipher.encrypt(b"sensitive answer", associated_data=b"tenant-a/run-1")

    assert b"sensitive answer" not in encrypted
    assert cipher.decrypt(encrypted, associated_data=b"tenant-a/run-1") == b"sensitive answer"
    with pytest.raises(ValueError, match="decrypt"):
        cipher.decrypt(encrypted, associated_data=b"tenant-b/run-1")
