# SPDX-License-Identifier: Apache-2.0

import asyncio
import json

from multillm import streaming
from multillm.stream_utils import StreamTokenCounter


async def _collect(iterator):
    chunks = []
    async for chunk in iterator:
        chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
    return "".join(chunks)


class _FakeStreamResponse:
    def __init__(self, lines):
        self.lines = lines
        self.raised = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def raise_for_status(self):
        self.raised = True

    async def aiter_lines(self):
        for line in self.lines:
            yield line


class _FakeClient:
    def __init__(self, lines):
        self.response = _FakeStreamResponse(lines)
        self.calls = []

    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response


def test_openai_compat_stream_executes_http_and_filters_bad_events(monkeypatch):
    chunk = {
        "choices": [
            {"delta": {"content": "hello"}, "finish_reason": None}
        ]
    }
    client = _FakeClient(
        ["ignored", "data: not-json", f"data: {json.dumps(chunk)}", "data: [DONE]"]
    )
    monkeypatch.setattr(streaming, "get_client", lambda backend: client)

    response = asyncio.run(
        streaming.stream_openai_compat(
            "https://example.com",
            "test-key",
            {"messages": [{"role": "user", "content": "hi"}]},
            "gpt-4o",
            "openai/gpt-4o",
        )
    )
    output = asyncio.run(_collect(response.body_iterator))

    assert "hello" in output
    assert client.response.raised is True
    assert client.calls[0][1].endswith("/v1/chat/completions")
    assert client.calls[0][2]["json"]["stream"] is True


def test_ollama_stream_executes_http_and_skips_empty_invalid_lines(monkeypatch):
    lines = [
        "",
        "not-json",
        json.dumps(
            {
                "message": {"content": "local"},
                "done": True,
                "eval_count": 2,
                "prompt_eval_count": 1,
            }
        ),
    ]
    client = _FakeClient(lines)
    monkeypatch.setattr(streaming, "get_client", lambda backend: client)

    response = asyncio.run(
        streaming.stream_ollama(
            "http://localhost:11434",
            {"messages": [{"role": "user", "content": "hi"}]},
            "llama3",
            "ollama/llama3",
        )
    )
    output = asyncio.run(_collect(response.body_iterator))

    assert "local" in output
    assert "message_stop" in output
    assert client.calls[0][1].endswith("/api/chat")


def test_anthropic_passthrough_preserves_event_line_breaks(monkeypatch):
    client = _FakeClient(["event: ping", "data: {}", ""])
    monkeypatch.setattr(streaming, "get_client", lambda backend: client)

    response = asyncio.run(
        streaming.stream_anthropic_passthrough(
            "test-key", {"messages": [{"role": "user", "content": "hi"}]}
        )
    )
    output = asyncio.run(_collect(response.body_iterator))

    assert output == "event: ping\ndata: {}\n\n"
    assert client.calls[0][2]["json"]["stream"] is True


def test_stream_token_counter_handles_openai_usage_and_fires_once():
    events = [
        "data: "
        + json.dumps({"choices": [{"delta": {"content": "hello"}}]})
        + "\n",
        "data: "
        + json.dumps(
            {
                "choices": [{"delta": {}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 7},
            }
        )
        + "\n",
        "data: [DONE]\n",
    ]

    async def generate():
        for event in events:
            yield event

    calls = []
    counter = StreamTokenCounter(generate(), lambda *args: calls.append(args))
    output = asyncio.run(_collect(counter))

    assert "hello" in output
    assert len(calls) == 1
    assert calls[0][0:2] == (10, 7)
    counter._fire_completion_callback()
    assert len(calls) == 1


def test_stream_token_counter_handles_anthropic_and_ollama_usage():
    payloads = [
        {
            "type": "message_start",
            "message": {
                "usage": {
                    "input_tokens": 11,
                    "cache_read_input_tokens": 5,
                    "cache_creation_input_tokens": 2,
                }
            },
        },
        {"type": "content_block_delta", "delta": {"text": "answer"}},
        {"type": "message_delta", "usage": {"output_tokens": 9}},
        {"prompt_eval_count": 12, "eval_count": 10},
    ]

    async def generate():
        # Split the final line to exercise buffering across chunks.
        text = "".join(f"data: {json.dumps(payload)}\n" for payload in payloads)
        yield text[:-5]
        yield text[-5:]

    calls = []
    counter = StreamTokenCounter(
        generate(), lambda *args: calls.append(args), input_tokens=1
    )
    asyncio.run(_collect(counter))

    assert calls[0][0:2] == (12, 10)
    assert counter.cache_read_tokens == 5
    assert counter.cache_create_tokens == 2


def test_stream_counter_yields_trailing_buffer_and_swallows_callback_failure():
    async def generate():
        yield "trailing-without-newline"

    def broken_callback(*args):
        raise RuntimeError("callback failure")

    counter = StreamTokenCounter(generate(), broken_callback)
    assert asyncio.run(_collect(counter)) == "trailing-without-newline"
