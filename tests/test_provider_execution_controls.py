# SPDX-License-Identifier: Apache-2.0

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from multillm.adapters import codex_cli, openai


def test_openai_adapter_dispatches_gpt5_to_responses(monkeypatch):
    response = {
        "id": "resp_1",
        "model": "gpt-5.5",
        "output_text": "response answer",
        "usage": {"input_tokens": 2, "output_tokens": 3},
    }
    call = AsyncMock(return_value=response)
    monkeypatch.setattr(openai, "OPENAI_KEY", "test-key")
    monkeypatch.setattr(openai, "call_openai_responses", call)
    adapter = openai.OpenAIAdapter()

    result = asyncio.run(
        adapter.send(
            {"messages": [{"role": "user", "content": "hello"}]},
            "gpt-5.5",
            "openai/gpt-5-5",
        )
    )

    assert result["content"][0]["text"] == "response answer"
    assert call.await_args.args[0]["model"] == "gpt-5.5"


def test_openai_adapter_retains_legacy_chat_path(monkeypatch):
    chat = {
        "choices": [{"message": {"content": "legacy"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 2, "completion_tokens": 1},
    }
    call = AsyncMock(return_value=chat)
    monkeypatch.setattr(openai, "OPENAI_KEY", "test-key")
    monkeypatch.setattr(openai, "call_openai_compat", call)

    result = asyncio.run(
        openai.OpenAIAdapter().send(
            {"messages": [{"role": "user", "content": "hello"}]},
            "gpt-4o",
            "openai/gpt-4o",
        )
    )

    assert result["content"][0]["text"] == "legacy"
    assert call.await_args.args[0] == "https://api.openai.com"


def test_openai_responses_parses_function_calls_and_invalid_arguments():
    response = {
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "checking"}],
            },
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "lookup",
                "arguments": "not-json",
            },
        ],
        "status": "incomplete",
        "usage": {},
    }

    result = openai.responses_to_anthropic(response, "openai/gpt-5")

    assert result["stop_reason"] == "tool_use"
    tool = next(block for block in result["content"] if block["type"] == "tool_use")
    assert tool["input"] == {"raw": "not-json"}


def test_openai_responses_payload_converts_tools_and_choice():
    payload = openai.build_responses_payload(
        {
            "messages": [{"role": "user", "content": "weather"}],
            "tools": [
                {
                    "name": "weather",
                    "description": "lookup",
                    "input_schema": {"type": "object", "properties": {}},
                }
            ],
            "tool_choice": {"type": "tool", "name": "weather"},
        },
        "gpt-5.5",
    )

    assert payload["tools"][0]["name"] == "weather"
    assert payload["tool_choice"] == {"type": "function", "name": "weather"}


def test_openai_responses_stream_preserves_anthropic_sse(monkeypatch):
    monkeypatch.setattr(openai, "OPENAI_KEY", "test-key")
    monkeypatch.setattr(
        openai.OpenAIAdapter,
        "send",
        AsyncMock(
            return_value={
                "content": [{"type": "text", "text": "streamed"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 2, "output_tokens": 3},
            }
        ),
    )

    response = asyncio.run(
        openai.OpenAIAdapter().stream({}, "gpt-5.5", "openai/gpt-5-5")
    )

    async def collect():
        chunks = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
        return "".join(chunks)

    stream = asyncio.run(collect())
    assert "streamed" in stream
    assert "message_stop" in stream


def test_openai_adapter_requires_api_key(monkeypatch):
    monkeypatch.setattr(openai, "OPENAI_KEY", "")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(openai.OpenAIAdapter().send({}, "gpt-5.5", "alias"))
    assert exc.value.status_code == 500


def test_codex_send_passes_execution_controls_and_returns_usage(monkeypatch):
    call = AsyncMock(return_value=(0, "codex answer", ""))
    monkeypatch.setattr(codex_cli, "_run_codex_exec", call)
    monkeypatch.setattr(
        codex_cli,
        "_resolve_codex_exec_target",
        lambda selector: (["-m", "gpt-5.5"], "gpt-5.5"),
    )

    result = asyncio.run(
        codex_cli.CodexCLIAdapter().send(
            {
                "messages": [{"role": "user", "content": "hello"}],
                "metadata": {
                    "sandbox_mode": "read-only",
                    "multillm_execution": {
                        "reasoning_effort": "low",
                        "verbosity": "concise",
                    },
                },
            },
            "codex:gpt-5-5",
            "codex/gpt-5-5",
        )
    )

    overrides = call.await_args.args[3]
    assert overrides == {
        "model_reasoning_effort": "low",
        "model_verbosity": "low",
    }
    assert result["content"][0]["text"] == "codex answer"


def test_codex_send_retries_missing_profile_with_direct_model(monkeypatch):
    call = AsyncMock(
        side_effect=[
            (1, "", "Config profile not found"),
            (0, "fallback answer", ""),
        ]
    )
    monkeypatch.setattr(codex_cli, "_run_codex_exec", call)
    monkeypatch.setattr(
        codex_cli,
        "_resolve_codex_exec_target",
        lambda selector: (["-p", "missing"], "gpt-5.5"),
    )

    result = asyncio.run(
        codex_cli.CodexCLIAdapter().send(
            {"messages": [{"role": "user", "content": "hello"}]},
            "codex:gpt-5-5",
            "codex/gpt-5-5",
        )
    )

    assert call.await_count == 2
    assert call.await_args_list[1].args[2] == ["-m", "gpt-5.5"]
    assert result["content"][0]["text"] == "fallback answer"


def test_codex_send_surfaces_process_failure(monkeypatch):
    monkeypatch.setattr(
        codex_cli, "_run_codex_exec", AsyncMock(return_value=(2, "", "boom"))
    )
    monkeypatch.setattr(
        codex_cli, "_resolve_codex_exec_target", lambda selector: (["-m", "gpt"], "gpt")
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            codex_cli.CodexCLIAdapter().send(
                {"messages": [{"role": "user", "content": "hello"}]},
                "codex:gpt",
                "codex/gpt",
            )
        )
    assert exc.value.status_code == 502


def test_codex_run_exec_ignores_unapproved_config_keys(monkeypatch):
    captured = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self, input=None):
            return b"ok", b""

    async def fake_exec(*args, **kwargs):
        captured["args"] = args
        return FakeProcess()

    monkeypatch.setattr(codex_cli, "resolve_cli_binary", lambda *a, **k: "/codex")
    monkeypatch.setattr(codex_cli.asyncio, "create_subprocess_exec", fake_exec)

    asyncio.run(
        codex_cli._run_codex_exec(
            "hello",
            "read-only",
            ["-m", "gpt"],
            {"model_reasoning_effort": "low", "dangerous_key": json.dumps("x")},
        )
    )

    assert "dangerous_key" not in " ".join(captured["args"])


def test_codex_request_can_tighten_but_not_loosen_server_sandbox(monkeypatch):
    monkeypatch.setenv("CODEX_SANDBOX", "workspace-write")
    assert codex_cli._resolve_codex_sandbox("read-only") == "read-only"
    monkeypatch.setenv("CODEX_SANDBOX", "read-only")
    assert codex_cli._resolve_codex_sandbox("danger-full-access") == "read-only"
    assert codex_cli._resolve_codex_sandbox("not-a-mode") == "read-only"
