# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Tests for Google Antigravity CLI adapter."""

from unittest.mock import AsyncMock, patch
import pytest
from fastapi import HTTPException

from multillm.adapters.antigravity import AntigravityAdapter


class TestAntigravityAdapter:
    def test_is_configured_true(self):
        adapter = AntigravityAdapter()
        with patch(
            "multillm.adapters.antigravity.resolve_cli_binary",
            return_value="/usr/local/bin/agy",
        ) as mock_resolve:
            assert adapter.is_configured() is True
            mock_resolve.assert_called_once_with("agy", env_var="ANTIGRAVITY_CLI_PATH")

    def test_is_configured_false(self):
        adapter = AntigravityAdapter()
        with patch(
            "multillm.adapters.antigravity.resolve_cli_binary", return_value=None
        ):
            assert adapter.is_configured() is False

    @pytest.mark.asyncio
    async def test_send_success(self):
        adapter = AntigravityAdapter()
        body = {
            "messages": [{"role": "user", "content": "Hello world"}],
            "max_tokens": 100,
        }

        class FakeProcess:
            returncode = 0

            async def communicate(self):
                return b"Grounded response", b""

        mock_exec = AsyncMock(return_value=FakeProcess())

        with (
            patch(
                "multillm.adapters.antigravity.resolve_cli_binary",
                return_value="/usr/local/bin/agy",
            ),
            patch("asyncio.create_subprocess_exec", mock_exec),
        ):
            res = await adapter.send(body, "antigravity/flash", "antigravity/flash")

        assert res["content"][0]["text"] == "Grounded response"
        assert res["model"] == "antigravity/flash"

        # Verify CLI args passed
        mock_exec.assert_called_once()
        args = mock_exec.call_args[0]
        assert args[0] == "/usr/local/bin/agy"
        assert "-p" in args
        assert "Hello world" in args
        assert "--model" in args

    @pytest.mark.asyncio
    async def test_send_truncates_long_prompt(self):
        adapter = AntigravityAdapter()
        long_prompt = "x" * 15000
        body = {
            "messages": [{"role": "user", "content": long_prompt}],
            "max_tokens": 100,
        }

        class FakeProcess:
            returncode = 0

            async def communicate(self):
                return b"Response", b""

        mock_exec = AsyncMock(return_value=FakeProcess())

        with (
            patch(
                "multillm.adapters.antigravity.resolve_cli_binary",
                return_value="/usr/local/bin/agy",
            ),
            patch("asyncio.create_subprocess_exec", mock_exec),
        ):
            await adapter.send(body, "Gemini 3.5 Flash (Medium)", "antigravity/flash")

        mock_exec.assert_called_once()
        args = mock_exec.call_args[0]
        sent_prompt = args[2]
        assert len(sent_prompt) == 10000 + len("\n...(truncated)")
        assert sent_prompt.endswith("\n...(truncated)")

    @pytest.mark.asyncio
    async def test_send_missing_binary(self):
        adapter = AntigravityAdapter()
        body = {"messages": [{"role": "user", "content": "Hello"}]}

        with patch(
            "multillm.adapters.antigravity.resolve_cli_binary", return_value=None
        ):
            with pytest.raises(HTTPException) as exc_info:
                await adapter.send(
                    body, "Gemini 3.5 Flash (Medium)", "antigravity/flash"
                )
            assert exc_info.value.status_code == 500
            assert "Antigravity CLI (agy) not found" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_send_cli_error(self):
        adapter = AntigravityAdapter()
        body = {"messages": [{"role": "user", "content": "Hello"}]}

        class FakeProcess:
            returncode = 1

            async def communicate(self):
                return b"", b"Permissions error or model unavailable"

        mock_exec = AsyncMock(return_value=FakeProcess())

        with (
            patch(
                "multillm.adapters.antigravity.resolve_cli_binary",
                return_value="/usr/local/bin/agy",
            ),
            patch("asyncio.create_subprocess_exec", mock_exec),
        ):
            with pytest.raises(HTTPException) as exc_info:
                await adapter.send(
                    body, "Gemini 3.5 Flash (Medium)", "antigravity/flash"
                )
            assert exc_info.value.status_code == 502
            assert "Antigravity CLI error" in exc_info.value.detail
            assert "Permissions error" in exc_info.value.detail
