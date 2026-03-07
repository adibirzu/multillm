"""Tests for the format converter module."""
import json
import pytest

from multillm.converters import (
    anthropic_messages_to_openai,
    openai_response_to_anthropic,
    anthropic_tools_to_openai,
    build_openai_payload,
    build_ollama_payload,
    make_anthropic_response,
    extract_text_from_anthropic,
    StreamState,
    openai_chunk_to_anthropic_events,
    ollama_chunk_to_anthropic_events,
)


# ── Message Conversion ──────────────────────────────────────────────────────

class TestAnthropicToOpenaiMessages:

    def test_simple_text_message(self):
        msgs = anthropic_messages_to_openai(
            [{"role": "user", "content": "Hello"}]
        )
        assert msgs == [{"role": "user", "content": "Hello"}]

    def test_system_prompt_string(self):
        msgs = anthropic_messages_to_openai(
            [{"role": "user", "content": "Hi"}],
            system="You are helpful.",
        )
        assert msgs[0] == {"role": "system", "content": "You are helpful."}
        assert msgs[1] == {"role": "user", "content": "Hi"}

    def test_system_prompt_list(self):
        msgs = anthropic_messages_to_openai(
            [{"role": "user", "content": "Hi"}],
            system=[{"type": "text", "text": "Be brief."}, {"type": "text", "text": "Be helpful."}],
        )
        assert msgs[0]["content"] == "Be brief. Be helpful."

    def test_content_blocks_text(self):
        msgs = anthropic_messages_to_openai([
            {"role": "user", "content": [
                {"type": "text", "text": "Hello"},
                {"type": "text", "text": "World"},
            ]},
        ])
        assert msgs[0]["content"] == "Hello\nWorld"

    def test_tool_use_blocks(self):
        msgs = anthropic_messages_to_openai([
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Let me check."},
                    {
                        "type": "tool_use",
                        "id": "toolu_abc",
                        "name": "get_weather",
                        "input": {"location": "London"},
                    },
                ],
            },
        ])
        assert len(msgs) == 1
        assert msgs[0]["role"] == "assistant"
        assert msgs[0]["content"] == "Let me check."
        assert len(msgs[0]["tool_calls"]) == 1
        tc = msgs[0]["tool_calls"][0]
        assert tc["id"] == "toolu_abc"
        assert tc["function"]["name"] == "get_weather"
        assert json.loads(tc["function"]["arguments"]) == {"location": "London"}

    def test_tool_result_blocks(self):
        msgs = anthropic_messages_to_openai([
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_abc",
                        "content": "15°C, cloudy",
                    },
                ],
            },
        ])
        assert len(msgs) == 1
        assert msgs[0]["role"] == "tool"
        assert msgs[0]["tool_call_id"] == "toolu_abc"
        assert msgs[0]["content"] == "15°C, cloudy"

    def test_tool_result_with_content_list(self):
        msgs = anthropic_messages_to_openai([
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_abc",
                        "content": [{"type": "text", "text": "Result here"}],
                    },
                ],
            },
        ])
        assert msgs[0]["content"] == "Result here"

    def test_image_blocks(self):
        msgs = anthropic_messages_to_openai([
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What's this?"},
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": "abc123"},
                    },
                ],
            },
        ])
        assert len(msgs) == 1
        content = msgs[0]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"
        assert "data:image/png;base64,abc123" in content[1]["image_url"]["url"]

    def test_multi_turn_with_tools(self, sample_anthropic_request_with_tools):
        body = sample_anthropic_request_with_tools
        msgs = anthropic_messages_to_openai(body["messages"], body.get("system"))
        # system + user + assistant(tool_call) + tool_result
        assert msgs[0]["role"] == "system"
        assert msgs[1]["role"] == "user"
        assert msgs[2]["role"] == "assistant"
        assert "tool_calls" in msgs[2]
        assert msgs[3]["role"] == "tool"


# ── Response Conversion ─────────────────────────────────────────────────────

