"""Tests for streaming event conversion and SSE formatting."""
import json
import pytest

from multillm.converters import (
    StreamState,
    anthropic_sse_event,
    make_message_start_event,
    make_content_block_start_event,
    make_text_delta_event,
    make_tool_input_delta_event,
    make_content_block_stop_event,
    make_message_delta_event,
    make_message_stop_event,
    make_ping_event,
    openai_chunk_to_anthropic_events,
    ollama_chunk_to_anthropic_events,
)


def parse_sse_event(event_str: str) -> tuple[str, dict]:
    """Parse an SSE event string into (event_type, data_dict)."""
    event_type = ""
    data_str = ""
    for line in event_str.strip().split("\n"):
        if line.startswith("event: "):
            event_type = line[7:]
        elif line.startswith("data: "):
            data_str = line[6:]
    return event_type, json.loads(data_str) if data_str else {}


class TestSSEEventFormatting:

    def test_anthropic_sse_event(self):
        event = anthropic_sse_event("ping", {"type": "ping"})
        assert event.startswith("event: ping\n")
        assert "data: " in event
        assert event.endswith("\n\n")

    def test_message_start_event(self):
        state = StreamState("test-model", input_tokens=42)
        event = make_message_start_event(state)
        etype, data = parse_sse_event(event)
        assert etype == "message_start"
        assert data["type"] == "message_start"
        assert data["message"]["model"] == "test-model"
        assert data["message"]["usage"]["input_tokens"] == 42
        assert data["message"]["id"] == state.msg_id

    def test_content_block_start_text(self):
        event = make_content_block_start_event(0, "text")
        etype, data = parse_sse_event(event)
        assert etype == "content_block_start"
        assert data["content_block"]["type"] == "text"
        assert data["index"] == 0

    def test_content_block_start_tool_use(self):
        event = make_content_block_start_event(1, "tool_use", id="toolu_abc", name="get_weather")
        etype, data = parse_sse_event(event)
        assert data["content_block"]["type"] == "tool_use"
        assert data["content_block"]["id"] == "toolu_abc"
        assert data["content_block"]["name"] == "get_weather"
        assert data["index"] == 1

    def test_text_delta_event(self):
        event = make_text_delta_event(0, "Hello world")
        etype, data = parse_sse_event(event)
        assert etype == "content_block_delta"
        assert data["delta"]["type"] == "text_delta"
        assert data["delta"]["text"] == "Hello world"

    def test_tool_input_delta_event(self):
        event = make_tool_input_delta_event(1, '{"location":')
        etype, data = parse_sse_event(event)
        assert data["delta"]["type"] == "input_json_delta"
        assert data["delta"]["partial_json"] == '{"location":'

    def test_content_block_stop_event(self):
        event = make_content_block_stop_event(0)
        etype, data = parse_sse_event(event)
        assert etype == "content_block_stop"
        assert data["index"] == 0

    def test_message_delta_event(self):
        event = make_message_delta_event("end_turn", 42)
        etype, data = parse_sse_event(event)
        assert etype == "message_delta"
        assert data["delta"]["stop_reason"] == "end_turn"
        assert data["usage"]["output_tokens"] == 42

    def test_message_stop_event(self):
        event = make_message_stop_event()
        etype, data = parse_sse_event(event)
        assert etype == "message_stop"

    def test_ping_event(self):
        event = make_ping_event()
        etype, data = parse_sse_event(event)
        assert etype == "ping"
        assert data["type"] == "ping"


