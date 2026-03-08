"""Codex CLI backend adapter (subprocess-based)."""

import asyncio
import os

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from .base import BaseAdapter
from ..converters import extract_text_from_anthropic, make_anthropic_response


class CodexCLIAdapter(BaseAdapter):
    name = "codex_cli"

    async def send(self, body: dict, model: str, model_alias: str) -> dict:
        prompt = extract_text_from_anthropic(body)
        if len(prompt) > 10000:
            prompt = prompt[:10000] + "\n...(truncated)"

        # Determine profile from route model field (e.g. "codex:gpt-5-4" → "-p gpt-5-4")
        if model.startswith("codex:"):
            profile = model.split(":", 1)[1]
        else:
            profile = os.getenv("CODEX_DEFAULT_PROFILE", "gpt-5-4")

        # Per-request sandbox override via metadata, else env var, else default
        metadata = body.get("metadata", {})
        sandbox = metadata.get("sandbox_mode") or os.getenv("CODEX_SANDBOX", "read-only")

        try:
            proc = await asyncio.create_subprocess_exec(
                "codex", "exec", "--full-auto", "-s", sandbox, "-p", profile, "-",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=prompt.encode("utf-8")), timeout=180
            )
            text = stdout.decode("utf-8", errors="replace").strip()
            if proc.returncode != 0 and not text:
                text = f"Codex CLI error (rc={proc.returncode}): {stderr.decode('utf-8', errors='replace')[:500]}"
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Codex CLI timed out after 180s")
        except FileNotFoundError:
            raise HTTPException(status_code=500, detail="Codex CLI not found. Install: npm i -g @openai/codex")

        return make_anthropic_response(
            text=text, model=model_alias,
            input_tokens=len(prompt) // 4, output_tokens=len(text) // 4,
        )

    async def stream(self, body: dict, model: str, model_alias: str):
        result = await self.send(body, model, model_alias)
        return JSONResponse(result)