class TestOpenaiToAnthropicResponse:

    def test_simple_text_response(self, sample_openai_response):
        result = openai_response_to_anthropic(sample_openai_response, "openai/gpt-4o")
        assert result["type"] == "message"
        assert result["role"] == "assistant"
        assert result["model"] == "openai/gpt-4o"
        assert result["stop_reason"] == "end_turn"
        assert result["content"][0]["type"] == "text"
        assert result["content"][0]["text"] == "Hello! How can I help?"
        assert result["usage"]["input_tokens"] == 10
        assert result["usage"]["output_tokens"] == 8

    def test_tool_calls_response(self, sample_openai_response_with_tools):
        result = openai_response_to_anthropic(sample_openai_response_with_tools, "openai/gpt-4o")
        assert result["stop_reason"] == "tool_use"
        assert len(result["content"]) == 1  # Only tool_use, no text (content was None)
        tool_block = result["content"][0]
        assert tool_block["type"] == "tool_use"
        assert tool_block["name"] == "get_weather"
        assert tool_block["input"] == {"location": "London", "unit": "celsius"}

    def test_empty_response(self):
        oai = {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}], "usage": {}}
        result = openai_response_to_anthropic(oai, "test")
        assert result["content"][0]["text"] == ""

    def test_length_stop_reason(self):
        oai = {
            "choices": [{"message": {"content": "truncated"}, "finish_reason": "length"}],
            "usage": {},
        }
        result = openai_response_to_anthropic(oai, "test")
        assert result["stop_reason"] == "max_tokens"


# ── Tool Definition Conversion ──────────────────────────────────────────────

class TestToolConversion:

    def test_anthropic_tools_to_openai(self):
        anthropic_tools = [
            {
                "name": "get_weather",
                "description": "Get weather",
                "input_schema": {
                    "type": "object",
                    "properties": {"location": {"type": "string"}},
                    "required": ["location"],
                },
            }
        ]
        oai_tools = anthropic_tools_to_openai(anthropic_tools)
        assert len(oai_tools) == 1
        assert oai_tools[0]["type"] == "function"
        assert oai_tools[0]["function"]["name"] == "get_weather"
        assert oai_tools[0]["function"]["parameters"]["required"] == ["location"]


# ── Payload Builders ────────────────────────────────────────────────────────

class TestPayloadBuilders:

    def test_build_openai_payload(self, sample_anthropic_request_with_system):
        payload = build_openai_payload(sample_anthropic_request_with_system, "gpt-4o")
        assert payload["model"] == "gpt-4o"
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][1]["role"] == "user"
        assert payload["max_tokens"] == 1024

    def test_build_openai_payload_with_tools(self, sample_anthropic_request_with_tools):
        payload = build_openai_payload(sample_anthropic_request_with_tools, "gpt-4o")
        assert "tools" in payload
        assert payload["tools"][0]["function"]["name"] == "get_weather"

    def test_build_openai_payload_tool_choice_any(self):
        body = {
            "messages": [{"role": "user", "content": "Hi"}],
            "tools": [{"name": "t", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "any"},
        }
        payload = build_openai_payload(body, "gpt-4o")
        assert payload["tool_choice"] == "required"

    def test_build_openai_payload_tool_choice_specific(self):
        body = {
            "messages": [{"role": "user", "content": "Hi"}],
            "tools": [{"name": "t", "input_schema": {"type": "object"}}],
            "tool_choice": {"type": "tool", "name": "get_weather"},
        }
        payload = build_openai_payload(body, "gpt-4o")
        assert payload["tool_choice"]["function"]["name"] == "get_weather"

    def test_build_ollama_payload(self, sample_anthropic_request):
        payload = build_ollama_payload(sample_anthropic_request, "llama3")
        assert payload["model"] == "llama3"
        assert payload["messages"][0]["role"] == "user"
        assert payload["options"]["num_predict"] == 1024

    def test_build_openai_payload_streaming(self):
        body = {
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
        }
        payload = build_openai_payload(body, "gpt-4o")
        assert payload["stream"] is True


# ── Response Builders ───────────────────────────────────────────────────────

class TestResponseBuilders:

    def test_make_anthropic_response(self):
        resp = make_anthropic_response("Hello", "test-model", 10, 5)
        assert resp["type"] == "message"
        assert resp["content"][0]["text"] == "Hello"
        assert resp["usage"]["input_tokens"] == 10
        assert resp["id"].startswith("msg_")

    def test_make_anthropic_response_with_custom_blocks(self):
        blocks = [
            {"type": "text", "text": "Let me help"},
            {"type": "tool_use", "id": "t1", "name": "foo", "input": {}},
        ]
        resp = make_anthropic_response("", "m", content_blocks=blocks, stop_reason="tool_use")
        assert len(resp["content"]) == 2
        assert resp["stop_reason"] == "tool_use"

    def test_extract_text_from_anthropic(self):
        body = {
            "system": "Be helpful.",
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
            ],
        }
        text = extract_text_from_anthropic(body)
        assert "Be helpful." in text
        assert "Hello" in text
        assert "Hi there" in text

    def test_extract_text_system_list(self):
        body = {
            "system": [{"type": "text", "text": "System prompt"}],
            "messages": [{"role": "user", "content": "Question"}],
        }
        text = extract_text_from_anthropic(body)
        assert "System prompt" in text

    def test_extract_text_content_blocks(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Part 1"},
                        {"type": "text", "text": "Part 2"},
                    ],
                }
            ],
        }
        text = extract_text_from_anthropic(body)
        assert "Part 1" in text
        assert "Part 2" in text


