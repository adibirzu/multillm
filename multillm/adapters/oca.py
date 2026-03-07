"""Oracle Code Assist (OCA) backend adapter."""

import json
import logging

from fastapi import HTTPException

from .base import BaseAdapter
from ..config import OCA_ENDPOINT, OCA_API_VERSION
from ..converters import build_openai_payload, openai_response_to_anthropic
from ..http_pool import get_client
from ..oca_auth import get_oca_bearer_token
from ..streaming import stream_oca

log = logging.getLogger("multillm.adapters.oca")


class OCAAdapter(BaseAdapter):
    name = "oca"

    def is_configured(self) -> bool:
        return bool(OCA_ENDPOINT)

    def validate(self, model: str) -> str | None:
        if not OCA_ENDPOINT:
            return "OCA_ENDPOINT not configured"
        return None

    async def send(self, body: dict, model: str, model_alias: str) -> dict:
        if err := self.validate(model):
            raise HTTPException(status_code=500, detail=err)

        token = await get_oca_bearer_token()
        if not token:
            raise HTTPException(
                status_code=401,
                detail="OCA not authenticated. Run OAuth flow or check ~/.oca/token.json",
            )

        payload = build_openai_payload(body, model)
        payload["stream"] = False
        # OCA: only send model + messages (strip everything else)
        payload = {"model": payload["model"], "messages": payload["messages"]}
        log.info("OCA request payload=%s", json.dumps(payload, default=str)[:1000])

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "client": "multillm-gateway",
            "client-version": "0.5.0",
        }

        url = f"{OCA_ENDPOINT}/{OCA_API_VERSION}/app/litellm/chat/completions"
        client = get_client("oca")
        r = await client.post(url, json=payload, headers=headers)
        log.info("OCA response status=%d content_type=%s body_len=%d",
                 r.status_code, r.headers.get("content-type", ""), len(r.content))
        if r.status_code != 200:
            log.error("OCA error %d: %s", r.status_code, r.text[:500])
        r.raise_for_status()

        # OCA may return SSE stream even when stream=false — handle both
        ct = r.headers.get("content-type", "")
        if "text/event-stream" in ct or r.text.startswith("data:"):
            text_parts = []
            for line in r.text.splitlines():
                if line.startswith("data:"):
                    chunk_str = line[5:].strip()
                    if chunk_str == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(chunk_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        if "content" in delta:
                            text_parts.append(delta["content"])
                    except json.JSONDecodeError:
                        continue
            data = {
                "id": "oca-response",
                "model": model,
                "choices": [{"message": {"role": "assistant", "content": "".join(text_parts)}}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
        else:
            data = r.json()

        return openai_response_to_anthropic(data, model if model.startswith("oca/") else f"oca/{model}")

    async def stream(self, body: dict, model: str, model_alias: str):
        if err := self.validate(model):
            raise HTTPException(status_code=500, detail=err)
        token = await get_oca_bearer_token()
        if not token:
            raise HTTPException(status_code=401, detail="OCA not authenticated")
        return await stream_oca(OCA_ENDPOINT, OCA_API_VERSION, token, body, model, model_alias)
