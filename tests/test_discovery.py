"""Tests for the dynamic model discovery module."""
from unittest.mock import AsyncMock

import pytest
import httpx
import respx

from multillm.discovery import (
    discover_oca,
    discover_ollama,
    discover_lmstudio,
    discover_openai,
    discover_openrouter,
    discover_gemini,
    discover_all_models,
    discovered_to_routes,
)


@pytest.fixture(autouse=True)
def clear_discovery_cache():
    """Clear the discovery cache before each test."""
    from multillm import discovery
    discovery._discovery_cache = {}
    discovery._cache_timestamp = 0.0
    yield


class TestDiscoverOllama:

    @pytest.mark.asyncio
    @respx.mock
    async def test_discover_ollama_success(self):
        respx.get("http://localhost:11434/api/tags").mock(
            return_value=httpx.Response(200, json={
                "models": [
                    {"name": "llama3:latest", "size": 4_000_000_000,
                     "details": {"parameter_size": "8B", "family": "llama", "quantization_level": "Q4_K_M"}},
                    {"name": "qwen3:30b", "size": 18_000_000_000,
                     "details": {"parameter_size": "30B", "family": "qwen2"}},
                ]
            })
        )
        models = await discover_ollama()
        assert len(models) == 2
        assert models[0]["id"] == "ollama/llama3"
        assert models[0]["backend"] == "ollama"
        assert models[0]["parameter_size"] == "8B"
        assert models[1]["id"] == "ollama/qwen3:30b"

    @pytest.mark.asyncio
    @respx.mock
    async def test_discover_ollama_empty(self):
        respx.get("http://localhost:11434/api/tags").mock(
            return_value=httpx.Response(200, json={"models": []})
        )
        models = await discover_ollama()
        assert models == []

    @pytest.mark.asyncio
    @respx.mock
    async def test_discover_ollama_connection_error(self):
        respx.get("http://localhost:11434/api/tags").mock(side_effect=httpx.ConnectError("refused"))
        models = await discover_ollama()
        assert models == []


class TestDiscoverLmStudio:

    @pytest.mark.asyncio
    @respx.mock
    async def test_discover_lmstudio_success(self):
        respx.get("http://localhost:1234/v1/models").mock(
            return_value=httpx.Response(200, json={
                "data": [
                    {"id": "mistral-7b-instruct", "owned_by": "mistralai"},
                ]
            })
        )
        models = await discover_lmstudio()
        assert len(models) == 1
        assert models[0]["id"] == "lmstudio/mistral-7b-instruct"
        assert models[0]["backend"] == "lmstudio"


class TestDiscoverOpenAI:

    @pytest.mark.asyncio
    @respx.mock
    async def test_discover_openai_filters_chat_models(self):
        from multillm import discovery
        old_key = discovery.OPENAI_KEY
        discovery.OPENAI_KEY = "test-key"
        try:
            respx.get("https://api.openai.com/v1/models").mock(
                return_value=httpx.Response(200, json={
                    "data": [
                        {"id": "gpt-4o", "owned_by": "openai"},
                        {"id": "gpt-3.5-turbo", "owned_by": "openai"},
                        {"id": "dall-e-3", "owned_by": "openai"},
                        {"id": "text-embedding-ada-002", "owned_by": "openai"},
                        {"id": "o3-mini", "owned_by": "openai"},
                    ]
                })
            )
            models = await discover_openai()
            ids = {m["id"] for m in models}
            assert "openai/gpt-4o" in ids
            assert "openai/gpt-3.5-turbo" in ids
            assert "openai/o3-mini" in ids
            # Non-chat models should be filtered out
            assert "openai/dall-e-3" not in ids
            assert "openai/text-embedding-ada-002" not in ids
        finally:
            discovery.OPENAI_KEY = old_key

    @pytest.mark.asyncio
    async def test_discover_openai_no_key(self):
        from multillm import discovery
        old_key = discovery.OPENAI_KEY
        discovery.OPENAI_KEY = ""
        try:
            models = await discover_openai()
            assert models == []
        finally:
            discovery.OPENAI_KEY = old_key


