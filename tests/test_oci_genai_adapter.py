# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Tests for the OCI Generative AI adapter (request shaping + response parsing)."""

import asyncio
import types

import pytest

from multillm.adapters import oci_genai


def test_text_of_handles_string_and_blocks():
    assert oci_genai._text_of({"content": "hi"}) == "hi"
    assert oci_genai._text_of({"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}) == "a b"


def test_build_request_uses_cohere_shape_for_cohere_models():
    from oci.generative_ai_inference.models import CohereChatRequest
    req = oci_genai._build_request(
        "cohere.command-a-03-2025",
        [{"role": "user", "content": "hello"}], 100, 0.5,
    )
    assert isinstance(req, CohereChatRequest)
    assert req.message == "hello"


def test_build_request_uses_generic_shape_for_meta_models():
    from oci.generative_ai_inference.models import GenericChatRequest
    req = oci_genai._build_request(
        "meta.llama-3.3-70b-instruct",
        [{"role": "user", "content": "hello"}], 100, 0.5,
    )
    assert isinstance(req, GenericChatRequest)
    assert req.messages[0].role == "USER"


def test_build_request_maps_assistant_to_cohere_history():
    req = oci_genai._build_request(
        "cohere.command-r-plus-08-2024",
        [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}, {"role": "user", "content": "q2"}],
        100, 0.5,
    )
    assert req.message == "q2"
    roles = [h["role"] for h in (req.chat_history or [])]
    assert "USER" in roles and "CHATBOT" in roles


def test_extract_response_text_cohere_and_generic():
    cohere_resp = types.SimpleNamespace(text="cohere answer", choices=None)
    assert oci_genai._extract_response_text(cohere_resp) == "cohere answer"

    inner = types.SimpleNamespace(message=types.SimpleNamespace(content=[types.SimpleNamespace(text="generic answer")]))
    generic_resp = types.SimpleNamespace(text=None, choices=[inner])
    assert oci_genai._extract_response_text(generic_resp) == "generic answer"


def test_send_builds_detail_and_parses_response(monkeypatch):
    captured = {}

    class _FakeClient:
        def chat(self, detail):
            captured["detail"] = detail
            chat_response = types.SimpleNamespace(text="hello from oci", choices=None)
            return types.SimpleNamespace(data=types.SimpleNamespace(chat_response=chat_response))

    monkeypatch.setattr(oci_genai, "_ensure_client", lambda: _FakeClient())
    monkeypatch.setattr(oci_genai, "_compartment", "ocid1.tenancy.test")

    adapter = oci_genai.OCIGenAIAdapter()
    result = asyncio.run(adapter.send(
        {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 50},
        "cohere.command-a-03-2025", "oci/cohere-command-a",
    ))
    assert result["content"][0]["text"] == "hello from oci"
    assert result["model"] == "oci/cohere-command-a"
    # the serving mode targets the requested model id
    assert captured["detail"].serving_mode.model_id == "cohere.command-a-03-2025"


def test_send_raises_when_unconfigured(monkeypatch):
    monkeypatch.setattr(oci_genai, "_ensure_client", lambda: None)
    monkeypatch.setattr(oci_genai, "_init_error", "no profile")
    from fastapi import HTTPException
    adapter = oci_genai.OCIGenAIAdapter()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(adapter.send({"messages": [{"role": "user", "content": "hi"}]}, "meta.llama-3.3-70b-instruct", "oci/llama"))
    assert exc.value.status_code == 503
