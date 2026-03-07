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
        oai = await call_openai_compat(self.base_url, self.key_fn(), payload, backend=self.name)
        return openai_response_to_anthropic(oai, f"{self.name}/{model.split('/')[-1]}")

    async def stream(self, body: dict, model: str, model_alias: str):
        if err := self.validate(model):
            raise HTTPException(status_code=500, detail=err)
        return await stream_openai_compat(
            self.base_url, self.key_fn(), body, model, model_alias, backend=self.name,
        )
