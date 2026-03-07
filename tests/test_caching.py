"""Tests for the semantic caching module."""
import json
from unittest.mock import MagicMock, patch

import pytest

from multillm.caching import (
    _extract_prompt_text,
    _make_attributes,
    cache_search,
    cache_store,
    get_cache_stats,
    cache_flush,
    _cache_stats,
)


@pytest.fixture(autouse=True)
def reset_cache_state():
    """Reset cache stats and client between tests."""
    from multillm import caching
    caching._cache_client = None
    caching._cache_stats = {"hits": 0, "misses": 0, "stores": 0, "errors": 0}
    old_enabled = caching.LANGCACHE_ENABLED
    old_cross = caching.LANGCACHE_CROSS_MODEL
    yield
    caching.LANGCACHE_ENABLED = old_enabled
    caching.LANGCACHE_CROSS_MODEL = old_cross
    caching._cache_client = None


class TestExtractPromptText:

    def test_simple_user_message(self):
        body = {"messages": [{"role": "user", "content": "Hello world"}]}
        text = _extract_prompt_text(body)
        assert "[user]Hello world" in text

    def test_with_system_prompt(self):
        body = {
            "system": "You are helpful.",
            "messages": [{"role": "user", "content": "Hi"}],
        }
        text = _extract_prompt_text(body)
        assert "[system]You are helpful." in text
        assert "[user]Hi" in text

    def test_system_as_list(self):
        body = {
            "system": [{"type": "text", "text": "Be concise."}],
            "messages": [{"role": "user", "content": "Hi"}],
        }
        text = _extract_prompt_text(body)
        assert "[system]Be concise." in text

    def test_last_3_messages(self):
        body = {
            "messages": [
                {"role": "user", "content": "msg1"},
                {"role": "assistant", "content": "resp1"},
                {"role": "user", "content": "msg2"},
                {"role": "assistant", "content": "resp2"},
                {"role": "user", "content": "msg3"},
            ],
        }
        text = _extract_prompt_text(body)
        # Only last 3 messages
        assert "msg1" not in text
        assert "resp1" not in text
        assert "msg2" in text
        assert "resp2" in text
        assert "msg3" in text

    def test_content_blocks(self):
        body = {
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is this?"},
                    {"type": "image", "source": {"type": "base64", "data": "abc"}},
                ],
            }],
        }
        text = _extract_prompt_text(body)
        assert "What is this?" in text

    def test_empty_messages(self):
        assert _extract_prompt_text({}) == ""
        assert _extract_prompt_text({"messages": []}) == ""


class TestMakeAttributes:

    def test_default_attributes(self):
        from multillm import caching
        caching.LANGCACHE_CROSS_MODEL = False
        attrs = _make_attributes("ollama/llama3", "ollama", "myproject")
        assert attrs == {"model": "ollama/llama3", "backend": "ollama", "project": "myproject"}

    def test_cross_model_search(self):
        from multillm import caching
        caching.LANGCACHE_CROSS_MODEL = True
        attrs = _make_attributes("ollama/llama3", "ollama", "myproject", for_search=True)
        assert attrs == {"project": "myproject"}
        assert "model" not in attrs

    def test_cross_model_store(self):
        """Even with cross-model enabled, stores should include model info."""
        from multillm import caching
        caching.LANGCACHE_CROSS_MODEL = True
        attrs = _make_attributes("ollama/llama3", "ollama", "myproject", for_search=False)
        assert "model" in attrs
        assert "backend" in attrs


