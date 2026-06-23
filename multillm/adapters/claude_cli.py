# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Claude Code CLI backend adapter (subprocess-based).

Drives the Claude Code CLI in one-shot print mode
(``claude -p <prompt> --model <model> --output-format text``). This gives the
gateway a Claude backend that rides the user's existing Claude Code login — no
API key required.

SAFETY: ``claude`` is an agentic CLI. Run as a *completion* backend we want it to
answer, not act. Two guards make it a pure text completion:

* **Print mode is non-interactive.** Without ``--dangerously-skip-permissions``,
  tool calls (Bash/Edit/Write/…) need interactive approval, which cannot be given
  in ``-p`` mode, so they are denied — the model just answers.
* **Isolated working directory.** It runs in a throwaway temp dir, so even a
  permitted read/write has nothing of the gateway's to touch.
"""

import asyncio
import os
import tempfile

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from .base import BaseAdapter
from ..cli_tools import resolve_cli_binary
from ..converters import extract_text_from_anthropic, make_anthropic_response

# Default model alias when a route does not pin one. Claude CLI accepts short
# aliases ("sonnet"/"opus"/"haiku") or full model IDs.
DEFAULT_MODEL = "sonnet"


class ClaudeCLIAdapter(BaseAdapter):
    name = "claude_cli"

    def is_configured(self) -> bool:
        return resolve_cli_binary("claude", env_var="CLAUDE_CLI_PATH") is not None

    async def send(self, body: dict, model: str, model_alias: str) -> dict:
        prompt = extract_text_from_anthropic(body)
        if len(prompt) > 10000:
            prompt = prompt[:10000] + "\n...(truncated)"

        # Route model field carries "claude:<model>"; the bare form is also fine.
        selected = model.split(":", 1)[1] if model.startswith("claude:") else model
        selected = (
            selected or os.getenv("CLAUDE_CLI_DEFAULT_MODEL", DEFAULT_MODEL)
        ).strip()

        claude_bin = resolve_cli_binary("claude", env_var="CLAUDE_CLI_PATH")
        if not claude_bin:
            raise HTTPException(
                status_code=500,
                detail="Claude Code CLI not found. Install: npm i -g @anthropic-ai/claude-code",
            )

        try:
            with tempfile.TemporaryDirectory(prefix="claude-cli-") as workdir:
                proc = await asyncio.create_subprocess_exec(
                    claude_bin,
                    "-p",
                    prompt,
                    "--model",
                    selected,
                    "--output-format",
                    "text",
                    cwd=workdir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=504, detail="Claude Code CLI timed out after 300s"
            )

        text = stdout.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0 and not text:
            # Surface a real failure rather than passing stderr off as the answer,
            # so council/fusion can detect and degrade.
            err = stderr.decode("utf-8", errors="replace")[:300]
            raise HTTPException(
                status_code=502,
                detail=f"Claude Code CLI error (rc={proc.returncode}): {err}",
            )

        return make_anthropic_response(
            text=text,
            model=model_alias,
            input_tokens=len(prompt) // 4,
            output_tokens=len(text) // 4,
        )

    async def stream(self, body: dict, model: str, model_alias: str):
        result = await self.send(body, model, model_alias)
        return JSONResponse(result)
