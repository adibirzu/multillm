# SPDX-License-Identifier: Apache-2.0

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from multillm import gateway
from multillm.gateway import _extract_usage_metrics, app
from multillm.orchestration_store import OrchestrationStore


client = TestClient(app)

DEEPEVAL_ROOT = Path(__file__).resolve().parents[1] / "evals" / "deepeval"


def test_deepeval_target_catalog_is_extensible_and_moa_is_final_target():
    models = json.loads((DEEPEVAL_ROOT / "models.json").read_text(encoding="utf-8"))
    assert models["schema_version"] == 1
    assert len(models["targets"]) >= 6
    assert all({"id", "label", "default_alias", "alias_env", "reasoning_ceiling"} <= set(target) for target in models["targets"])
    assert models["moa"]["id"] == "moa-quality"


def _adaptive_result():
    return {
        "runId": "orch_test",
        "status": "accepted",
        "decision": {"earlyExitReason": "low_risk_deterministic_pass"},
        "stages": [],
        "evidence": None,
        "confidence": 0.9,
        "finalAnswer": "adaptive answer",
        "panel": [],
        "analysis": "",
        "judge": None,
        "totals": {
            "estimatedCostUSD": 0.01,
            "actualCostUSD": 0.01,
            "costUSD": 0.01,
            "panelSucceeded": 1,
        },
    }


def test_messages_rejects_unknown_multillm_policy_fields():
    response = client.post(
        "/v1/messages",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
            "metadata": {"multillm": {"unexpected": True}},
        },
    )

    assert response.status_code == 400
    assert "unexpected" in response.json()["detail"].lower()


def test_messages_rejects_noncritical_max_reasoning():
    response = client.post(
        "/v1/messages",
        json={
            "model": "auto",
            "messages": [{"role": "user", "content": "hello"}],
            "metadata": {"multillm": {"reasoning_ceiling": "max"}},
        },
    )

    assert response.status_code == 400
    assert "critical" in response.json()["detail"].lower()


def test_fusion_preset_uses_adaptive_engine_and_preserves_response_shape():
    with patch(
        "multillm.gateway._run_adaptive", new=AsyncMock(return_value=_adaptive_result())
    ) as run:
        response = client.post(
            "/api/fusion",
            json={"prompt": "Compare two designs", "preset": "economy"},
        )

    assert response.status_code == 200
    assert response.json()["runId"] == "orch_test"
    assert response.json()["finalAnswer"] == "adaptive answer"
    assert run.await_args.kwargs["force_deliberation"] is True


def test_adaptive_endpoint_preserves_full_result_without_forced_deliberation():
    with patch(
        "multillm.gateway._run_adaptive", new=AsyncMock(return_value=_adaptive_result())
    ) as run:
        response = client.post("/api/adaptive", json={"prompt": "Review this", "preset": "balanced"})

    assert response.status_code == 200
    assert response.json()["runId"] == "orch_test"
    assert run.await_args.kwargs["force_deliberation"] is False


def test_adaptive_endpoint_requires_a_prompt():
    assert client.post("/api/adaptive", json={}).status_code == 400


