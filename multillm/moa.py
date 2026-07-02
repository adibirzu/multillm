# SPDX-License-Identifier: Apache-2.0

"""Layered Mixture of Agents orchestration with bounded anonymous context."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


QueryFn = Callable[[str, str, int, float], Awaitable[dict[str, Any]]]

# The built-in roster keeps the default MoA vendor-diverse while making Claude
# a first-class proposer and the final synthesizer. Callers can still replace
# either role explicitly.
DEFAULT_PROPOSER_MODELS = (
    "claude-cli/sonnet",
    "codex/gpt-5-5",
    "gemini-cli/flash",
)
DEFAULT_AGGREGATOR_MODEL = "claude-cli/opus"


def _recursive(alias: str) -> bool:
    normalized = alias.strip().lower()
    return normalized in {"moa", "fusion", "auto"} or normalized.startswith(
        ("moa/", "fusion/", "auto/")
    )


class MoAConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    proposer_models: tuple[str, ...]
    refiner_layers: tuple[tuple[str, ...], ...] = ()
    aggregator_model: str
    max_tokens: int = Field(default=4096, ge=128, le=131_072)
    temperature: float = Field(default=0.2, ge=0, le=2)
    max_context_chars: int = Field(default=48_000, ge=100, le=2_000_000)
    per_call_timeout_seconds: float = Field(default=180, gt=0, le=3_600)

    @field_validator("proposer_models", mode="before")
    @classmethod
    def normalize_proposers(cls, value: Any) -> tuple[str, ...]:
        if not isinstance(value, (list, tuple)):
            raise ValueError("proposer_models must be an array")
        return tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))

    @field_validator("refiner_layers", mode="before")
    @classmethod
    def normalize_layers(cls, value: Any) -> tuple[tuple[str, ...], ...]:
        if value in (None, ""):
            return ()
        if not isinstance(value, (list, tuple)):
            raise ValueError("refiner_layers must be an array of model arrays")
        layers: list[tuple[str, ...]] = []
        for layer in value:
            if not isinstance(layer, (list, tuple)):
                raise ValueError("every refiner layer must be an array")
            normalized = tuple(
                dict.fromkeys(str(item).strip() for item in layer if str(item).strip())
            )
            if not normalized:
                raise ValueError("refiner layers cannot be empty")
            layers.append(normalized)
        if len(layers) > 4:
            raise ValueError("at most four refiner layers are allowed")
        return tuple(layers)

    @field_validator("aggregator_model")
    @classmethod
    def normalize_aggregator(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("aggregator_model is required")
        return normalized

    @model_validator(mode="after")
    def validate_roles(self):
        if len(self.proposer_models) < 2:
            raise ValueError("MoA requires at least two proposer models")
        aliases = [
            *self.proposer_models,
            *(alias for layer in self.refiner_layers for alias in layer),
            self.aggregator_model,
        ]
        if any(_recursive(alias) for alias in aliases):
            raise ValueError("recursive MoA, Fusion, or Auto aliases are not allowed")
        return self


class AggregationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis: dict[str, Any] = Field(default_factory=dict)
    final_answer: str = Field(min_length=1, max_length=500_000)
    confidence: float = Field(ge=0, le=1)


def _bounded_response_blocks(
    responses: Sequence[dict[str, Any]], max_context_chars: int
) -> str:
    usable = [item for item in responses if str(item.get("text", "")).strip()]
    if not usable:
        return ""
    remaining = max_context_chars
    blocks: list[str] = []
    for index, response in enumerate(usable, 1):
        slots_left = len(usable) - index + 1
        allowance = max(1, remaining // slots_left)
        text = str(response.get("text", "")).strip()
        alias = str(response.get("alias", "")).strip()
        if alias:
            text = text.replace(alias, "[source]")
        excerpt = text[:allowance]
        remaining -= len(excerpt)
        blocks.append(f"--- Response {index} ---\n{excerpt}")
    return "\n\n".join(blocks)


def build_layer_prompt(
    *,
    user_prompt: str,
    responses: Sequence[dict[str, Any]],
    role: str,
    max_context_chars: int,
) -> str:
    blocks = _bounded_response_blocks(responses, max_context_chars)
    if role == "aggregator":
        instructions = (
            "Synthesize the strongest supported answer, resolve contradictions, and "
            "do not mention the response panel. Return one JSON object only:\n"
            '{"analysis":{"consensus":[],"contradictions":[],"blind_spots":[]},'
            '"final_answer":"...","confidence":0.0}'
        )
    else:
        instructions = (
            "Improve the candidate answers. Check correctness, expose contradictions, "
            "preserve useful minority evidence, and produce a self-contained refined answer. "
            "Do not mention model or provider identities."
        )
    return (
        f"You are a {role} in a layered Mixture of Agents.\n{instructions}\n\n"
        f"USER REQUEST:\n{user_prompt.strip()}\n\n"
        f"ANONYMOUS RESPONSES:\n{blocks}"
    )


def _parse_aggregation(text: str) -> AggregationResult:
    candidate = text.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", candidate, flags=re.DOTALL)
    if fence:
        candidate = fence.group(1)
    return AggregationResult.model_validate(json.loads(candidate))


async def _query_stage(
    models: Sequence[str],
    prompt_for_model: Callable[[str], str],
    *,
    config: MoAConfig,
    query_fn: QueryFn,
) -> list[dict[str, Any]]:
    async def call(alias: str) -> dict[str, Any]:
        try:
            result = await asyncio.wait_for(
                query_fn(alias, prompt_for_model(alias), config.max_tokens, config.temperature),
                timeout=config.per_call_timeout_seconds,
            )
            return {**result, "alias": result.get("alias") or alias}
        except TimeoutError:
            return {"alias": alias, "text": "", "error": "timeout"}
        except Exception as exc:
            return {"alias": alias, "text": "", "error": type(exc).__name__}

    return list(await asyncio.gather(*(call(alias) for alias in models)))


def _succeeded(results: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        item
        for item in results
        if not item.get("error") and str(item.get("text", "")).strip()
    ]


def _stage_summary(stage: str, results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "stage": stage,
        "queried": len(results),
        "succeeded": len(_succeeded(results)),
        "models": [
            {
                "alias": item.get("alias"),
                "status": "failed" if item.get("error") else "succeeded",
                "error": item.get("error"),
                "inputTokens": item.get("inputTokens", 0),
                "outputTokens": item.get("outputTokens", 0),
                "latencyMs": item.get("latencyMs"),
                "costUSD": item.get("actualCostUSD"),
            }
            for item in results
        ],
    }


def _totals(all_results: Sequence[dict[str, Any]]) -> dict[str, Any]:
    return {
        "modelsQueried": len(all_results),
        "modelsSucceeded": len(_succeeded(all_results)),
        "inputTokens": sum(int(item.get("inputTokens", 0) or 0) for item in all_results),
        "outputTokens": sum(int(item.get("outputTokens", 0) or 0) for item in all_results),
        "reasoningTokens": sum(int(item.get("reasoningTokens", 0) or 0) for item in all_results),
        "actualCostUSD": round(
            sum(float(item.get("actualCostUSD", 0) or 0) for item in all_results), 6
        ),
    }


def _timed_totals(
    all_results: Sequence[dict[str, Any]], *, started_at: float
) -> dict[str, Any]:
    return {
        **_totals(all_results),
        "criticalPathMs": round((time.perf_counter() - started_at) * 1_000, 3),
    }


async def run_moa(*, prompt: str, config: MoAConfig, query_fn: QueryFn) -> dict[str, Any]:
    """Execute proposer layers, refiners, and one structured final aggregator."""
    started_at = time.perf_counter()
    stages: list[dict[str, Any]] = []
    all_results: list[dict[str, Any]] = []

    current = await _query_stage(
        config.proposer_models,
        lambda _alias: prompt,
        config=config,
        query_fn=query_fn,
    )
    all_results.extend(current)
    stages.append(_stage_summary("proposer", current))
    current_success = _succeeded(current)
    if not current_success:
        return {
            "kind": "moa",
            "status": "failed",
            "finalAnswer": "",
            "analysis": {},
            "confidence": 0.0,
            "degradedReason": "no_proposer_succeeded",
            "stages": stages,
            "totals": _timed_totals(all_results, started_at=started_at),
        }

    for index, layer in enumerate(config.refiner_layers, 1):
        layer_prompt = build_layer_prompt(
            user_prompt=prompt,
            responses=current_success,
            role="refiner",
            max_context_chars=config.max_context_chars,
        )
        current = await _query_stage(
            layer,
            lambda _alias, value=layer_prompt: value,
            config=config,
            query_fn=query_fn,
        )
        all_results.extend(current)
        stages.append(_stage_summary(f"refiner_{index}", current))
        if _succeeded(current):
            current_success = _succeeded(current)

    aggregator_prompt = build_layer_prompt(
        user_prompt=prompt,
        responses=current_success,
        role="aggregator",
        max_context_chars=config.max_context_chars,
    )
    aggregator_results = await _query_stage(
        (config.aggregator_model,),
        lambda _alias: aggregator_prompt,
        config=config,
        query_fn=query_fn,
    )
    all_results.extend(aggregator_results)
    stages.append(_stage_summary("aggregator", aggregator_results))
    aggregator = aggregator_results[0]
    try:
        if aggregator.get("error"):
            raise ValueError("aggregator failed")
        parsed = _parse_aggregation(str(aggregator.get("text", "")))
    except (ValueError, json.JSONDecodeError):
        fallback = max(
            current_success,
            key=lambda item: float(item.get("qualityScore", 0.0) or 0.0),
        )
        return {
            "kind": "moa",
            "status": "degraded",
            "finalAnswer": fallback["text"],
            "analysis": {},
            "confidence": float(fallback.get("qualityScore", 0.0) or 0.0),
            "degradedReason": "aggregator_failed",
            "stages": stages,
            "totals": _timed_totals(all_results, started_at=started_at),
        }
    return {
        "kind": "moa",
        "status": "completed",
        "finalAnswer": parsed.final_answer,
        "analysis": parsed.analysis,
        "confidence": parsed.confidence,
        "stages": stages,
        "totals": _timed_totals(all_results, started_at=started_at),
    }