class TestCacheSearch:

    @pytest.mark.asyncio
    async def test_disabled_returns_none(self):
        from multillm import caching
        caching.LANGCACHE_ENABLED = False
        result = await cache_search(
            {"messages": [{"role": "user", "content": "Hello"}]},
            "test", "ollama", "proj",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_short_prompt_returns_none(self):
        from multillm import caching
        caching.LANGCACHE_ENABLED = True
        caching._cache_client = MagicMock()
        result = await cache_search(
            {"messages": [{"role": "user", "content": "Hi"}]},
            "test", "ollama", "proj",
        )
        assert result is None  # prompt too short (<10 chars)

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        from multillm import caching
        caching.LANGCACHE_ENABLED = True

        mock_result = MagicMock()
        mock_result.response = json.dumps({
            "content": [{"type": "text", "text": "cached answer"}],
            "model": "test-model",
            "stop_reason": "end_turn",
        })
        mock_result.entry_id = "entry123"

        mock_client = MagicMock()
        mock_client.search.return_value = mock_result
        caching._cache_client = mock_client

        result = await cache_search(
            {"messages": [{"role": "user", "content": "What is the meaning of life?"}]},
            "ollama/llama3", "ollama", "proj",
        )
        assert result is not None
        assert result["_cached"] is True
        assert result["content"][0]["text"] == "cached answer"
        assert caching._cache_stats["hits"] == 1

    @pytest.mark.asyncio
    async def test_cache_miss(self):
        from multillm import caching
        caching.LANGCACHE_ENABLED = True

        mock_client = MagicMock()
        mock_client.search.return_value = None
        caching._cache_client = mock_client

        result = await cache_search(
            {"messages": [{"role": "user", "content": "What is the meaning of life?"}]},
            "ollama/llama3", "ollama", "proj",
        )
        assert result is None
        assert caching._cache_stats["misses"] == 1

    @pytest.mark.asyncio
    async def test_cache_error_handled(self):
        from multillm import caching
        caching.LANGCACHE_ENABLED = True

        mock_client = MagicMock()
        mock_client.search.side_effect = Exception("Redis down")
        caching._cache_client = mock_client

        result = await cache_search(
            {"messages": [{"role": "user", "content": "What is the meaning of life?"}]},
            "ollama/llama3", "ollama", "proj",
        )
        assert result is None
        assert caching._cache_stats["errors"] == 1


class TestCacheStore:

    @pytest.mark.asyncio
    async def test_stores_successful_response(self):
        from multillm import caching
        caching.LANGCACHE_ENABLED = True

        mock_client = MagicMock()
        caching._cache_client = mock_client

        body = {"messages": [{"role": "user", "content": "What is the meaning of life?"}]}
        response = {
            "content": [{"type": "text", "text": "42"}],
            "model": "llama3",
            "stop_reason": "end_turn",
        }
        result = await cache_store(body, response, "ollama/llama3", "ollama", "proj")
        assert result is True
        mock_client.set.assert_called_once()
        assert caching._cache_stats["stores"] == 1

    @pytest.mark.asyncio
    async def test_skips_error_response(self):
        from multillm import caching
        caching.LANGCACHE_ENABLED = True
        mock_client = MagicMock()
        caching._cache_client = mock_client

        body = {"messages": [{"role": "user", "content": "What is the meaning of life?"}]}
        response = {"content": [{"type": "text", "text": "err"}], "stop_reason": "error"}
        result = await cache_store(body, response, "test", "ollama", "proj")
        assert result is False
        mock_client.set.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_tool_use_response(self):
        from multillm import caching
        caching.LANGCACHE_ENABLED = True
        mock_client = MagicMock()
        caching._cache_client = mock_client

        body = {"messages": [{"role": "user", "content": "What is the meaning of life?"}]}
        response = {
            "content": [{"type": "tool_use", "id": "t1", "name": "search", "input": {}}],
            "stop_reason": "tool_use",
        }
        result = await cache_store(body, response, "test", "ollama", "proj")
        assert result is False

    @pytest.mark.asyncio
    async def test_skips_empty_content(self):
        from multillm import caching
        caching.LANGCACHE_ENABLED = True
        mock_client = MagicMock()
        caching._cache_client = mock_client

        body = {"messages": [{"role": "user", "content": "What is the meaning of life?"}]}
        response = {"content": [], "stop_reason": "end_turn"}
        result = await cache_store(body, response, "test", "ollama", "proj")
        assert result is False

    @pytest.mark.asyncio
    async def test_disabled_returns_false(self):
        from multillm import caching
        caching.LANGCACHE_ENABLED = False
        result = await cache_store({}, {}, "test", "ollama", "proj")
        assert result is False


class TestCacheStats:

    def test_stats_structure(self):
        stats = get_cache_stats()
        assert "hits" in stats
        assert "misses" in stats
        assert "stores" in stats
        assert "errors" in stats
        assert "hit_rate_pct" in stats
        assert "enabled" in stats
        assert "cross_model" in stats
        assert "threshold" in stats

    def test_hit_rate_calculation(self):
        from multillm import caching
        caching._cache_stats = {"hits": 3, "misses": 7, "stores": 3, "errors": 0}
        stats = get_cache_stats()
        assert stats["hit_rate_pct"] == 30.0
        assert stats["total_lookups"] == 10

    def test_zero_lookups(self):
        from multillm import caching
        caching._cache_stats = {"hits": 0, "misses": 0, "stores": 0, "errors": 0}
        stats = get_cache_stats()
        assert stats["hit_rate_pct"] == 0
        assert stats["total_lookups"] == 0


class TestCacheFlush:

    @pytest.mark.asyncio
    async def test_flush_disabled(self):
        from multillm import caching
        caching.LANGCACHE_ENABLED = False
        result = await cache_flush()
        assert result is False

    @pytest.mark.asyncio
    async def test_flush_success(self):
        from multillm import caching
        caching.LANGCACHE_ENABLED = True
        mock_client = MagicMock()
        caching._cache_client = mock_client
        caching._cache_stats = {"hits": 5, "misses": 3, "stores": 5, "errors": 0}

        result = await cache_flush()
        assert result is True
        mock_client.flush.assert_called_once()
        assert caching._cache_stats["hits"] == 0

    @pytest.mark.asyncio
    async def test_flush_error(self):
        from multillm import caching
        caching.LANGCACHE_ENABLED = True
        mock_client = MagicMock()
        mock_client.flush.side_effect = Exception("Redis error")
        caching._cache_client = mock_client

        result = await cache_flush()
        assert result is False
