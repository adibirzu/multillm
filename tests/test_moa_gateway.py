from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from multillm.gateway import _fusion_query_fn, app


client = TestClient(app)


def test_canonical_moa_endpoint_and_legacy_fusion_endpoint_coexist():
    result = {
        "kind": "moa",
        "status": "completed",
        "finalAnswer": "Fused answer",
        "analysis": {},
        "confidence": 0.8,
        "stages": [],
        "totals": {},
    }
    with patch(
        "multillm.gateway._run_moa_request", new=AsyncMock(return_value=result)
    ) as run:
        response = client.post(
            "/api/moa",
            json={
                "prompt": "Compare designs",
                "models": ["codex/a", "claude/b"],
                "aggregator": "gemini/c",
                "preset": "quality",
            },
        )

    assert response.status_code == 200
    assert response.json()["kind"] == "moa"
    run.assert_awaited_once()
    assert any(getattr(route, "path", None) == "/api/fusion" for route in app.routes)


def test_moa_endpoint_validates_roles_and_prompt():
    assert client.post("/api/moa", json={}).status_code == 400
    response = client.post(
        "/api/moa",
        json={"prompt": "Question", "models": ["codex/a"], "aggregator": "gemini/c"},
    )
    assert response.status_code == 422


def test_moa_endpoint_uses_default_agents_with_claude():
    result = {
        "kind": "moa",
        "status": "completed",
        "finalAnswer": "Default roster answer",
        "analysis": {},
        "confidence": 0.8,
        "stages": [],
        "totals": {},
    }
    with patch(
        "multillm.gateway._run_moa_request", new=AsyncMock(return_value=result)
    ) as run:
        response = client.post("/api/moa", json={"prompt": "Compare designs"})

    assert response.status_code == 200
    request = run.await_args.args[0]
    assert "claude-cli/sonnet" in request["models"]
    assert request["aggregator"] == "claude-cli/opus"


def test_anthropic_messages_accepts_canonical_moa_model_slug():
    result = {
        "kind": "moa",
        "status": "completed",
        "finalAnswer": "MoA result",
        "analysis": {},
        "confidence": 0.9,
        "stages": [],
        "totals": {"inputTokens": 12, "outputTokens": 4, "actualCostUSD": 0.02},
    }
    with patch(
        "multillm.gateway._run_moa_request", new=AsyncMock(return_value=result)
    ) as run:
        response = client.post(
            "/v1/messages",
            json={
                "model": "moa/quality",
                "messages": [{"role": "user", "content": "Compare designs"}],
                "moa_panel": ["codex/a", "claude/b"],
                "moa_aggregator": "gemini/c",
                "max_tokens": 100,
            },
        )

    assert response.status_code == 200
    assert response.json()["content"][0]["text"] == "MoA result"
    assert response.json()["usage"]["input_tokens"] == 12
    assert run.await_args.args[0]["preset"] == "quality"


def test_anthropic_moa_slug_uses_default_claude_agents():
    result = {
        "kind": "moa",
        "status": "completed",
        "finalAnswer": "MoA result",
        "analysis": {},
        "confidence": 0.9,
        "stages": [],
        "totals": {},
    }
    with patch(
        "multillm.gateway._run_moa_request", new=AsyncMock(return_value=result)
    ) as run:
        response = client.post(
            "/v1/messages",
            json={
                "model": "moa/quality",
                "messages": [{"role": "user", "content": "Compare designs"}],
            },
        )

    assert response.status_code == 200
    request = run.await_args.args[0]
    assert "claude-cli/sonnet" in request["models"]
    assert request["aggregator"] == "claude-cli/opus"


def test_moa_agent_call_emits_visible_langfuse_trace():
    response = {
        "alias": "claude-cli/fable",
        "backend": "claude_cli",
        "providerModel": "claude-fable-5",
        "text": "agent answer",
        "inputTokens": 5,
        "outputTokens": 8,
        "reasoningTokens": 3,
        "latencyMs": 20,
    }
    with (
        patch(
            "multillm.gateway._council_query_one",
            new=AsyncMock(return_value=response),
        ),
        patch("multillm.gateway.record_usage"),
        patch("multillm.gateway.trace_llm_generation") as trace,
    ):
        result = __import__("asyncio").run(
            _fusion_query_fn("claude-cli/fable", "agent prompt", 512, 0.2)
        )

    assert result == response
    assert trace.call_args.kwargs["prompt_text"] == "agent prompt"
    assert trace.call_args.kwargs["response_text"] == "agent answer"
    assert trace.call_args.kwargs["reasoning_tokens"] == 3
