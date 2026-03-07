"""OpenRouter backend adapter."""

from fastapi import HTTPException

from .base import BaseAdapter
from .openai_compat import call_openai_compat
from ..config import OPENROUTER_KEY
from ..converters import build_openai_payload, openai_response_to_anthropic
from ..streaming import stream_openai_compat

_EXTRA_HEADERS = {"HTTP-Referer": "https://multillm-gateway", "X-Title": "MultiLLM Gateway"}


class OpenRouterAdapter(BaseAdapter):
    name = "openrouter"

    def is_configured(self) -> bool:
        return bool(OPENROUTER_KEY)

    def validate(self, model: str) -> str | None:
        if not OPENROUTER_KEY:
            return "OPENROUTER_API_KEY not set"
        return None

    async def send(self, body: dict, model: str, model_alias: str) -> dict:
        if err := self.validate(model):
            raise HTTPException(status_code=500, detail=err)
        payload = build_openai_payload(body, model)
        payload["stream"] = False
        oai = await call_openai_compat(
            "https://openrouter.ai/api", OPENROUTER_KEY, payload,
            extra_headers=_EXTRA_HEADERS, backend="openrouter",
        )
        return openai_response_to_anthropic(oai, model_alias)

    async def stream(self, body: dict, model: str, model_alias: str):
        if err := self.validate(model):
            raise HTTPException(status_code=500, detail=err)
        return await stream_openai_compat(
            "https://openrouter.ai/api", OPENROUTER_KEY, body, model, model_alias,
            extra_headers=_EXTRA_HEADERS, backend="openrouter",
        )
