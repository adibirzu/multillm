"""Gemini CLI backend adapter (subprocess-based)."""

import asyncio
import json
import os

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from .base import BaseAdapter
from ..converters import extract_text_from_anthropic, make_anthropic_response


class GeminiCLIAdapter(BaseAdapter):
    name = "gemini_cli"

    async def send(self, body: dict, model: str, model_alias: str) -> dict:
        prompt = extract_text_from_anthropic(body)
        if len(prompt) > 10000:
            prompt = prompt[:10000] + "\n...(truncated)"

        # Model selection: "gemini-cli:gemini-3-flash-preview" → "-m gemini-3-flash-preview"
        model_flag = []
        if model.startswith("gemini-cli:"):
            gemini_model = model.split(":", 1)[1]
            if gemini_model:
                model_flag = ["-m", gemini_model]

        gemini_bin = os.getenv("GEMINI_CLI_PATH", "gemini")

        # Per-request approval mode override via metadata, else env var, else yolo
        metadata = body.get("metadata", {})
        approval = metadata.get("sandbox_mode") or os.getenv("GEMINI_APPROVAL_MODE", "yolo")
        approval_flag = ["--approval-mode", approval] if approval != "yolo" else ["--yolo"]

        try:
            proc = await asyncio.create_subprocess_exec(
                gemini_bin, "-p", prompt, "-o", "json",
                *approval_flag, *model_flag,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
            raw = stdout.decode("utf-8", errors="replace").strip()

            # Gemini CLI may prefix MCP noise before the JSON — find the first '{'
            json_start = raw.find("{")
            if json_start >= 0:
                try:
                    data = json.loads(raw[json_start:])
                    text = data.get("response", "")
                    input_tokens = 0
                    output_tokens = 0
                    for m_stats in data.get("stats", {}).get("models", {}).values():
                        tokens = m_stats.get("tokens", {})
                        input_tokens += tokens.get("input", 0)
                        output_tokens += tokens.get("candidates", 0)
                except json.JSONDecodeError:
                    text = raw
                    input_tokens = len(prompt) // 4
                    output_tokens = len(text) // 4
            else:
                text = raw
                input_tokens = len(prompt) // 4
                output_tokens = len(text) // 4

            if proc.returncode != 0 and not text:
                text = f"Gemini CLI error (rc={proc.returncode}): {stderr.decode('utf-8', errors='replace')[:500]}"
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="Gemini CLI timed out after 180s")
        except FileNotFoundError:
            raise HTTPException(
                status_code=500,
                detail="Gemini CLI not found. Install: npm i -g @google/gemini-cli",
            )

        return make_anthropic_response(
            text=text, model=model_alias,
            input_tokens=input_tokens, output_tokens=output_tokens,
        )

    async def stream(self, body: dict, model: str, model_alias: str):
        result = await self.send(body, model, model_alias)
        return JSONResponse(result)
