# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Google Antigravity (``agy``) CLI backend adapter.

Drives the Antigravity agentic CLI in one-shot print mode
(``agy -p <prompt> --model <model> --dangerously-skip-permissions``). Antigravity
fronts Gemini 3.x (Flash/Pro), Claude 4.6, and GPT-OSS, so this backend gives the
gateway access to those models through a single authenticated CLI — replacing the
separately-authenticated gemini-cli backend.
"""

import asyncio
import os
import tempfile

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from .base import BaseAdapter
from ..cli_tools import resolve_cli_binary
from ..converters import extract_text_from_anthropic, make_anthropic_response

# Default model when a route does not pin one. Antigravity model names are the
# display names from `agy models` (e.g. "Gemini 3.5 Flash (Medium)").
DEFAULT_MODEL = "Gemini 3.5 Flash (Medium)"


class AntigravityAdapter(BaseAdapter):
    name = "antigravity"

    def is_configured(self) -> bool:
        return resolve_cli_binary("agy", env_var="ANTIGRAVITY_CLI_PATH") is not None

    async def send(self, body: dict, model: str, model_alias: str) -> dict:
        prompt = extract_text_from_anthropic(body)
        if len(prompt) > 10000:
            prompt = prompt[:10000] + "\n...(truncated)"

        # The route's model field carries the Antigravity display name; the
        # `antigravity:<model>` form is also accepted for parity with other CLIs.
        selected = model.split(":", 1)[1] if model.startswith("antigravity:") else model
        selected = (
            selected or os.getenv("ANTIGRAVITY_DEFAULT_MODEL", DEFAULT_MODEL)
        ).strip()

        agy_bin = resolve_cli_binary("agy", env_var="ANTIGRAVITY_CLI_PATH")
        if not agy_bin:
            raise HTTPException(
                status_code=500,
                detail="Antigravity CLI (agy) not found. Install Antigravity and run `agy install`.",
            )

        # SAFETY: `agy` is an agentic CLI that reads/edits files and runs
        # commands in its working directory. As a completion backend we want it
        # to *answer*, not act on the gateway's files. Run it in a throwaway
        # empty directory and with --sandbox (terminal restrictions) so it has
        # nothing of ours to touch. --dangerously-skip-permissions keeps print
        # mode non-interactive; combined with the empty cwd + sandbox it is
        # contained.
        try:
            with tempfile.TemporaryDirectory(prefix="agy-") as workdir:
                proc = await asyncio.create_subprocess_exec(
                    agy_bin,
                    "-p",
                    prompt,
                    "--model",
                    selected,
                    "--sandbox",
                    "--dangerously-skip-permissions",
                    cwd=workdir,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=504, detail="Antigravity CLI timed out after 300s"
            )

        text = stdout.decode("utf-8", errors="replace").strip()
        if proc.returncode != 0 and not text:
            # Surface a genuine failure as an error rather than returning stderr
            # as the answer (so council/fusion can detect and degrade).
            err = stderr.decode("utf-8", errors="replace")[:300]
            raise HTTPException(
                status_code=502,
                detail=f"Antigravity CLI error (rc={proc.returncode}): {err}",
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