class TestDiscoverGemini:

    @pytest.mark.asyncio
    @respx.mock
    async def test_discover_gemini_success(self):
        from multillm import discovery
        old_key = discovery.GEMINI_KEY
        discovery.GEMINI_KEY = "test-key"
        try:
            respx.get("https://generativelanguage.googleapis.com/v1beta/models").mock(
                return_value=httpx.Response(200, json={
                    "models": [
                        {
                            "name": "models/gemini-2.0-flash",
                            "displayName": "Gemini 2.0 Flash",
                            "supportedGenerationMethods": ["generateContent"],
                            "inputTokenLimit": 1048576,
                            "outputTokenLimit": 8192,
                        },
                        {
                            "name": "models/embedding-001",
                            "displayName": "Embedding",
                            "supportedGenerationMethods": ["embedContent"],
                        },
                    ]
                })
            )
            models = await discover_gemini()
            assert len(models) == 1  # embedding model filtered out
            assert models[0]["id"] == "gemini/gemini-2.0-flash"
            assert models[0]["input_token_limit"] == 1048576
        finally:
            discovery.GEMINI_KEY = old_key


class TestDiscoverOCA:

    @pytest.mark.asyncio
    async def test_discover_oca_cache_marks_catalog_source(self, monkeypatch):
        from multillm import oca_auth

        monkeypatch.setattr(oca_auth, "get_oca_bearer_token", AsyncMock(return_value=None))
        monkeypatch.setattr(
            oca_auth,
            "_load_cached_oca_models",
            lambda: [{"id": "oca/gpt-5.4"}],
        )

        models = await discover_oca()

        assert len(models) == 1
        assert models[0]["id"] == "oca/gpt-5.4"
        assert models[0]["catalog_source"] == "cache"


class TestDiscoverOpenRouter:

    @pytest.mark.asyncio
    @respx.mock
    async def test_discover_openrouter_success(self):
        from multillm import discovery
        old_key = discovery.OPENROUTER_KEY
        discovery.OPENROUTER_KEY = "test-key"
        try:
            respx.get("https://openrouter.ai/api/v1/models").mock(
                return_value=httpx.Response(200, json={
                    "data": [
                        {"id": "anthropic/claude-3-opus", "name": "Claude 3 Opus",
                         "context_length": 200000, "pricing": {"prompt": "0.015", "completion": "0.075"}},
                    ]
                })
            )
            models = await discover_openrouter()
            assert len(models) == 1
            assert models[0]["backend"] == "openrouter"
            assert models[0]["context_length"] == 200000
        finally:
            discovery.OPENROUTER_KEY = old_key


class TestDiscoverAll:

    @pytest.mark.asyncio
    @respx.mock
    async def test_discover_all_aggregates(self):
        respx.get("http://localhost:11434/api/tags").mock(
            return_value=httpx.Response(200, json={"models": [{"name": "llama3:latest", "details": {}}]})
        )
        respx.get("http://localhost:1234/v1/models").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        # Other backends return empty (no keys set)
        result = await discover_all_models(force=True)
        assert "ollama" in result
        assert "lmstudio" in result
        assert len(result["ollama"]) == 1

    @pytest.mark.asyncio
    @respx.mock
    async def test_discover_all_caches_results(self):
        respx.get("http://localhost:11434/api/tags").mock(
            return_value=httpx.Response(200, json={"models": []})
        )
        respx.get("http://localhost:1234/v1/models").mock(
            return_value=httpx.Response(200, json={"data": []})
        )
        r1 = await discover_all_models(force=True)
        r2 = await discover_all_models(force=False)
        assert r1 is r2  # Same object = cache hit


class TestDiscoveredToRoutes:

    def test_converts_to_route_format(self):
        discovered = {
            "ollama": [
                {"id": "ollama/llama3", "model": "llama3:latest", "name": "llama3"},
            ],
            "openai": [
                {"id": "openai/gpt-4o", "model": "gpt-4o", "name": "gpt-4o"},
            ],
        }
        routes = discovered_to_routes(discovered)
        assert "ollama/llama3" in routes
        assert routes["ollama/llama3"]["backend"] == "ollama"
        assert routes["ollama/llama3"]["model"] == "llama3:latest"
        assert routes["ollama/llama3"]["discovered"] is True
        assert "openai/gpt-4o" in routes
