from __future__ import annotations

import asyncio

import pytest

from multillm.evaluation.artifacts import ArtifactCipher
from multillm.evaluation.contracts import EvaluationCase, EvaluationRunRequest
from multillm.evaluation.runner import (
    CORE_MODEL_ALIASES,
    EvaluationResponse,
    EvaluationRunner,
    deduplicate_targets,
    validate_live_targets,
)
from multillm.evaluation.store import EvaluationStore


def _store(tmp_path):
    store = EvaluationStore(
        tmp_path / "eval.db", artifact_cipher=ArtifactCipher(bytes(range(32)))
    )
    store.upsert_suite(
        "tenant-a",
        suite_id="finops-v1",
        name="FinOps",
        version="1",
        source="owned",
        license_id="Apache-2.0",
        cases=(
            EvaluationCase(
                id="case-1",
                prompt="Explain the anomaly",
                category="anomaly_detection",
                required_terms=("baseline",),
                forbidden_terms=("guaranteed savings",),
            ),
        ),
    )
    return store


def test_target_deduplication_preserves_distinct_execution_profiles():
    catalog = [
        {
            "alias": "codex/a",
            "provider": "codex",
            "providerModel": "gpt-x",
            "reasoning": "medium",
        },
        {
            "alias": "codex/a-copy",
            "provider": "codex",
            "providerModel": "gpt-x",
            "reasoning": "medium",
        },
        {
            "alias": "codex/a-high",
            "provider": "codex",
            "providerModel": "gpt-x",
            "reasoning": "high",
        },
        {
            "alias": "claude/b",
            "provider": "claude",
            "providerModel": "sonnet",
            "reasoning": "medium",
        },
    ]

    result = deduplicate_targets(catalog)

    assert [item["alias"] for item in result] == ["codex/a", "codex/a-high", "claude/b"]
    assert result[0]["equivalentAliases"] == ["codex/a", "codex/a-copy"]


def test_live_preflight_fails_closed_for_discovery_only_or_sandboxed_aliases():
    catalog = {
        "codex/gpt": {
            "available": True,
            "executionVerified": True,
            "executionMode": "live_host",
        },
        "claude/sonnet": {
            "available": True,
            "executionVerified": False,
            "executionMode": "live_host",
        },
        "gemini/pro": {
            "available": True,
            "executionVerified": True,
            "executionMode": "sandbox",
        },
    }

    assert validate_live_targets(("codex/gpt",), catalog) == ("codex/gpt",)
    with pytest.raises(ValueError, match="execution probe"):
        validate_live_targets(("claude/sonnet",), catalog)
    with pytest.raises(ValueError, match="live_host"):
        validate_live_targets(("gemini/pro",), catalog)


def test_core_set_covers_each_installed_cli_family():
    assert "claude-cli/sonnet" in CORE_MODEL_ALIASES
    assert "gemini-cli/flash" in CORE_MODEL_ALIASES
    assert "antigravity/pro" in CORE_MODEL_ALIASES
    assert any(alias.startswith("codex/") for alias in CORE_MODEL_ALIASES)


def test_runner_executes_same_prompt_and_persists_deterministic_metrics(tmp_path):
    store = _store(tmp_path)
    seen: list[tuple[str, str]] = []

    async def execute(target, case, request):
        seen.append((target, case.prompt))
        return EvaluationResponse(
            text="Compare the baseline and investigate the owner.",
            input_tokens=8,
            output_tokens=7,
            total_ms=120,
            ttft_ms=None,
            ttft_unavailable_reason="non_streaming_cli",
            actual_cost_usd=None,
            normalized_cost_usd=0.001,
            resolved_model=target,
        )

    run_id = store.create_run(
        "tenant-a",
        EvaluationRunRequest(
            suite_id="finops-v1",
            candidate_scope="explicit",
            candidates=("codex/gpt-5-5", "claude-cli/sonnet"),
            moa_variants=(),
        ),
    )
    runner = EvaluationRunner(store=store, execute=execute, worker_id="worker-test")

    completed = asyncio.run(runner.run_once())

    assert completed == run_id
    assert seen == [
        ("codex/gpt-5-5", "Explain the anomaly"),
        ("claude-cli/sonnet", "Explain the anomaly"),
    ]
    detail = store.get_run("tenant-a", run_id)
    assert detail["status"] == "completed"
    assert detail["summary"]["outputs"] == 2
    assert detail["summary"]["deterministicPassRate"] == 1.0
    assert {metric["metric"] for metric in detail["metrics"]} == {
        "forbidden_terms",
        "required_terms",
    }


def test_runner_reports_pass_at_k_and_pass_power_k_for_repeated_runs(tmp_path):
    store = _store(tmp_path)
    responses = iter(("baseline", "guaranteed savings", "baseline"))

    async def execute(_target, _case, _request):
        return EvaluationResponse(text=next(responses))

    run_id = store.create_run(
        "tenant-a",
        EvaluationRunRequest(
            suite_id="finops-v1",
            candidate_scope="explicit",
            candidates=("model/a",),
            moa_variants=(),
            repeats=3,
        ),
    )
    asyncio.run(
        EvaluationRunner(
            store=store, execute=execute, worker_id="worker-repeat"
        ).run_once()
    )

    reliability = store.get_run("tenant-a", run_id)["summary"]["reliability"][0]
    assert reliability["target"] == "model/a"
    assert reliability["attemptPassRate"] == pytest.approx(2 / 3)
    assert reliability["passAtK"] == 1.0
    assert reliability["passPowerK"] == pytest.approx((2 / 3) ** 3)
    metrics = store.get_run("tenant-a", run_id)["metrics"]
    assert len(metrics) == 6
    assert {metric["attempt"] for metric in metrics} == {1, 2, 3}