@pytest.mark.skipif(os.getenv("DEEPEVAL_E2E") != "1", reason="live DeepEval is opt-in")
def test_deepeval_compares_live_models_then_moa_last():
    """A live black-box comparison kept outside the OCI Skills repository."""
    from deepeval import assert_test
    from deepeval.metrics import GEval
    from deepeval.models.base_model import DeepEvalBaseLLM
    from deepeval.test_case import LLMTestCase, SingleTurnParams

    models = json.loads((DEEPEVAL_ROOT / "models.json").read_text(encoding="utf-8"))
    cases = json.loads((DEEPEVAL_ROOT / "cases.json").read_text(encoding="utf-8"))["cases"]
    gateway_url = os.getenv("LLM_GATEWAY_URL", "http://localhost:8080").rstrip("/")
    judge_alias = os.getenv("MULTILLM_EVAL_JUDGE_ALIAS", "").strip()
    if not judge_alias:
        pytest.skip("MULTILLM_EVAL_JUDGE_ALIAS is required")

    import httpx
    headers = {"X-API-Key": os.environ["MULTILLM_API_KEY"]} if os.getenv("MULTILLM_API_KEY") else {}
    with httpx.Client(timeout=30) as live_client:
        try:
            catalog_response = live_client.get(f"{gateway_url}/api/models/catalog", params={"refresh": "true"}, headers=headers)
            catalog_response.raise_for_status()
            catalog = catalog_response.json()
        except httpx.HTTPError as exc:
            pytest.skip(f"live discovery unavailable: {type(exc).__name__}")
        available = {item["alias"] for item in catalog.get("models", []) if item.get("available")}
        targets = [target for target in models["targets"] if os.getenv(target["alias_env"], target["default_alias"]) in available]
        if not targets:
            pytest.skip("no configured evaluation aliases were live after discovery")
        if len(targets) < 2:
            pytest.fail(f"Fusion comparison requires at least two live targets; got {[target['id'] for target in targets]}")

        def ask(alias: str, command: str, effort: str = "medium") -> str:
            response = live_client.post(f"{gateway_url}/v1/messages", headers=headers, json={"model": alias, "messages": [{"role": "user", "content": command}], "metadata": {"multillm": {"reasoning_ceiling": effort}}})
            response.raise_for_status()
            return str((response.json().get("content") or [{}])[0].get("text") or "")

        # Individual targets must complete before the final Fusion request.
        outputs = [(target["label"], ask(os.getenv(target["alias_env"], target["default_alias"]), case["command"])) for target in targets for case in cases]
        aliases = [os.getenv(target["alias_env"], target["default_alias"]) for target in targets]
        for case in cases:
            aggregator = os.getenv(
                models["moa"]["aggregator_alias_env"],
                models["moa"]["default_aggregator_alias"],
            )
            response = live_client.post(
                f"{gateway_url}/api/moa",
                headers=headers,
                json={
                    "prompt": case["command"],
                    "models": aliases,
                    "aggregator": aggregator,
                    "preset": models["moa"]["preset"],
                },
            )
            response.raise_for_status()
            outputs.append((models["moa"]["label"], str(response.json().get("finalAnswer") or "")))

    class GatewayJudge(DeepEvalBaseLLM):
        def load_model(self): return self
        def generate(self, prompt: str) -> str:
            with httpx.Client(timeout=180) as judge_client:
                response = judge_client.post(f"{gateway_url}/v1/messages", headers=headers, json={"model": judge_alias, "messages": [{"role": "user", "content": prompt}], "metadata": {"multillm": {"reasoning_ceiling": "medium"}}})
                response.raise_for_status()
                return str((response.json().get("content") or [{}])[0].get("text") or "")
        async def a_generate(self, prompt: str) -> str: return self.generate(prompt)
        def get_model_name(self) -> str: return f"gateway:{judge_alias}"

    for index, (_label, output) in enumerate(outputs):
        case = cases[index % len(cases)]
        metric = GEval(name="Gateway response quality", criteria=case["criteria"], evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT], model=GatewayJudge(), threshold=0.5)
        assert_test(LLMTestCase(input=case["command"], actual_output=output), [metric])


def test_explicit_fusion_panel_still_uses_legacy_pipeline():
    legacy = {
        "status": "single",
        "finalAnswer": "legacy",
        "panel": [],
        "judge": None,
        "analysis": "",
        "totals": {"costUSD": 0, "panelSucceeded": 1},
    }
    with (
        patch("multillm.gateway._run_fusion", new=AsyncMock(return_value=legacy)) as old,
        patch("multillm.gateway._run_adaptive", new=AsyncMock()) as adaptive,
    ):
        response = client.post(
            "/api/fusion",
            json={
                "prompt": "Compare",
                "preset": "quality",
                "fusion_panel": ["ollama/llama3"],
                "fusion_judge": "ollama/llama3",
            },
        )

    assert response.status_code == 200
    assert response.json()["finalAnswer"] == "legacy"
    old.assert_awaited_once()
    adaptive.assert_not_awaited()


def test_council_synthesized_routes_through_shared_engine():
    with patch(
        "multillm.gateway._run_adaptive", new=AsyncMock(return_value=_adaptive_result())
    ) as run:
        response = client.post(
            "/api/council",
            json={
                "prompt": "Review this design",
                "mode": "synthesized",
                "models": ["ollama/llama3"],
            },
        )

    assert response.status_code == 200
    assert response.json()["mode"] == "synthesized"
    assert response.json()["finalAnswer"] == "adaptive answer"
    assert run.await_args.kwargs["candidates"] == ["ollama/llama3"]