# ── Streaming Conversion ────────────────────────────────────────────────────

class TestStreamingConversion:

    def test_openai_text_streaming(self, sample_openai_stream_chunks):
        state = StreamState("test-model")
        all_events = []
        for chunk in sample_openai_stream_chunks:
            events = openai_chunk_to_anthropic_events(chunk, state)
            all_events.extend(events)

        # Should have: content_block_start, text_deltas, content_block_stop, message_delta, message_stop
        event_types = [self._extract_event_type(e) for e in all_events]
        assert "content_block_start" in event_types
        assert "content_block_delta" in event_types
        assert "content_block_stop" in event_types
        assert "message_delta" in event_types
        assert "message_stop" in event_types

    def test_openai_tool_streaming(self, sample_openai_stream_chunks_with_tools):
        state = StreamState("test-model")
        all_events = []
        for chunk in sample_openai_stream_chunks_with_tools:
            events = openai_chunk_to_anthropic_events(chunk, state)
            all_events.extend(events)

        event_types = [self._extract_event_type(e) for e in all_events]
        assert "content_block_start" in event_types
        assert "message_stop" in event_types

        # Verify tool_use block was started
        starts = [e for e in all_events if "content_block_start" in e]
        assert any('"tool_use"' in s for s in starts)

    def test_ollama_streaming(self, sample_ollama_stream_chunks):
        state = StreamState("ollama/llama3")
        all_events = []
        for chunk in sample_ollama_stream_chunks:
            events = ollama_chunk_to_anthropic_events(chunk, state)
            all_events.extend(events)

        event_types = [self._extract_event_type(e) for e in all_events]
        assert "content_block_start" in event_types
        assert "content_block_delta" in event_types
        assert "content_block_stop" in event_types
        assert "message_stop" in event_types

        # Verify token counts from final chunk
        assert state.output_tokens == 5
        assert state.input_tokens == 10

    def test_stream_state_initialization(self):
        state = StreamState("test", input_tokens=100)
        assert state.model == "test"
        assert state.input_tokens == 100
        assert state.output_tokens == 0
        assert not state.content_started
        assert state.msg_id.startswith("msg_")

    @staticmethod
    def _extract_event_type(event_str: str) -> str:
        """Extract the event type from an SSE event string."""
        for line in event_str.split("\n"):
            if line.startswith("event: "):
                return line[7:]
        return ""
