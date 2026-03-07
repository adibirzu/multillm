"""Anthropic direct backend adapter."""

from fastapi import HTTPException

from .base import BaseAdapter
from ..config import ANTHROPIC_KEY
from ..http_pool import get_client
from ..streaming import stream_anthropic_passthrough


class AnthropicAdapter(BaseAdapter):
    name = "anthropic"

    def is_configured(self) -> bool:
        return bool(ANTHROPIC_KEY)

    def validate(self, model: str) -> str | None:
        if not ANTHROPIC_KEY:
            return "ANTHROPIC_REAL_KEY not set"
        return None

    async def send(self, body: dict, model: str, model_alias: str) -> dict:
        if err := self.validate(model):
            raise HTTPException(status_code=500, detail=err)
        headers = {
            "x-api-key": ANTHROPIC_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        body_copy = {**body, "model": model, "stream": False}
        client = get_client("anthropic")
        r = await client.post("https://api.anthropic.com/v1/messages", json=body_copy, headers=headers)
        r.raise_for_status()
        return r.json()

    async def stream(self, body: dict, model: str, model_alias: str):
        if err := self.validate(model):
            raise HTTPException(status_code=500, detail=err)
        return await stream_anthropic_passthrough(ANTHROPIC_KEY, {**body, "model": model})