def test_capabilities_endpoint_reports_model_level_pricing_and_controls():
    routes = {
        "openai/gpt-5-5": {"backend": "openai", "model": "gpt-5.5"},
        "ollama/llama3": {"backend": "ollama", "model": "llama3"},
    }
    with patch.dict("multillm.gateway.ROUTES", routes, clear=True):
        response = client.get("/api/models/capabilities")

    assert response.status_code == 200
    profiles = {item["alias"]: item for item in response.json()["profiles"]}
    assert profiles["openai/gpt-5-5"]["protocol"] == "responses"
    assert profiles["openai/gpt-5-5"]["pricing"]["output_per_million"] > 0
    assert "high" in profiles["openai/gpt-5-5"]["reasoning_efforts"]


def test_catalog_treats_detected_cli_models_as_live():
    routes = {"codex/gpt-5-5": {"backend": "codex_cli", "model": "codex:gpt-5-5"}}
    cli = {"codex_cli": {"available": True, "models": [{"id": "codex/gpt-5-5"}]}}
    with (
        patch.dict("multillm.gateway.ROUTES", routes, clear=True),
        patch("multillm.gateway.discover_all_models", new=AsyncMock(return_value={})),
        patch("multillm.cli_discovery.discover_cli_agents", return_value=cli),
    ):
        response = client.get("/api/models/catalog")

    assert response.status_code == 200
    model = response.json()["models"][0]
    assert model["available"] is True
    assert model["classificationSource"] == "cli_discovery"


def test_normalized_usage_includes_reasoning_cache_and_model_identity():
    usage = _extract_usage_metrics(
        {
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_read_input_tokens": 70,
                "cache_creation_input_tokens": 10,
                "reasoning_tokens": 25,
                "service_tier": "default",
                "provider_model": "gpt-5.5-2026-01-01",
            }
        }
    )

    assert usage["reasoning_tokens"] == 25
    assert usage["cache_read_input_tokens"] == 70
    assert usage["cache_creation_input_tokens"] == 10
    assert usage["service_tier"] == "default"
    assert usage["provider_model"] == "gpt-5.5-2026-01-01"


def test_run_adaptive_executes_and_persists_sanitized_trace(tmp_path):
    routes = {"ollama/small": {"backend": "ollama", "model": "small"}}
    query = AsyncMock(
        return_value={
            "alias": "ollama/small",
            "backend": "ollama",
            "text": "Paris.",
            "inputTokens": 3,
            "outputTokens": 2,
            "actualCostUSD": 0,
            "latencyMs": 2,
            "error": None,
        }
    )
    store = OrchestrationStore(tmp_path / "multillm.db")
    with (
        patch.dict("multillm.gateway.ROUTES", routes, clear=True),
        patch("multillm.gateway._adaptive_query_fn", new=query),
        patch("multillm.gateway._orchestration_store", return_value=store),
        patch("multillm.gateway.score_backend", return_value={"score": 1.0}),
    ):
        result = asyncio.run(
            gateway._run_adaptive(
                {
                    "messages": [
                        {"role": "user", "content": "What is the capital of France?"}
                    ],
                    "metadata": {"multillm": {"preset": "balanced"}},
                    "max_tokens": 50,
                }
            )
        )

    assert result["status"] == "accepted"
    trace = store.get_trace("default", result["runId"])
    assert trace is not None
    assert trace["calls"][0]["model"] == "ollama/small"
    assert "capital of France" not in str(trace)


def test_council_query_builds_verifier_and_comparison_schemas():
    routes = {"openai/gpt-5": {"backend": "openai", "model": "gpt-5.5"}}
    seen = []

    async def fake_route(body, model_alias=None, route=None):
        seen.append(body)
        return {
            "content": [{"type": "text", "text": "{}"}],
            "usage": {"input_tokens": 10, "output_tokens": 2},
        }

    with (
        patch.dict("multillm.gateway.ROUTES", routes, clear=True),
        patch("multillm.gateway.route_request", new=AsyncMock(side_effect=fake_route)),
    ):
        verifier = asyncio.run(
            gateway._council_query_one(
                "openai/gpt-5", "verify", 100, 0.2, {"structured_output": "verifier"}
            )
        )
        comparison = asyncio.run(
            gateway._council_query_one(
                "openai/gpt-5",
                "compare",
                100,
                0.2,
                {"structured_output": "comparison"},
            )
        )

    assert verifier["error"] is None and comparison["error"] is None
    assert seen[0]["output_schema"]["name"] == "multillm_verifier_verdict"
    assert seen[1]["output_schema"]["name"] == "multillm_comparison"


