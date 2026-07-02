from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from multillm import gateway


client = TestClient(gateway.app)


def test_live_preflight_requires_operator_enablement(monkeypatch):
    monkeypatch.delenv("MULTILLM_EVAL_ALLOW_LIVE_HOST", raising=False)
    response = client.post(
        "/api/evaluations/preflight",
        json={"targets": ["codex/gpt-5-5"]},
    )
    assert response.status_code == 403
    assert response.json()["success"] is False


def test_live_preflight_validation_uses_the_evaluation_envelope(monkeypatch):
    monkeypatch.setenv("MULTILLM_EVAL_ALLOW_LIVE_HOST", "true")

    response = client.post("/api/evaluations/preflight", json={"targets": []})

    assert response.status_code == 422
    assert response.json()["success"] is False
    assert response.json()["error"]["message"]


def test_live_preflight_executes_each_alias_and_returns_bound_receipt(monkeypatch):
    monkeypatch.setenv("MULTILLM_EVAL_ALLOW_LIVE_HOST", "true")
    monkeypatch.setitem(
        gateway.ROUTES,
        "test/model-a",
        {"backend": "codex_cli", "model": "gpt-test"},
    )
    result = {
        "alias": "test/model-a",
        "text": "MULTILLM_EVAL_PROBE_OK",
        "providerModel": "resolved-test-model",
        "latencyMs": 12,
        "error": None,
    }
    with patch(
        "multillm.gateway._council_query_one", new=AsyncMock(return_value=result)
    ) as query:
        response = client.post(
            "/api/evaluations/preflight",
            json={"targets": ["test/model-a"]},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["executionMode"] == "live_host"
    assert payload["data"]["sandboxFallback"] is False
    assert payload["data"]["receipt"].startswith("evalpf_")
    assert payload["data"]["targets"][0]["executionVerified"] is True
    query.assert_awaited_once()


def test_live_preflight_fails_when_probe_does_not_return_expected_marker(monkeypatch):
    monkeypatch.setenv("MULTILLM_EVAL_ALLOW_LIVE_HOST", "true")
    monkeypatch.setitem(
        gateway.ROUTES,
        "test/model-b",
        {"backend": "claude_cli", "model": "sonnet"},
    )
    with patch(
        "multillm.gateway._council_query_one",
        new=AsyncMock(return_value={"text": "unexpected", "error": None}),
    ):
        response = client.post(
            "/api/evaluations/preflight",
            json={"targets": ["test/model-b"]},
        )

    assert response.status_code == 409
    assert response.json()["data"]["targets"][0]["executionVerified"] is False


def test_live_target_catalog_is_deduplicated_but_not_execution_verified(monkeypatch):
    monkeypatch.setattr(
        "multillm.cli_discovery.discover_cli_agents",
        lambda _routes: {
            "codex_cli": {
                "available": True,
                "models": [
                    {"id": "codex/a", "model": "gpt-x"},
                    {"id": "codex/a-alias", "model": "gpt-x"},
                ],
            },
            "claude_cli": {
                "available": False,
                "models": [{"id": "claude/offline", "model": "sonnet"}],
            },
        },
    )

    response = client.get("/api/evaluations/live-targets")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["meta"]["executionVerified"] is False
    assert payload["data"]["targets"] == [
        {
            "alias": "codex/a",
            "backend": "codex_cli",
            "providerModel": "gpt-x",
            "equivalentAliases": ["codex/a", "codex/a-alias"],
        }
    ]
