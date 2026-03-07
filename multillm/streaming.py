"""
SSE/HTTP streaming support for the MultiLLM gateway.

Converts streaming responses from various backends (OpenAI, Ollama, Anthropic)
into Anthropic-compatible SSE format that Claude Code expects.
"""

import json
import logging
from typing import AsyncIterator, Optional

import httpx
from starlette.responses import StreamingResponse

from .http_pool import get_client
from .converters import (
    StreamState,
    build_openai_payload,
    build_ollama_payload,
    make_message_start_event,
    make_ping_event,
    openai_chunk_to_anthropic_events,
    ollama_chunk_to_anthropic_events,
    anthropic_messages_to_openai,
)

log = logging.getLogger("multillm.streaming")

ANTHROPIC_SSE_HEADERS = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


# ── OpenAI-compatible streaming ─────────────────────────────────────────────

async def stream_openai_compat(
    base_url: str,
    api_key: str,
    body: dict,
    model: str,
    model_alias: str,
    extra_headers: Optional[dict] = None,
    backend: str = "openai",
) -> StreamingResponse:
    """Stream from an OpenAI-compatible endpoint, converting to Anthropic SSE."""
    payload = build_openai_payload(body, model)
    payload["stream"] = True

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        **(extra_headers or {}),
    }

    async def generate() -> AsyncIterator[str]:
        state = StreamState(model_alias)
        yield make_message_start_event(state)
        yield make_ping_event()

        client = get_client(backend)
        async with client.stream(
            "POST",
            f"{base_url}/v1/chat/completions",
            json=payload,
            headers=headers,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                for event in openai_chunk_to_anthropic_events(chunk, state):
                    yield event

    return StreamingResponse(generate(), media_type="text/event-stream", headers=ANTHROPIC_SSE_HEADERS)


# ── Ollama streaming ────────────────────────────────────────────────────────

async def stream_ollama(
    ollama_url: str,
    body: dict,
    model: str,
    model_alias: str,
) -> StreamingResponse:
    """Stream from Ollama, converting to Anthropic SSE."""
    payload = build_ollama_payload(body, model)
    payload["stream"] = True

    async def generate() -> AsyncIterator[str]:
        state = StreamState(model_alias)
        yield make_message_start_event(state)
        yield make_ping_event()

        client = get_client("ollama")
        async with client.stream(
            "POST",
            f"{ollama_url}/api/chat",
            json=payload,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue

                for event in ollama_chunk_to_anthropic_events(chunk, state):
                    yield event

    return StreamingResponse(generate(), media_type="text/event-stream", headers=ANTHROPIC_SSE_HEADERS)


# ── Anthropic passthrough streaming ─────────────────────────────────────────

async def stream_anthropic_passthrough(
    api_key: str,
    body: dict,
) -> StreamingResponse:
    """Stream from Anthropic API, passing through SSE events directly."""
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    body_stream = {**body, "stream": True}

    async def generate() -> AsyncIterator[str]:
        client = get_client("anthropic")
        async with client.stream(
            "POST",
            "https://api.anthropic.com/v1/messages",
            json=body_stream,
            headers=headers,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line:
                    yield line + "\n"
                else:
                    yield "\n"

    return StreamingResponse(generate(), media_type="text/event-stream", headers=ANTHROPIC_SSE_HEADERS)


# ── OCA streaming ───────────────────────────────────────────────────────────

async def stream_oca(
    endpoint: str,
    api_version: str,
    token: str,
    body: dict,
    model: str,
    model_alias: str,
) -> StreamingResponse:
    """Stream from Oracle Code Assist, converting OpenAI SSE to Anthropic SSE."""
    payload = build_openai_payload(body, model)
    payload["stream"] = True

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
        "client": "multillm-gateway",
        "client-version": "0.1.0",
    }
    url = f"{endpoint}/{api_version}/app/litellm/chat/completions"

    async def generate() -> AsyncIterator[str]:
        state = StreamState(model_alias)
        yield make_message_start_event(state)
        yield make_ping_event()

        client = get_client("oca")
        async with client.stream("POST", url, json=payload, headers=headers) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                for event in openai_chunk_to_anthropic_events(chunk, state):
                    yield event

    return StreamingResponse(generate(), media_type="text/event-stream", headers=ANTHROPIC_SSE_HEADERS)


# ── Gemini streaming ────────────────────────────────────────────────────────

async def stream_gemini(
    api_key: str,
    body: dict,
    model: str,
    model_alias: str,
) -> StreamingResponse:
    """Stream from Gemini via the google-genai SDK (synchronous iteration wrapped in async)."""
    from .converters import extract_text_from_anthropic, make_content_block_start_event, \
        make_text_delta_event, make_content_block_stop_event, make_message_delta_event, \
        make_message_stop_event

    prompt = extract_text_from_anthropic(body)

    async def generate() -> AsyncIterator[str]:
        try:
            from google import genai
        except ImportError:
            yield make_message_start_event(StreamState(model_alias))
            yield make_content_block_start_event(0, "text")
            yield make_text_delta_event(0, "Error: google-genai package not installed")
            yield make_content_block_stop_event(0)
            yield make_message_delta_event("end_turn", 0)
            yield make_message_stop_event()
            return

        client = genai.Client(api_key=api_key)
        state = StreamState(model_alias)
        yield make_message_start_event(state)
        yield make_ping_event()

        block_started = False
        try:
            response = client.models.generate_content_stream(
                model=model,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    max_output_tokens=body.get("max_tokens", 4096),
                    temperature=body.get("temperature", 0.7),
                ),
            )
            for chunk in response:
                text = chunk.text or ""
                if text:
                    if not block_started:
                        yield make_content_block_start_event(0, "text")
                        block_started = True
                    yield make_text_delta_event(0, text)
                    state.output_tokens += max(1, len(text) // 4)
        except Exception as e:
            if not block_started:
                yield make_content_block_start_event(0, "text")
                block_started = True
            yield make_text_delta_event(0, f"\n\nStreaming error: {e}")

        if block_started:
            yield make_content_block_stop_event(0)
        yield make_message_delta_event("end_turn", state.output_tokens)
        yield make_message_stop_event()

    return StreamingResponse(generate(), media_type="text/event-stream", headers=ANTHROPIC_SSE_HEADERS)
