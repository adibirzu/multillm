"""Tests for the gateway HTTP endpoints."""
import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from multillm.gateway import app
from multillm.converters import make_anthropic_response


client = TestClient(app)


class TestHealthEndpoint:

    def test_health_returns_ok(self):
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "backends" in data
        assert "routes" in data

    def test_health_shows_all_backends(self):
        response = client.get("/health")
        backends = response.json()["backends"]
        expected = {"ollama", "lmstudio", "oca", "gemini", "openai", "anthropic", "openrouter", "codex_cli", "gemini_cli"}
        assert expected == set(backends.keys())


class TestRoutesEndpoint:

    def test_routes_returns_dict(self):
        response = client.get("/routes")
        assert response.status_code == 200
        routes = response.json()
        assert isinstance(routes, dict)
        assert len(routes) > 0

    def test_routes_have_required_fields(self):
        response = client.get("/routes")
        routes = response.json()
        for alias, config in routes.items():
            assert "backend" in config, f"Route {alias} missing 'backend'"
            assert "model" in config, f"Route {alias} missing 'model'"


class TestModelsEndpoint:

    def test_list_models(self):
        response = client.get("/v1/models")
        assert response.status_code == 200
        data = response.json()
        assert data["object"] == "list"
        assert len(data["data"]) > 0

    def test_model_entries_have_required_fields(self):
        response = client.get("/v1/models")
        for model in response.json()["data"]:
            assert "id" in model
            assert "owned_by" in model


class TestUsageEndpoint:

    def test_usage_returns_data(self):
        response = client.get("/usage")
        assert response.status_code == 200
        data = response.json()
        assert "by_model" in data
        assert "by_project" in data

    def test_usage_with_project_filter(self):
        response = client.get("/usage?project=testproject&hours=1")
        assert response.status_code == 200


class TestDashboardEndpoints:

    def test_dashboard_api_returns_derived_metrics(self):
        response = client.get("/api/dashboard?hours=1")
        assert response.status_code == 200
        data = response.json()
        assert "derived" in data
        assert "hours" in data

    def test_dashboard_api_accepts_project_filter(self):
        response = client.get("/api/dashboard?hours=1&project=testproject")
        assert response.status_code == 200


class TestSettingsEndpoints:

    def test_get_settings(self):
        response = client.get("/settings")
        assert response.status_code == 200
        data = response.json()
        assert "default_model" in data

    def test_update_settings(self):
        response = client.put("/settings", json={"test_setting": "test_value"})
        assert response.status_code == 200

        response = client.get("/settings")
        assert response.json()["test_setting"] == "test_value"


class TestMessagesEndpoint:

    @patch("multillm.gateway._call_ollama")
    def test_non_streaming_request(self, mock_ollama):
        mock_response = make_anthropic_response("Hello!", "ollama/llama3", 10, 5)
        mock_ollama.return_value = mock_response

        response = client.post("/v1/messages", json={
            "model": "ollama/llama3",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "message"
        assert data["content"][0]["text"] == "Hello!"

    def test_unknown_model_returns_error(self):
        response = client.post("/v1/messages", json={
            "model": "nonexistent/model",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
        })
        assert response.status_code == 400

    @patch("multillm.gateway.ANTHROPIC_KEY", "test-key")
    @patch("multillm.gateway._call_anthropic_real")
    def test_claude_model_passthrough(self, mock_anthropic):
        mock_response = make_anthropic_response("Hi from Claude", "claude-sonnet-4-6", 10, 5)
        mock_anthropic.return_value = mock_response

        response = client.post("/v1/messages", json={
            "model": "claude-sonnet-4-6",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
        })
        assert response.status_code == 200
        assert response.json()["content"][0]["text"] == "Hi from Claude"

    @patch("multillm.gateway._call_ollama")
    def test_request_with_system_prompt(self, mock_ollama):
        mock_response = make_anthropic_response("Yes!", "ollama/llama3", 10, 5)
        mock_ollama.return_value = mock_response

        response = client.post("/v1/messages", json={
            "model": "ollama/llama3",
            "system": "You are a helpful assistant.",
            "messages": [{"role": "user", "content": "Hi"}],
            "max_tokens": 100,
        })
        assert response.status_code == 200

    @patch("multillm.gateway._call_ollama")
    def test_request_with_tools(self, mock_ollama):
        mock_response = make_anthropic_response(
            "", "ollama/llama3",
            content_blocks=[
                {"type": "tool_use", "id": "t1", "name": "get_weather", "input": {"location": "London"}},
            ],
            stop_reason="tool_use",
        )
        mock_ollama.return_value = mock_response

        response = client.post("/v1/messages", json={
            "model": "ollama/llama3",
            "messages": [{"role": "user", "content": "Weather?"}],
            "tools": [{
                "name": "get_weather",
                "description": "Get weather",
                "input_schema": {"type": "object", "properties": {"location": {"type": "string"}}},
            }],
            "max_tokens": 100,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["stop_reason"] == "tool_use"


class TestMemorySearchEndpoint:

    def test_memory_search(self):
        from multillm.memory import store_memory, delete_memory
        mem_id = store_memory(title="Gateway Test", content="test content for gateway")

        response = client.get("/memory/search?q=gateway+test")
        assert response.status_code == 200

        delete_memory(mem_id)
