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

    def test_dashboard_page_includes_inline_favicon(self):
        response = client.get("/dashboard")
        assert response.status_code == 200
        assert 'rel="icon"' in response.text


class TestSettingsEndpoints:

    def test_get_settings(self):
        response = client.get("/settings")
        assert response.status_code == 200
        data = response.json()
        assert "default_model" in data
        assert "usage_limits" in data

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


class TestObservabilityEndpoints:

    @patch("multillm.gateway.get_langfuse_status")
    def test_otel_endpoint_includes_langfuse_status(self, mock_langfuse_status):
        mock_langfuse_status.return_value = {
            "enabled": True,
            "initialized": True,
            "host": "http://localhost:3001",
            "has_keys": True,
        }

        response = client.get("/api/otel")
        assert response.status_code == 200
        data = response.json()
        assert "langfuse" in data
        assert data["langfuse"]["initialized"] is True

    @patch("multillm.gateway.get_gemini_stats")
    def test_gemini_stats_endpoint(self, mock_gemini_stats):
        mock_gemini_stats.return_value = {"available": True, "totalSessions": 2, "totalTokens": 1000}

        response = client.get("/api/gemini-stats?hours=24")
        assert response.status_code == 200
        assert response.json()["available"] is True

    @patch("multillm.gateway.get_gemini_stats")
    def test_gemini_stats_endpoint_forwards_project_filter(self, mock_gemini_stats):
        mock_gemini_stats.return_value = {"available": True, "totalSessions": 2, "totalTokens": 1000}

        response = client.get("/api/gemini-stats?hours=24&project=testproject")
        assert response.status_code == 200
        mock_gemini_stats.assert_called_once_with(hours=24, project="testproject")

    @patch("multillm.gateway.get_codex_stats")
    def test_codex_stats_endpoint(self, mock_codex_stats):
        mock_codex_stats.return_value = {"available": True, "totalSessions": 2, "totalTokens": 1000}

        response = client.get("/api/codex-stats?hours=24")
        assert response.status_code == 200
        assert response.json()["available"] is True

    @patch("multillm.gateway.get_codex_stats")
    def test_codex_stats_endpoint_forwards_project_filter(self, mock_codex_stats):
        mock_codex_stats.return_value = {"available": True, "totalSessions": 2, "totalTokens": 1000}

        response = client.get("/api/codex-stats?hours=24&project=testproject")
        assert response.status_code == 200
        mock_codex_stats.assert_called_once_with(hours=24, project="testproject")

    @patch("multillm.gateway.get_claude_code_stats")
    def test_claude_stats_endpoint_forwards_filters(self, mock_claude_stats):
        mock_claude_stats.return_value = {"available": True, "totalSessions": 1, "totalMessages": 2, "modelUsage": {}}

        response = client.get("/api/claude-stats?hours=24&project=testproject")
        assert response.status_code == 200
        mock_claude_stats.assert_called_once_with(hours=24, project="testproject")

    @patch("multillm.gateway.get_gemini_stats")
    @patch("multillm.gateway.get_codex_stats")
    @patch("multillm.gateway.get_claude_code_stats")
    @patch("multillm.gateway.get_dashboard_stats")
    def test_all_llm_usage_endpoint_returns_limit_summary(
        self,
        mock_dashboard_stats,
        mock_claude_stats,
        mock_codex_stats,
        mock_gemini_stats,
    ):
        mock_dashboard_stats.return_value = {
            "totals": {
                "total_input": 100,
                "total_output": 50,
                "total_cost": 1.25,
                "total_requests": 2,
            },
            "session_count": 1,
            "by_model": [],
        }
        mock_claude_stats.return_value = {
            "available": True,
            "totalSessions": 3,
            "totalMessages": 10,
            "modelUsage": {
                "claude-sonnet-4-6": {
                    "inputTokens": 1000,
                    "outputTokens": 500,
                    "cacheReadInputTokens": 0,
                    "cacheCreationInputTokens": 0,
                    "estimatedCostUSD": 0.15,
                }
            },
            "dailyActivity": [],
            "dailyModelTokens": [
                {"date": "2026-04-06", "tokensByModel": {"claude-sonnet-4-6": 1500}}
            ],
            "latestDate": "2026-04-06",
        }
        mock_codex_stats.return_value = {
            "available": True,
            "totalSessions": 2,
            "totalTokens": 4000,
            "totalActualCostUSD": 1.2,
            "totalListPriceUSD": 2.4,
            "byModel": {},
            "byProvider": {
                "openai": {
                    "tokens": 1000,
                    "sessions": 1,
                    "actualCostUSD": 1.2,
                    "listPriceUSD": 1.2,
                    "isOCA": False,
                },
                "oca-chicago": {
                    "tokens": 3000,
                    "sessions": 1,
                    "actualCostUSD": 0.0,
                    "listPriceUSD": 1.2,
                    "isOCA": True,
                },
            },
            "daily": [],
        }
        mock_gemini_stats.return_value = {
            "available": True,
            "totalSessions": 4,
            "totalTokens": 5000,
            "totalEstimatedCostUSD": 0.9,
            "model": "gemini-2.5-pro",
            "byProject": {},
            "daily": [],
        }

        response = client.get("/api/all-llm-usage?hours=24")
        assert response.status_code == 200
        data = response.json()
        mock_claude_stats.assert_called_once_with(hours=24, project=None)
        assert "statusBySource" in data
        assert "limits" in data
        assert data["statusBySource"]["gemini_cli"]["status"] == "active"
        assert data["statusBySource"]["codex_cli"]["status"] == "external_usage"
        limit_ids = {item["id"] for item in data["limits"]["items"]}
        assert "gemini_cli" in limit_ids
        assert "codex_cli_external" in limit_ids
        assert "modelItems" in data["limits"]
        claude_source = next(item for item in data["sources"] if item["source"] == "claude_code")
        assert claude_source["tokens"] == 1500
