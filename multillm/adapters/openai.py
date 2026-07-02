# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""OpenAI direct backend adapter.

GPT-5 family requests use the Responses API so reasoning, state, verbosity,
structured output, and prompt-cache controls are request-scoped. Older models
retain the Chat Completions compatibility path.
"""

from __future__ import annotations

import json
import uuid

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from .base import BaseAdapter
from .openai_compat import call_openai_compat
from ..http_pool import get_client
from ..config import OPENAI_KEY
from ..converters import (
    StreamState,
    anthropic_messages_to_openai,
    anthropic_tools_to_openai,
    build_openai_payload,
    make_content_block_start_event,
    make_content_block_stop_event,
    make_message_delta_event,
    make_message_start_event,
    make_message_stop_event,
    make_text_delta_event,
    openai_response_to_anthropic,
)
from ..streaming import stream_openai_compat


_VERBOSITY_MAP = {
    "concise": "low",
    "low": "low",
    "balanced": "medium",
    "medium": "medium",
    "detailed": "high",
    "high": "high",
}


def should_use_responses(model: str) -> bool:
    return model.lower().startswith("gpt-5")


def _responses_tools(tools: list[dict]) -> list[dict]:
    converted = []
    for tool in anthropic_tools_to_openai(tools):
        function = tool["function"]
        converted.append(
            {
                "type": "function",
                "name": function["name"],
                "description": function.get("description", ""),
                "parameters": function.get("parameters", {}),
            }
        )
    return converted


def build_responses_payload(body: dict, model: str) -> dict:
    """Translate an Anthropic-style request to the OpenAI Responses shape."""
    payload: dict = {
        "model": model,
        "input": anthropic_messages_to_openai(
            body.get("messages", []), system=body.get("system")
        ),
        "max_output_tokens": int(body.get("max_tokens", 4096)),
        "stream": False,
    }
    controls = (body.get("metadata") or {}).get("multillm_execution") or {}
    effort = controls.get("reasoning_effort")
    if effort and effort != "none":
        payload["reasoning"] = {"effort": effort}
    text_config: dict = {}
    verbosity = _VERBOSITY_MAP.get(str(controls.get("verbosity") or "").lower())
    if verbosity:
        text_config["verbosity"] = verbosity
    output_schema = body.get("output_schema") or {}
    if isinstance(output_schema, dict) and isinstance(
        output_schema.get("schema"), dict
    ):
        if len(json.dumps(output_schema["schema"])) > 100_000:
            raise HTTPException(status_code=400, detail="output_schema is too large")
        text_config["format"] = {
            "type": "json_schema",
            "name": str(output_schema.get("name") or "multillm_output")[:64],
            "schema": output_schema["schema"],
            "strict": True,
        }
    if text_config:
        payload["text"] = text_config
    cache_key = controls.get("prompt_cache_key")
    if cache_key:
        payload["prompt_cache_key"] = str(cache_key)[:256]
    previous_response_id = controls.get("previous_response_id")
    if previous_response_id:
        payload["previous_response_id"] = str(previous_response_id)[:200]
    tools = body.get("tools")
    if tools:
        payload["tools"] = _responses_tools(tools)
    tool_choice = body.get("tool_choice")
    if tool_choice:
        choice_type = tool_choice.get("type", "auto")
        if choice_type == "any":
            payload["tool_choice"] = "required"
        elif choice_type == "none":
            payload["tool_choice"] = "none"
        elif choice_type == "tool":
            payload["tool_choice"] = {
                "type": "function",
                "name": tool_choice.get("name", ""),
            }
        else:
            payload["tool_choice"] = "auto"
    return payload


def _responses_content(response: dict) -> list[dict]:
    content: list[dict] = []
    if response.get("output_text"):
        content.append({"type": "text", "text": str(response["output_text"])})
    for item in response.get("output", []) or []:
        if item.get("type") == "message":
            for part in item.get("content", []) or []:
                if part.get("type") in {"output_text", "text"} and part.get("text"):
                    content.append({"type": "text", "text": str(part["text"])})
        elif item.get("type") == "function_call":
            try:
                arguments = json.loads(item.get("arguments") or "{}")
            except json.JSONDecodeError:
                arguments = {"raw": item.get("arguments") or ""}
            content.append(
                {
                    "type": "tool_use",
                    "id": item.get("call_id")
                    or item.get("id")
                    or f"toolu_{uuid.uuid4().hex[:24]}",
                    "name": item.get("name", "unknown"),
                    "input": arguments,
                }
            )
    if not content:
        content.append({"type": "text", "text": ""})
    # output_text is a convenience mirror of output content; avoid duplicates.
    deduplicated: list[dict] = []
    for block in content:
        if block not in deduplicated:
            deduplicated.append(block)
    return deduplicated


def responses_to_anthropic(response: dict, model_alias: str) -> dict:
    usage = response.get("usage") or {}
    input_details = usage.get("input_tokens_details") or {}
    output_details = usage.get("output_tokens_details") or {}
    content = _responses_content(response)
    has_tool = any(block.get("type") == "tool_use" for block in content)
    incomplete = response.get("status") == "incomplete"
    return {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "content": content,
        "model": model_alias,
        "stop_reason": "tool_use"
        if has_tool
        else ("max_tokens" if incomplete else "end_turn"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "cache_read_input_tokens": int(input_details.get("cached_tokens") or 0),
            "cache_creation_input_tokens": int(
                input_details.get("cache_write_tokens") or 0
            ),
            "reasoning_tokens": int(output_details.get("reasoning_tokens") or 0),
            "service_tier": response.get("service_tier"),
            "provider_model": response.get("model"),
        },
        "provider_response_id": response.get("id"),
    }


async def call_openai_responses(payload: dict) -> dict:
    client = get_client("openai")
    response = await client.post(
        "https://api.openai.com/v1/responses",
        json=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENAI_KEY}",
        },
    )
    response.raise_for_status()
    return response.json()


class OpenAIAdapter(BaseAdapter):
    name = "openai"

    def is_configured(self) -> bool:
        return bool(OPENAI_KEY)

    def validate(self, model: str) -> str | None:
        if not OPENAI_KEY:
            return "OPENAI_API_KEY not set"
        return None

    async def send(self, body: dict, model: str, model_alias: str) -> dict:
        if err := self.validate(model):
            raise HTTPException(status_code=500, detail=err)
        if should_use_responses(model):
            payload = build_responses_payload(body, model)
            response = await call_openai_responses(payload)
            return responses_to_anthropic(response, model_alias)
        payload = build_openai_payload(body, model)
        payload["stream"] = False
        oai = await call_openai_compat("https://api.openai.com", OPENAI_KEY, payload)
        return openai_response_to_anthropic(oai, model_alias)

    async def stream(self, body: dict, model: str, model_alias: str):
        if err := self.validate(model):
            raise HTTPException(status_code=500, detail=err)
        if should_use_responses(model):
            result = await self.send({**body, "stream": False}, model, model_alias)
            text = "".join(
                block.get("text", "")
                for block in result.get("content", [])
                if block.get("type") == "text"
            )
            usage = result.get("usage") or {}

            async def generate():
                state = StreamState(
                    model_alias, input_tokens=usage.get("input_tokens", 0)
                )
                yield make_message_start_event(state)
                yield make_content_block_start_event(0)
                yield make_text_delta_event(0, text)
                yield make_content_block_stop_event(0)
                yield make_message_delta_event(
                    result.get("stop_reason") or "end_turn",
                    usage.get("output_tokens", 0),
                )
                yield make_message_stop_event()

            return StreamingResponse(generate(), media_type="text/event-stream")
        return await stream_openai_compat(
            "https://api.openai.com",
            OPENAI_KEY,
            body,
            model,
            model_alias,
            backend="openai",
        )
