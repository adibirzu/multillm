"""Codex CLI backend adapter (subprocess-based)."""

import asyncio
import os
import re
import tomllib
from functools import lru_cache
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from .base import BaseAdapter
from ..cli_tools import resolve_cli_binary
from ..converters import extract_text_from_anthropic, make_anthropic_response

CODEX_CONFIG_FILE = Path.home() / ".codex" / "config.toml"


def _normalize_codex_model_name(value: str) -> str:
    """Translate route aliases like gpt-5-4 into CLI model names like gpt-5.4."""
    return re.sub(r"-(\d+)-(\d+)(?=-|$)", r"-\1.\2", (value or "").strip())


@lru_cache(maxsize=16)
def _load_codex_profiles_cached(path_str: str, mtime_ns: int, size: int) -> tuple[dict, str]:
    del mtime_ns, size

    try:
        with open(path_str, "rb") as f:
            payload = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return {}, ""

    profiles = payload.get("profiles", {})
    if not isinstance(profiles, dict):
        profiles = {}

    normalized_profiles = {
        str(name): cfg for name, cfg in profiles.items()
        if isinstance(cfg, dict)
    }
    default_profile = str(payload.get("profile") or "").strip()
    return normalized_profiles, default_profile


def _load_codex_profiles() -> tuple[dict, str]:
    if not CODEX_CONFIG_FILE.exists():
        return {}, ""
    try:
        stat = CODEX_CONFIG_FILE.stat()
    except OSError:
        return {}, ""
    return _load_codex_profiles_cached(str(CODEX_CONFIG_FILE), stat.st_mtime_ns, stat.st_size)


def _resolve_codex_exec_target(selector: str) -> tuple[list[str], str]:
    """Resolve a route selector to either a config profile or a direct model flag."""
    profiles, default_profile = _load_codex_profiles()
    target = (selector or "").strip()
    if not target:
        target = os.getenv("CODEX_DEFAULT_PROFILE", "").strip() or default_profile
        if target in profiles:
            model_name = str(profiles[target].get("model") or "").strip()
            return ["-p", target], model_name

    if target in profiles:
        model_name = str(profiles[target].get("model") or "").strip()
        return ["-p", target], model_name

    normalized_model = _normalize_codex_model_name(target)
    if default_profile and default_profile in profiles:
        default_cfg = profiles[default_profile]
        default_model = str(default_cfg.get("model") or "").strip()
        if normalized_model and default_model == normalized_model:
            return ["-p", default_profile], default_model

    for profile_name, profile_cfg in profiles.items():
        model_names = {
            str(profile_cfg.get("model") or "").strip(),
            str(profile_cfg.get("review_model") or "").strip(),
        }
        if normalized_model in model_names or target in model_names:
            return ["-p", profile_name], normalized_model or target
        dashed_target = target.replace(".", "-")
        if target and (
            profile_name == target
            or profile_name.endswith(target)
            or profile_name.endswith(dashed_target)
        ):
            model_name = str(profile_cfg.get("model") or "").strip()
            return ["-p", profile_name], model_name or normalized_model or target

    fallback_model = normalized_model or target or os.getenv("CODEX_DEFAULT_MODEL", "gpt-5.4")
    return ["-m", fallback_model], fallback_model


async def _run_codex_exec(prompt: str, sandbox: str, exec_target: list[str]) -> tuple[int, str, str]:
    codex_bin = resolve_cli_binary("codex", env_var="CODEX_CLI_PATH")
    if not codex_bin:
        raise FileNotFoundError("codex")

    proc = await asyncio.create_subprocess_exec(
        codex_bin, "exec", "--full-auto", "-s", sandbox, *exec_target, "-",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(
        proc.communicate(input=prompt.encode("utf-8")), timeout=180
    )
    return (
        proc.returncode,
        stdout.decode("utf-8", errors="replace").strip(),
        stderr.decode("utf-8", errors="replace").strip(),
    )


class CodexCLIAdapter(BaseAdapter):
    name = "codex_cli"

    async def send(self, body: dict, model: str, model_alias: str) -> dict:
        prompt = extract_text_from_anthropic(body)
        if len(prompt) > 10000:
            prompt = prompt[:10000] + "\n...(truncated)"

        selector = ""
        if model.startswith("codex:"):
            selector = model.split(":", 1)[1]
        exec_target, resolved_model = _resolve_codex_exec_target(selector)

        # Per-request sandbox override via metadata, else env var, else default
        metadata = body.get("metadata", {})
        sandbox = metadata.get("sandbox_mode") or os.getenv("CODEX_SANDBOX", "read-only")

        try:
            returncode, text, stderr = await _run_codex_exec(prompt, sandbox, exec_target)

            # Route aliases may target a model while the local machine only has a profile for it.
            if (
                returncode != 0
                and exec_target[:1] == ["-p"]
                and resolved_model
                and "config profile" in stderr.lower()
            ):
                returncode, text, stderr = await _run_codex_exec(prompt, sandbox, ["-m", resolved_model])

            if returncode != 0 and not text:
                text = f"Codex CLI error (rc={returncode}): {stderr[:500]}"
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