class TestOpenAIStreamConversion:

    def test_full_text_stream(self, sample_openai_stream_chunks):
        state = StreamState("test-model", input_tokens=10)
        all_events = []
        for chunk in sample_openai_stream_chunks:
            events = openai_chunk_to_anthropic_events(chunk, state)
            all_events.extend(events)

        parsed = [parse_sse_event(e) for e in all_events]

        # First event should be content_block_start
        assert parsed[0][0] == "content_block_start"

        # Should have text deltas
        deltas = [(t, d) for t, d in parsed if t == "content_block_delta"]
        texts = [d["delta"]["text"] for _, d in deltas if d["delta"].get("type") == "text_delta"]
        assert "Hello" in texts
        assert " world" in texts

        # Should end with stop events
        assert parsed[-2][0] == "message_delta"
        assert parsed[-2][1]["delta"]["stop_reason"] == "end_turn"
        assert parsed[-1][0] == "message_stop"

    def test_tool_call_stream(self, sample_openai_stream_chunks_with_tools):
        state = StreamState("test-model")
        all_events = []
        for chunk in sample_openai_stream_chunks_with_tools:
            events = openai_chunk_to_anthropic_events(chunk, state)
            all_events.extend(events)

        parsed = [parse_sse_event(e) for e in all_events]
        event_types = [t for t, _ in parsed]

        # Should have content_block_start for tool_use
        starts = [(t, d) for t, d in parsed if t == "content_block_start"]
        tool_starts = [d for _, d in starts if d["content_block"]["type"] == "tool_use"]
        assert len(tool_starts) >= 1
        assert tool_starts[0]["content_block"]["name"] == "get_weather"

        # Should have input_json_delta events
        json_deltas = [(t, d) for t, d in parsed if d.get("delta", {}).get("type") == "input_json_delta"]
        assert len(json_deltas) >= 1

        # Should end with tool_use stop reason
        delta_events = [(t, d) for t, d in parsed if t == "message_delta"]
        assert delta_events[-1][1]["delta"]["stop_reason"] == "tool_use"

    def test_empty_deltas_ignored(self):
        state = StreamState("test")
        chunk = {
            "id": "chatcmpl-abc",
            "choices": [{"index": 0, "delta": {}, "finish_reason": None}],
        }
        events = openai_chunk_to_anthropic_events(chunk, state)
        assert len(events) == 0

    def test_usage_from_chunk(self):
        state = StreamState("test")
        # Force content started
        state.content_started = True
        chunk = {
            "id": "chatcmpl-abc",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {"completion_tokens": 99},
        }
        events = openai_chunk_to_anthropic_events(chunk, state)
        assert state.output_tokens == 99


class TestOllamaStreamConversion:

    def test_full_text_stream(self, sample_ollama_stream_chunks):
        state = StreamState("ollama/llama3")
        all_events = []
        for chunk in sample_ollama_stream_chunks:
            events = ollama_chunk_to_anthropic_events(chunk, state)
            all_events.extend(events)

        parsed = [parse_sse_event(e) for e in all_events]

        # First event should be content_block_start
        assert parsed[0][0] == "content_block_start"

        # Should have text deltas
        texts = [d["delta"]["text"] for _, d in parsed
                 if d.get("delta", {}).get("type") == "text_delta"]
        assert "Hello" in texts
        assert " there" in texts

        # Should end properly
        assert parsed[-1][0] == "message_stop"

    def test_ollama_with_tool_calls(self):
        state = StreamState("ollama/llama3")
        chunk = {
            "model": "llama3",
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [{
                    "function": {
                        "name": "get_weather",
                        "arguments": {"location": "London"},
                    },
                }],
            },
            "done": True,
            "eval_count": 10,
            "prompt_eval_count": 20,
        }
        events = ollama_chunk_to_anthropic_events(chunk, state)
        parsed = [parse_sse_event(e) for e in events]

        # Should have tool_use block
        starts = [d for t, d in parsed if t == "content_block_start"]
        assert any(s["content_block"]["type"] == "tool_use" for s in starts)

        # Stop reason should be tool_use
        deltas = [d for t, d in parsed if t == "message_delta"]
        assert deltas[0]["delta"]["stop_reason"] == "tool_use"

    def test_token_counts_from_done(self):
        state = StreamState("ollama/llama3")
        chunks = [
            {"model": "llama3", "message": {"role": "assistant", "content": "Hi"}, "done": False},
            {"model": "llama3", "message": {"role": "assistant", "content": ""}, "done": True,
             "eval_count": 42, "prompt_eval_count": 100},
        ]
        for chunk in chunks:
            ollama_chunk_to_anthropic_events(chunk, state)

        assert state.output_tokens == 42
        assert state.input_tokens == 100
