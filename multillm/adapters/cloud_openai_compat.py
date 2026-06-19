# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Generic adapter for OpenAI-compatible cloud backends.

Handles: Groq, DeepSeek, Mistral, Together, xAI, Fireworks.
Each is an instance of CloudOpenAICompatAdapter with different config.
"""

from fastapi import HTTPException

from .base import BaseAdapter
from .openai_compat import call_openai_compat
from ..converters import build_openai_payload, openai_response_to_anthropic
from ..streaming import stream_openai_compat


class CloudOpenAICompatAdapter(BaseAdapter):
    """Adapter for any cloud backend with an OpenAI-compatible API."""

    def __init__(self, name: str, base_url: str, key_fn):
        self.name = name
        self.base_url = base_url
        self.key_fn = key_fn

    def is_configured(self) -> bool:
        return bool(self.key_fn())

    def validate(self, model: str) -> str | None:
        if not self.key_fn():
            return f"{self.name.upper()}_API_KEY not set"
        return None

    async def send(self, body: dict, model: str, model_alias: str) -> dict:
        if err := self.validate(model):
            raise HTTPException(status_code=500, detail=err)
        payload = build_openai_payload(body, model)
        payload["stream"] = False
        oai = await call_openai_compat(
            self.base_url, self.key_fn(), payload, backend=self.name
        )
        return openai_response_to_anthropic(oai, f"{self.name}/{model.split('/')[-1]}")

    async def stream(self, body: dict, model: str, model_alias: str):
        if err := self.validate(model):
            raise HTTPException(status_code=500, detail=err)
        return await stream_openai_compat(
            self.base_url,
            self.key_fn(),
            body,
            model,
            model_alias,
            backend=self.name,
        )


# ---------------------------------------------------------------------------
# Entry-point factories (Plan 02a-01 Task 3).
#
# Each factory returns a CloudOpenAICompatAdapter pre-configured for one of
# the six cloud_openai_compat family backends. Wired into the registry via
# `[project.entry-points."multillm.backends"]` in pyproject.toml.
#
# URLs and key bindings are lifted verbatim from
# multillm/adapters/setup.py:45-50 so behavior is identical to the legacy
# register_all_adapters() codepath.
#
# Inline config imports avoid circular-import risk at module load time.
# ---------------------------------------------------------------------------


def make_groq() -> "CloudOpenAICompatAdapter":
    from ..config import GROQ_KEY

    return CloudOpenAICompatAdapter(
        "groq", "https://api.groq.com/openai", lambda: GROQ_KEY
    )


def make_deepseek() -> "CloudOpenAICompatAdapter":
    from ..config import DEEPSEEK_KEY

    return CloudOpenAICompatAdapter(
        "deepseek", "https://api.deepseek.com", lambda: DEEPSEEK_KEY
    )


def make_mistral() -> "CloudOpenAICompatAdapter":
    from ..config import MISTRAL_KEY

    return CloudOpenAICompatAdapter(
        "mistral", "https://api.mistral.ai", lambda: MISTRAL_KEY
    )


def make_together() -> "CloudOpenAICompatAdapter":
    from ..config import TOGETHER_KEY

    return CloudOpenAICompatAdapter(
        "together", "https://api.together.xyz", lambda: TOGETHER_KEY
    )


def make_xai() -> "CloudOpenAICompatAdapter":
    from ..config import XAI_KEY

    return CloudOpenAICompatAdapter("xai", "https://api.x.ai", lambda: XAI_KEY)


def make_fireworks() -> "CloudOpenAICompatAdapter":
    from ..config import FIREWORKS_KEY

    return CloudOpenAICompatAdapter(
        "fireworks", "https://api.fireworks.ai/inference", lambda: FIREWORKS_KEY
    )