def test_runner_stops_at_the_next_boundary_after_cancellation(tmp_path):
    store = _store(tmp_path)
    run_id = store.create_run(
        "tenant-a",
        EvaluationRunRequest(
            suite_id="finops-v1",
            candidate_scope="explicit",
            candidates=("model/a", "model/b"),
            moa_variants=(),
            repeats=2,
        ),
    )
    calls = 0

    async def execute(_target, _case, _request):
        nonlocal calls
        calls += 1
        store.cancel_run("tenant-a", run_id)
        return EvaluationResponse(text="baseline")

    asyncio.run(
        EvaluationRunner(
            store=store, execute=execute, worker_id="worker-cancel"
        ).run_once()
    )

    assert calls == 1
    assert store.get_run("tenant-a", run_id)["status"] == "cancelled"


def test_gateway_executor_distinguishes_fixture_from_live_host(monkeypatch):
    from multillm import gateway

    case = EvaluationCase(
        id="fixture-case",
        prompt="Explain",
        category="general",
        required_terms=("baseline", "owner"),
    )
    fixture = asyncio.run(
        gateway._gateway_evaluation_execute(
            "codex/gpt-5-5",
            case,
            EvaluationRunRequest(suite_id="finops-v1", execution_mode="fixture"),
        )
    )
    assert fixture.text == "baseline owner"
    assert fixture.resolved_model == "fixture:codex/gpt-5-5"

    async def live(alias, prompt, max_tokens, temperature, controls=None):
        return {
            "alias": alias,
            "text": "live answer",
            "inputTokens": 5,
            "outputTokens": 3,
            "reasoningTokens": 2,
            "latencyMs": 90,
            "actualCostUSD": 0.02,
            "providerModel": "resolved-live-model",
            "error": None,
        }

    monkeypatch.setattr(gateway, "_council_query_one", live)
    gateway._EVALUATION_PREFLIGHTS["evalpf_testreceipt1234"] = {
        "targets": {"codex/gpt-5-5"},
        "expiresAt": float("inf"),
    }
    live_response = asyncio.run(
        gateway._gateway_evaluation_execute(
            "codex/gpt-5-5",
            case,
            EvaluationRunRequest(
                suite_id="finops-v1",
                execution_mode="live_host",
                live_authorized=True,
                preflight_receipt="evalpf_testreceipt1234",
            ),
        )
    )
    assert live_response.text == "live answer"
    assert live_response.actual_cost_usd is None
    assert live_response.normalized_cost_usd == pytest.approx(0.02)
    assert live_response.resolved_model == "resolved-live-model"
    gateway._EVALUATION_PREFLIGHTS.pop("evalpf_testreceipt1234", None)


def test_gateway_judge_is_bound_to_the_same_live_preflight(monkeypatch):
    from multillm import gateway

    request = EvaluationRunRequest(
        suite_id="finops-v1",
        execution_mode="live_host",
        live_authorized=True,
        preflight_receipt="evalpf_judgereceipt1234",
    )
    gateway._EVALUATION_PREFLIGHTS[request.preflight_receipt] = {
        "targets": {"judge/model"},
        "expiresAt": float("inf"),
    }

    async def live(alias, prompt, max_tokens, temperature, controls=None):
        return {"text": '{"decision":"response_a"}', "error": None}

    monkeypatch.setattr(gateway, "_council_query_one", live)
    assert asyncio.run(
        gateway._gateway_evaluation_judge("judge/model", "Judge prompt", request)
    ).startswith("{")

    with pytest.raises(RuntimeError, match="preflight"):
        asyncio.run(
            gateway._gateway_evaluation_judge("other/judge", "Judge prompt", request)
        )
    gateway._EVALUATION_PREFLIGHTS.pop(request.preflight_receipt, None)


def test_gateway_moa_response_declares_all_participants_for_judge_independence(
    monkeypatch,
):
    from multillm import gateway

    async def moa(_body):
        return {
            "kind": "moa",
            "status": "completed",
            "finalAnswer": "combined",
            "totals": {"criticalPathMs": 12},
            "stages": [
                {
                    "stage": "proposer",
                    "models": [
                        {"inputTokens": 4, "outputTokens": 2},
                        {"inputTokens": 4, "outputTokens": 3},
                    ],
                },
                {
                    "stage": "aggregator",
                    "models": [{"inputTokens": 8, "outputTokens": 4}],
                },
            ],
        }

    monkeypatch.setattr(gateway, "_run_moa_request", moa)
    receipt = "evalpf_moaparticipants12"
    gateway._EVALUATION_PREFLIGHTS[receipt] = {
        "targets": {"model/a", "model/b", "model/c"},
        "expiresAt": float("inf"),
    }
    request = EvaluationRunRequest(
        suite_id="finops-v1",
        candidate_scope="explicit",
        candidates=("model/a", "model/b"),
        execution_mode="live_host",
        live_authorized=True,
        preflight_receipt=receipt,
        metadata={
            "moa_panel": ["model/a", "model/b"],
            "moa_aggregator": "model/c",
        },
    )

    response = asyncio.run(
        gateway._gateway_evaluation_execute(
            "moa/quality",
            EvaluationCase(id="case", prompt="Question", category="general"),
            request,
        )
    )

    assert response.participant_models == ("model/a", "model/b", "model/c")
    assert [
        (stage.stage, stage.input_tokens, stage.output_tokens)
        for stage in response.stage_usage
    ] == [
        ("proposer", 8, 5),
        ("aggregator", 8, 4),
    ]
    assert response.total_ms == 12
    gateway._EVALUATION_PREFLIGHTS.pop(receipt, None)
