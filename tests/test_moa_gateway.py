from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from multillm.gateway import app


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
    with patch("multillm.gateway._run_moa_request", new=AsyncMock(return_value=result)) as run:
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
    with patch("multillm.gateway._run_moa_request", new=AsyncMock(return_value=result)) as run:
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