def test_council_query_captures_http_and_unexpected_errors():
    with patch(
        "multillm.gateway.route_request",
        new=AsyncMock(side_effect=HTTPException(status_code=429, detail="quota")),
    ):
        quota = asyncio.run(
            gateway._council_query_one("missing/model", "x", 10, 0.2)
        )
    with patch(
        "multillm.gateway.route_request",
        new=AsyncMock(side_effect=RuntimeError("exploded")),
    ):
        exploded = asyncio.run(
            gateway._council_query_one("missing/model", "x", 10, 0.2)
        )

    assert quota["error"] == "429: quota"
    assert exploded["error"] == "exploded"


def test_trace_and_feedback_endpoints_update_local_scorecard(tmp_path):
    store = OrchestrationStore(tmp_path / "multillm.db")
    run_id = store.create_run(
        "default", "private", {"preset": "balanced"}, {"task_type": "coding"}
    )
    store.complete_run(
        "default",
        run_id,
        decision={"selectedModels": ["openai/gpt-5"]},
        totals={"actualCostUSD": 0.02},
        outcome="accepted",
    )
    with patch("multillm.gateway._orchestration_store", return_value=store):
        trace_response = client.get(f"/api/orchestration/{run_id}")
        feedback_response = client.post(
            f"/api/orchestration/{run_id}/feedback",
            json={"rating": 5, "issue_categories": ["correct"]},
        )
        bad_field = client.post(
            f"/api/orchestration/{run_id}/feedback",
            json={"rating": 5, "raw_answer": "do not retain"},
        )

    assert trace_response.status_code == 200
    assert feedback_response.status_code == 200
    assert bad_field.status_code == 400
    assert store.list_feedback("default")[0]["rating"] == 5


def test_trace_and_feedback_endpoints_return_not_found(tmp_path):
    store = OrchestrationStore(tmp_path / "multillm.db")
    with patch("multillm.gateway._orchestration_store", return_value=store):
        assert client.get("/api/orchestration/missing").status_code == 404
        assert (
            client.post(
                "/api/orchestration/missing/feedback", json={"rating": 4}
            ).status_code
            == 404
        )


def test_adaptive_rollout_setting_supports_rollback_and_invalid_percentage():
    with patch(
        "multillm.memory.get_setting",
        side_effect=lambda key, default=None: {
            "adaptive_auto_enabled": False,
        }.get(key, default),
    ):
        assert gateway._adaptive_auto_enabled("prompt") is False

    with patch(
        "multillm.memory.get_setting",
        side_effect=lambda key, default=None: {
            "adaptive_auto_enabled": True,
            "adaptive_auto_rollout_percent": "invalid",
        }.get(key, default),
    ):
        assert gateway._adaptive_auto_enabled("prompt") is True


def test_server_policy_limits_can_only_tighten_request_policy():
    with patch(
        "multillm.memory.get_setting",
        side_effect=lambda key, default=None: {
            "orchestration_policy_limits": {
                "max_cost_usd": 0.1,
                "max_latency_ms": 5_000,
                "reasoning_ceiling": "medium",
                "allowed_providers": ["openai", "ollama"],
                "require_vendor_diversity": True,
            }
        }.get(key, default),
    ):
        policy = gateway._resolve_orchestration_policy(
            {
                "metadata": {
                    "multillm": {
                        "max_cost_usd": 1.0,
                        "max_latency_ms": 30_000,
                        "reasoning_ceiling": "high",
                        "allowed_providers": ["openai", "anthropic"],
                        "require_vendor_diversity": False,
                    }
                }
            }
        )

    assert policy.max_cost_usd == 0.1
    assert policy.max_latency_ms == 5_000
    assert policy.reasoning_ceiling.value == "medium"
    assert policy.allowed_providers == ("openai",)
    assert policy.require_vendor_diversity is True
