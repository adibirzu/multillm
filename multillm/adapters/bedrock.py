"""AWS Bedrock backend adapter."""

from fastapi import HTTPException
from fastapi.responses import JSONResponse

from .base import BaseAdapter
from ..config import AWS_BEDROCK_REGION, AWS_BEDROCK_PROFILE
from ..converters import extract_text_from_anthropic, make_anthropic_response


class BedrockAdapter(BaseAdapter):
    name = "bedrock"

    async def send(self, body: dict, model: str, model_alias: str) -> dict:
        try:
            import boto3
        except ImportError:
            raise HTTPException(status_code=500, detail="boto3 not installed. Run: pip install boto3")

        prompt = extract_text_from_anthropic(body)
        max_tokens = body.get("max_tokens", 4096)

        session_kwargs = {"region_name": AWS_BEDROCK_REGION}
        if AWS_BEDROCK_PROFILE:
            session_kwargs["profile_name"] = AWS_BEDROCK_PROFILE
        session = boto3.Session(**session_kwargs)
        bedrock = session.client("bedrock-runtime")

        messages = [{"role": "user", "content": [{"text": prompt}]}]
        system_text = body.get("system")
        system_param = [{"text": system_text}] if system_text else []

        try:
            kwargs = {
                "modelId": model,
                "messages": messages,
                "inferenceConfig": {"maxTokens": max_tokens, "temperature": body.get("temperature", 0.7)},
            }
            if system_param:
                kwargs["system"] = system_param
            response = bedrock.converse(**kwargs)
            text = response["output"]["message"]["content"][0]["text"]
            usage = response.get("usage", {})
            return make_anthropic_response(
                text=text, model=f"bedrock/{model.split('.')[-1]}",
                input_tokens=usage.get("inputTokens", 0),
                output_tokens=usage.get("outputTokens", 0),
            )
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Bedrock error: {e}")

    async def stream(self, body: dict, model: str, model_alias: str):
        # Bedrock uses boto3, no HTTP streaming — fall back to non-streaming
        result = await self.send(body, model, model_alias)
        return JSONResponse(result)
