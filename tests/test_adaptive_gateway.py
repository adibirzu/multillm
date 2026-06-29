# SPDX-License-Identifier: Apache-2.0

import asyncio
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from multillm import gateway
from multillm.gateway import _extract_usage_metrics, app
from multillm.orchestration_store import OrchestrationStore


client = TestClient(app)


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
