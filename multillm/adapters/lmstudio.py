"""LM Studio backend adapter."""

from .base import BaseAdapter
from .openai_compat import call_openai_compat
from ..config import LMSTUDIO_URL
from ..converters import build_openai_payload, openai_response_to_anthropic
from ..streaming import stream_openai_compat


class LMStudioAdapter(BaseAdapter):
    name = "lmstudio"

    async def send(self, body: dict, model: str, model_alias: str) -> dict:
        payload = build_openai_payload(body, model)
        payload["stream"] = False
        oai = await call_openai_compat(LMSTUDIO_URL, "", payload, backend="lmstudio")
        return openai_response_to_anthropic(oai, model_alias)

    async def stream(self, body: dict, model: str, model_alias: str):
        return await stream_openai_compat(LMSTUDIO_URL, "", body, model, model_alias, backend="lmstudio")
