"""Ollama backend adapter."""

import uuid
from fastapi import HTTPException

from .base import BaseAdapter
from ..config import OLLAMA_URL
from ..converters import build_ollama_payload, make_anthropic_response
from ..http_pool import get_client
from ..streaming import stream_ollama


class OllamaAdapter(BaseAdapter):
    name = "ollama"

    async def send(self, body: dict, model: str, model_alias: str) -> dict:
        payload = build_ollama_payload(body, model)
        payload["stream"] = False

        client = get_client("ollama")
        r = await client.post(f"{OLLAMA_URL}/api/chat", json=payload)
        r.raise_for_status()
        data = r.json()

        message = data.get("message", {})
        content_blocks: list[dict] = []

        text = message.get("content", "")
        if text:
            content_blocks.append({"type": "text", "text": text})

        tool_calls = message.get("tool_calls", [])
        for tc in tool_calls:
            func = tc.get("function", {})
            content_blocks.append({
                "type": "tool_use",
                "id": f"toolu_{uuid.uuid4().hex[:24]}",
                "name": func.get("name", ""),
                "input": func.get("arguments", {}),
            })

        stop_reason = "tool_use" if tool_calls else "end_turn"
        if not content_blocks:
            content_blocks.append({"type": "text", "text": ""})

        return make_anthropic_response(
            text="", model=model,
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
            stop_reason=stop_reason,
            content_blocks=content_blocks,
        )

    async def stream(self, body: dict, model: str, model_alias: str):
        return await stream_ollama(OLLAMA_URL, body, model, model_alias)
