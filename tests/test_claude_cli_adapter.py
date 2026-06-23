# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Tests for the Claude Code CLI adapter (`claude -p` print mode)."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from multillm.adapters.claude_cli import ClaudeCLIAdapter


class TestClaudeCLIAdapter:
    def test_is_configured_true(self):
        adapter = ClaudeCLIAdapter()
        with patch(
            "multillm.adapters.claude_cli.resolve_cli_binary",
            return_value="/opt/homebrew/bin/claude",
        ) as mock_resolve:
            assert adapter.is_configured() is True
            mock_resolve.assert_called_once_with("claude", env_var="CLAUDE_CLI_PATH")

    def test_is_configured_false(self):
        adapter = ClaudeCLIAdapter()
        with patch(
            "multillm.adapters.claude_cli.resolve_cli_binary", return_value=None
        ):
            assert adapter.is_configured() is False

    @pytest.mark.asyncio
    async def test_send_success_passes_model_and_print_mode(self):
        adapter = ClaudeCLIAdapter()
        body = {"messages": [{"role": "user", "content": "Hello world"}]}

        class FakeProcess:
            returncode = 0

            async def communicate(self):
                return b"Hi from claude", b""

        mock_exec = AsyncMock(return_value=FakeProcess())
        with (
            patch(
                "multillm.adapters.claude_cli.resolve_cli_binary",
                return_value="/opt/homebrew/bin/claude",
            ),
            patch("asyncio.create_subprocess_exec", mock_exec),
        ):
            res = await adapter.send(body, "claude:haiku", "claude-cli/haiku")

        assert res["content"][0]["text"] == "Hi from claude"
        assert res["model"] == "claude-cli/haiku"

        args = mock_exec.call_args[0]
        assert args[0] == "/opt/homebrew/bin/claude"
        assert "-p" in args
        assert "Hello world" in args
        # model prefix stripped: "claude:haiku" -> "haiku"
        assert "--model" in args
        assert args[args.index("--model") + 1] == "haiku"
        # print mode is non-interactive (no skip-permissions / no agentic flags)
        assert "--dangerously-skip-permissions" not in args
        # runs in an isolated temp cwd, not the gateway's directory
        assert mock_exec.call_args.kwargs["cwd"].startswith("/")

    @pytest.mark.asyncio
    async def test_send_truncates_long_prompt(self):
        adapter = ClaudeCLIAdapter()
        body = {"messages": [{"role": "user", "content": "x" * 15000}]}

        class FakeProcess:
            returncode = 0

            async def communicate(self):
                return b"ok", b""

        mock_exec = AsyncMock(return_value=FakeProcess())
        with (
            patch(
                "multillm.adapters.claude_cli.resolve_cli_binary",
                return_value="/opt/homebrew/bin/claude",
            ),
            patch("asyncio.create_subprocess_exec", mock_exec),
        ):
            await adapter.send(body, "claude:sonnet", "claude-cli/sonnet")

        sent_prompt = mock_exec.call_args[0][2]
        assert len(sent_prompt) == 10000 + len("\n...(truncated)")
        assert sent_prompt.endswith("\n...(truncated)")

    @pytest.mark.asyncio
    async def test_send_missing_binary(self):
        adapter = ClaudeCLIAdapter()
        body = {"messages": [{"role": "user", "content": "Hello"}]}
        with patch(
            "multillm.adapters.claude_cli.resolve_cli_binary", return_value=None
        ):
            with pytest.raises(HTTPException) as exc_info:
                await adapter.send(body, "claude:sonnet", "claude-cli/sonnet")
            assert exc_info.value.status_code == 500
            assert "Claude Code CLI not found" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_send_cli_error_surfaces_502(self):
        adapter = ClaudeCLIAdapter()
        body = {"messages": [{"role": "user", "content": "Hello"}]}

        class FakeProcess:
            returncode = 1

            async def communicate(self):
                return b"", b"not logged in"

        mock_exec = AsyncMock(return_value=FakeProcess())
        with (
            patch(
                "multillm.adapters.claude_cli.resolve_cli_binary",
                return_value="/opt/homebrew/bin/claude",
            ),
            patch("asyncio.create_subprocess_exec", mock_exec),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await adapter.send(body, "claude:sonnet", "claude-cli/sonnet")
            assert exc_info.value.status_code == 502
            assert "Claude Code CLI error" in exc_info.value.detail
            assert "not logged in" in exc_info.value.detail
