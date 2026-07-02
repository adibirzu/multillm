import asyncio

import httpx
from pydantic import BaseModel

from multillm.evaluation.deepeval_adapter import GatewayDeepEvalModel


class _Schema(BaseModel):
    score: int


def test_deepeval_gateway_model_uses_selected_alias_and_schema():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["json"] = __import__("json").loads(request.content)
        seen["key"] = request.headers.get("x-api-key")
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": '{"score": 5}'}],
                "usage": {"input_tokens": 3, "output_tokens": 2},
            },
        )

    model = GatewayDeepEvalModel(
        gateway_url="http://gateway.test",
        alias="judge/model",
        api_key="test-key",
        transport=httpx.MockTransport(handler),
    )
    result = model.generate("Judge this", schema=_Schema)

    assert result == '{"score": 5}'
    assert seen["json"]["model"] == "judge/model"
    assert seen["json"]["output_schema"]["schema"]["properties"]["score"]
    assert seen["key"] == "test-key"
    assert model.get_model_name() == "gateway:judge/model"
    assert model.supports_structured_outputs() is True


def test_deepeval_gateway_model_async_path_matches_sync():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "async answer"}]},
        )

    model = GatewayDeepEvalModel(
        gateway_url="http://gateway.test",
        alias="judge/model",
        transport=httpx.MockTransport(handler),
    )
    assert asyncio.run(model.a_generate("Judge this")) == "async answer"
