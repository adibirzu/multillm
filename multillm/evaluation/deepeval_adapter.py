"""DeepEval 4 adapter that judges through an authenticated MultiLLM alias."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
from deepeval.models import DeepEvalBaseLLM


class GatewayDeepEvalModel(DeepEvalBaseLLM):
    """Expose any gateway alias through DeepEval's custom-model interface."""

    def __init__(
        self,
        *,
        gateway_url: str,
        alias: str,
        api_key: str | None = None,
        timeout: float = 180,
        transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
    ):
        self.gateway_url = gateway_url.rstrip("/")
        self.alias = alias
        self.api_key = api_key
        self.timeout = timeout
        self.transport = transport
        super().__init__(model=alias)

    def load_model(self):
        return self

    def get_model_name(self) -> str:
        return f"gateway:{self.alias}"

    def supports_structured_outputs(self) -> bool:
        return True

    def supports_json_mode(self) -> bool:
        return True

    def supports_temperature(self) -> bool:
        return True

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self.api_key} if self.api_key else {}

    def _payload(self, prompt: str, schema: type | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.alias,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 2_048,
            "temperature": 0,
            "metadata": {
                "multillm": {"reasoning_ceiling": "medium"},
                "multillm_execution": {
                    "reasoning_effort": "medium",
                    "verbosity": "concise",
                },
            },
        }
        if schema is not None and hasattr(schema, "model_json_schema"):
            payload["output_schema"] = {
                "name": getattr(schema, "__name__", "deepeval_result"),
                "schema": schema.model_json_schema(),
            }
        return payload

    @staticmethod
    def _text(payload: dict[str, Any]) -> str:
        content = payload.get("content") or []
        text = next(
            (
                str(block.get("text") or "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ),
            "",
        )
        if not text.strip():
            raise ValueError("DeepEval judge returned an empty gateway response")
        return text

    def generate(self, prompt: str, schema: type | None = None, **_: Any) -> str:
        transport = (
            self.transport if isinstance(self.transport, httpx.BaseTransport) else None
        )
        with httpx.Client(timeout=self.timeout, transport=transport) as client:
            response = client.post(
                f"{self.gateway_url}/v1/messages",
                headers=self._headers(),
                json=self._payload(prompt, schema),
            )
            response.raise_for_status()
            return self._text(response.json())

    async def a_generate(
        self, prompt: str, schema: type | None = None, **_: Any
    ) -> str:
        transport = (
            self.transport
            if isinstance(self.transport, httpx.AsyncBaseTransport)
            else None
        )
        async with httpx.AsyncClient(
            timeout=self.timeout, transport=transport
        ) as client:
            response = await client.post(
                f"{self.gateway_url}/v1/messages",
                headers=self._headers(),
                json=self._payload(prompt, schema),
            )
            response.raise_for_status()
            return self._text(response.json())


@dataclass(frozen=True)
class DeepEvalMetricResult:
    name: str
    score: float
    passed: bool
    reason: str
    evaluation_model: str


async def evaluate_geval(
    *,
    prompt: str,
    output: str,
    criteria: str,
    judge: GatewayDeepEvalModel,
    metric_name: str = "MultiLLM response quality",
    threshold: float = 0.5,
) -> DeepEvalMetricResult:
    """Run a real DeepEval GEval metric and return a persistence-safe result."""
    from deepeval.metrics import GEval
    from deepeval.test_case import LLMTestCase, SingleTurnParams

    metric = GEval(
        name=metric_name,
        criteria=criteria,
        evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT],
        model=judge,
        threshold=threshold,
        async_mode=True,
    )
    score = float(
        await metric.a_measure(
            LLMTestCase(input=prompt, actual_output=output),
            _show_indicator=False,
            _log_metric_to_confident=False,
        )
    )
    return DeepEvalMetricResult(
        name=metric_name,
        score=score,
        passed=bool(metric.is_successful()),
        reason=str(metric.reason or ""),
        evaluation_model=judge.get_model_name(),
    )
