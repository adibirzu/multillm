"""Opt-in live MoA comparison through the local MultiLLM gateway."""

from __future__ import annotations

import os
import asyncio

import httpx
import pytest

from gateway_compare import configured_alias, live_aliases, message, moa, read_json
from multillm.evaluation.deepeval_adapter import GatewayDeepEvalModel, evaluate_geval


pytestmark = pytest.mark.skipif(
    os.getenv("DEEPEVAL_E2E") != "1",
    reason="live DeepEval is opt-in",
)


def test_same_prompt_models_then_moa_last():
    """Generate the same prompt everywhere, then score every output with DeepEval."""
    models = read_json("models.json")
    case = read_json("cases.json")["cases"][0]
    gateway_url = os.getenv("MULTILLM_GATEWAY_URL", "http://127.0.0.1:8080")
    judge_alias = os.getenv(models["judge_alias_env"], "").strip()
    if not judge_alias:
        pytest.skip(f"set {models['judge_alias_env']} to an independent live judge")

    with httpx.Client(timeout=180) as client:
        catalog = client.get(f"{gateway_url}/api/models/catalog").json()
        targets = live_aliases(catalog, models["targets"])
        if not targets:
            pytest.skip("no live aliases discovered")

        responses: dict[str, str] = {}
        for target in targets:
            alias = configured_alias(target)
            try:
                answer = message(client, gateway_url, alias, case["command"], target["reasoning_ceiling"])
            except httpx.HTTPError:
                continue
            if answer.strip():
                responses[alias] = answer

        if not responses:
            pytest.skip("no alias returned a usable response")

        aggregator = os.getenv(
            models["moa"]["aggregator_alias_env"],
            models["moa"]["default_aggregator_alias"],
        )
        final = moa(
            client,
            gateway_url,
            case["command"],
            list(responses),
            aggregator,
            models["moa"]["preset"],
        )
        assert final.strip()
        responses["moa/quality"] = final

    judge = GatewayDeepEvalModel(
        gateway_url=gateway_url,
        alias=judge_alias,
        api_key=os.getenv("MULTILLM_API_KEY") or None,
    )
    for label, output in responses.items():
        metric = asyncio.run(
            evaluate_geval(
                prompt=case["command"],
                output=output,
                criteria=case["criteria"],
                judge=judge,
                metric_name=f"FinOps quality · {label}",
                threshold=0.5,
            )
        )
        assert metric.passed, f"{label}: {metric.score:.3f} — {metric.reason}"
