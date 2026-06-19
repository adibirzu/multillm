# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""OCI Generative AI backend adapter.

Calls Oracle's managed Generative AI inference service (Cohere, Meta Llama,
Google Gemini, OpenAI gpt-oss) via the OCI Python SDK. Auth is an OCI config
profile (e.g. ``cap``); the compartment defaults to the profile's tenancy and
the service endpoint is derived from the region.

The OCI SDK is synchronous, so ``chat()`` runs in a worker thread to keep the
event loop responsive. Cohere models use the Cohere request shape; all others
use the generic (OpenAI-style messages) shape.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from ..config import (
    OCI_GENAI_PROFILE, OCI_GENAI_REGION, OCI_GENAI_COMPARTMENT_ID, OCI_GENAI_ENDPOINT,
)
from ..converters import extract_text_from_anthropic, make_anthropic_response
from .base import BaseAdapter

log = logging.getLogger("multillm.oci_genai")

_client = None
_compartment = None
_init_error: str | None = None


def _ensure_client():
    """Lazily build the OCI GenAI inference client; cache it (or the error)."""
    global _client, _compartment, _init_error
    if _client is not None or _init_error is not None:
        return _client
    try:
        import oci
        from oci.generative_ai_inference import GenerativeAiInferenceClient

        cfg = oci.config.from_file(profile_name=OCI_GENAI_PROFILE)
        cfg["region"] = OCI_GENAI_REGION
        _compartment = OCI_GENAI_COMPARTMENT_ID or cfg.get("tenancy")
        _client = GenerativeAiInferenceClient(cfg, service_endpoint=OCI_GENAI_ENDPOINT)
        log.info("OCI GenAI ready: profile=%s region=%s", OCI_GENAI_PROFILE, OCI_GENAI_REGION)
    except Exception as exc:  # missing SDK, bad profile, no endpoint, etc.
        _init_error = str(exc)
        log.warning("OCI GenAI not configured: %s", exc)
    return _client


def _build_request(model: str, prompt_messages: list[dict], max_tokens: int, temperature: float):
    """Build the vendor-appropriate OCI chat request from Anthropic messages."""
    from oci.generative_ai_inference.models import (
        CohereChatRequest, GenericChatRequest, Message, TextContent,
    )

    if model.startswith("cohere."):
        # Cohere shape: a single message string + prior turns as chat history.
        history = []
        last_user = ""
        for m in prompt_messages:
            role = m.get("role")
            text = _text_of(m)
            if role == "user":
                if last_user:
                    history.append({"role": "USER", "message": last_user})
                last_user = text
            elif role == "assistant":
                history.append({"role": "CHATBOT", "message": text})
        return CohereChatRequest(
            message=last_user or _join(prompt_messages),
            chat_history=history or None,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    # Generic (Meta / Google / OpenAI) shape: OpenAI-style messages.
    role_map = {"user": "USER", "assistant": "ASSISTANT", "system": "SYSTEM"}
    messages = [
        Message(role=role_map.get(m.get("role"), "USER"), content=[TextContent(text=_text_of(m))])
        for m in prompt_messages
        if _text_of(m)
    ]
    return GenericChatRequest(
        api_format="GENERIC", messages=messages,
        max_tokens=max_tokens, temperature=temperature,
    )


def _text_of(message: dict) -> str:
    """Extract plain text from an Anthropic message (string or content blocks)."""
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return str(content)


def _join(messages: list[dict]) -> str:
    return "\n".join(_text_of(m) for m in messages if _text_of(m))


def _extract_response_text(chat_response) -> str:
    """Pull the answer text out of a Cohere or generic OCI chat response."""
    # Cohere: chat_response.text. Generic: choices[0].message.content[0].text.
    text = getattr(chat_response, "text", None)
    if text:
        return text
    choices = getattr(chat_response, "choices", None) or []
    if choices:
        content = getattr(choices[0].message, "content", None) or []
        if content:
            return getattr(content[0], "text", "") or ""
    return ""


class OCIGenAIAdapter(BaseAdapter):
    name = "oci_genai"

    def is_configured(self) -> bool:
        return _ensure_client() is not None

    async def send(self, body: dict, model: str, model_alias: str) -> dict:
        client = _ensure_client()
        if client is None:
            raise HTTPException(status_code=503, detail=f"OCI GenAI not configured: {_init_error}")

        messages = body.get("messages") or [{"role": "user", "content": extract_text_from_anthropic(body)}]
        max_tokens = int(body.get("max_tokens", 1024))
        temperature = float(body.get("temperature", 0.7))

        from oci.generative_ai_inference.models import ChatDetails, OnDemandServingMode

        detail = ChatDetails(
            compartment_id=_compartment,
            serving_mode=OnDemandServingMode(model_id=model),
            chat_request=_build_request(model, messages, max_tokens, temperature),
        )
        try:
            resp = await asyncio.to_thread(client.chat, detail)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"OCI GenAI error: {str(exc)[:300]}")

        text = _extract_response_text(resp.data.chat_response)
        prompt_text = _join(messages)
        return make_anthropic_response(
            text=text, model=model_alias,
            input_tokens=len(prompt_text) // 4, output_tokens=len(text) // 4,
        )

    async def stream(self, body: dict, model: str, model_alias: str):
        # OCI GenAI supports streaming, but a single synthesized response keeps
        # the adapter simple; callers that asked for a stream still get JSON.
        result = await self.send(body, model, model_alias)
        return JSONResponse(result)
