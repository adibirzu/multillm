"""Azure OpenAI backend adapter."""

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from .base import BaseAdapter
from ..config import AZURE_OPENAI_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_VERSION
from ..converters import build_openai_payload, openai_response_to_anthropic
from ..http_pool import get_client


class AzureOpenAIAdapter(BaseAdapter):
    name = "azure_openai"

    def is_configured(self) -> bool:
        return bool(AZURE_OPENAI_KEY and AZURE_OPENAI_ENDPOINT)

    def validate(self, model: str) -> str | None:
        if not AZURE_OPENAI_KEY or not AZURE_OPENAI_ENDPOINT:
            return "AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT required"
        return None

    async def send(self, body: dict, model: str, model_alias: str) -> dict:
        if err := self.validate(model):
            raise HTTPException(status_code=500, detail=err)
        payload = build_openai_payload(body, model)
        payload["stream"] = False
        url = (
            f"{AZURE_OPENAI_ENDPOINT}/openai/deployments/{model}"
            f"/chat/completions?api-version={AZURE_OPENAI_API_VERSION}"
        )
        headers = {"Content-Type": "application/json", "api-key": AZURE_OPENAI_KEY}
        client = get_client("azure_openai")
        r = await client.post(url, json=payload, headers=headers)
        r.raise_for_status()
        return openai_response_to_anthropic(r.json(), f"azure/{model}")

    async def stream(self, body: dict, model: str, model_alias: str):
        # Azure uses different URL pattern — fall back to non-streaming
        result = await self.send(body, model, model_alias)
        return JSONResponse(result)
