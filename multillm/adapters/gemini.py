"""Google Gemini backend adapter."""

from fastapi import HTTPException

from .base import BaseAdapter
from ..config import GEMINI_KEY
from ..converters import extract_text_from_anthropic, make_anthropic_response
from ..streaming import stream_gemini


class GeminiAdapter(BaseAdapter):
    name = "gemini"

    def is_configured(self) -> bool:
        return bool(GEMINI_KEY)

    def validate(self, model: str) -> str | None:
        if not GEMINI_KEY:
            return "GEMINI_API_KEY or GOOGLE_API_KEY not set"
        return None

    async def send(self, body: dict, model: str, model_alias: str) -> dict:
        if err := self.validate(model):
            raise HTTPException(status_code=500, detail=err)

        try:
            from google import genai
        except ImportError:
            raise HTTPException(status_code=500, detail="google-genai package not installed")

        client = genai.Client(api_key=GEMINI_KEY)
        prompt = extract_text_from_anthropic(body)

        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=genai.types.GenerateContentConfig(
                    max_output_tokens=body.get("max_tokens", 4096),
                    temperature=body.get("temperature", 0.7),
                ),
            )
            text = response.text or ""
            input_tokens = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
            output_tokens = getattr(response.usage_metadata, "candidates_token_count", 0) or 0
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Gemini error: {e}")

        return make_anthropic_response(text, model, input_tokens, output_tokens)

    async def stream(self, body: dict, model: str, model_alias: str):
        if err := self.validate(model):
            raise HTTPException(status_code=500, detail=err)
        return await stream_gemini(GEMINI_KEY, body, model, model_alias)
