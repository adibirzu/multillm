# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Capability and model-level pricing registry."""

from __future__ import annotations

import time
from collections.abc import Iterable, Mapping
from typing import TypedDict

from .orchestration_contracts import (
    ExecutionMode,
    ModelPricing,
    ModelProfile,
    ModelProtocol,
    ModelTier,
    ReasoningEffort,
    TaskStrength,
    TaskType,
)


_LOCAL_PROVIDERS = {"ollama", "lmstudio", "claude_cli", "codex_cli", "gemini_cli", "antigravity"}

_PROVIDER_PRICING: dict[str, ModelPricing] = {
    "ollama": ModelPricing(),
    "lmstudio": ModelPricing(),
    "claude_cli": ModelPricing(),
    "codex_cli": ModelPricing(),
    "gemini_cli": ModelPricing(),
    "antigravity": ModelPricing(),
    "openai": ModelPricing(
        input_per_million=2.5,
        output_per_million=15.0,
        cached_read_per_million=0.25,
        reasoning_per_million=15.0,
    ),
    "anthropic": ModelPricing(
        input_per_million=3.0,
        output_per_million=15.0,
        cached_read_per_million=0.3,
        cache_write_per_million=3.75,
    ),
    "gemini": ModelPricing(input_per_million=0.075, output_per_million=0.30),
    "groq": ModelPricing(input_per_million=0.05, output_per_million=0.08),
    "deepseek": ModelPricing(input_per_million=0.27, output_per_million=1.10),
    "mistral": ModelPricing(input_per_million=2.0, output_per_million=6.0),
    "together": ModelPricing(input_per_million=0.88, output_per_million=0.88),
    "xai": ModelPricing(input_per_million=3.0, output_per_million=15.0),
    "fireworks": ModelPricing(input_per_million=0.90, output_per_million=0.90),
    "openrouter": ModelPricing(input_per_million=2.5, output_per_million=10.0),
    "azure_openai": ModelPricing(input_per_million=2.5, output_per_million=15.0),
    "bedrock": ModelPricing(input_per_million=3.0, output_per_million=15.0),
    "oci_genai": ModelPricing(input_per_million=0.10, output_per_million=0.10),
}

_MODEL_PRICING: dict[str, ModelPricing] = {
    "claude-fable-5": ModelPricing(
        input_per_million=10.0,
        output_per_million=50.0,
        cached_read_per_million=1.0,
        cache_write_per_million=12.5,
    ),
}

class _PreviewModelConfig(TypedDict):
    alias: str
    tier: ModelTier
    pricing: ModelPricing


_PREVIEW_MODELS: dict[str, _PreviewModelConfig] = {
    "gpt-5.6-luna": {
        "alias": "openai/luna",
        "tier": ModelTier.ECONOMY,
        "pricing": ModelPricing(
            input_per_million=1.0,
            output_per_million=6.0,
            cached_read_per_million=0.1,
            cache_write_per_million=1.25,
            reasoning_per_million=6.0,
        ),
    },
    "gpt-5.6-terra": {
        "alias": "openai/terra",
        "tier": ModelTier.BALANCED,
        "pricing": ModelPricing(
            input_per_million=2.5,
            output_per_million=15.0,
            cached_read_per_million=0.25,
            cache_write_per_million=3.125,
            reasoning_per_million=15.0,
        ),
    },
    "gpt-5.6-sol": {
        "alias": "openai/sol",
        "tier": ModelTier.FRONTIER,
        "pricing": ModelPricing(
            input_per_million=5.0,
            output_per_million=30.0,
            cached_read_per_million=0.5,
            cache_write_per_million=6.25,
            reasoning_per_million=30.0,
        ),
    },
}


def pricing_for(provider: str, provider_model_id: str = "") -> ModelPricing:
    """Return the most specific known pricing without enabling model routing."""
    if provider_model_id in _MODEL_PRICING:
        return _MODEL_PRICING[provider_model_id]
    preview = _PREVIEW_MODELS.get(provider_model_id)
    if preview is not None:
        return preview["pricing"]
    return _PROVIDER_PRICING.get(provider, ModelPricing())


def _protocol(provider: str, model: str) -> ModelProtocol:
    if provider.endswith("_cli") or provider == "antigravity":
        return ModelProtocol.CLI
    if provider == "anthropic":
        return ModelProtocol.ANTHROPIC
    if provider in {"openai", "azure_openai"} and model.startswith("gpt-5"):
        return ModelProtocol.RESPONSES
    return ModelProtocol.CHAT_COMPLETIONS


def _classify_route(provider: str, model: str) -> tuple[ModelTier, bool]:
    value = model.lower()
    if provider in _LOCAL_PROVIDERS:
        return ModelTier.LOCAL, True
    if any(term in value for term in ("mini", "flash", "small", "8b", "luna")):
        return ModelTier.ECONOMY, True
    if any(
        term in value
        for term in (
            "frontier",
            "fable",
            "opus",
            "405b",
            "reasoner",
            "gpt-5.5",
            "sol",
        )
    ):
        return ModelTier.FRONTIER, True
    if any(
        term in value
        for term in ("sonnet", "pro", "large", "70b", "120b", "gpt-4", "terra")
    ):
        return ModelTier.BALANCED, True
    return ModelTier.BALANCED, False


def _family(provider: str, model: str) -> str:
    lowered = model.lower()
    for family in ("gpt-5.6", "gpt-5", "gpt-4", "claude", "gemini", "llama", "qwen", "mistral", "deepseek"):
        if family in lowered:
            return family
    return f"{provider}:{lowered.split(':', 1)[0].split('/', 1)[-1]}"


def _profile_from_route(alias: str, route: Mapping[str, object]) -> ModelProfile:
    provider = str(route.get("backend") or alias.split("/", 1)[0]).lower()
    model = str(route.get("model") or alias)
    tier, recognized = _classify_route(provider, model)
    reasoning: tuple[ReasoningEffort, ...] = (
        ReasoningEffort.NONE,
        ReasoningEffort.LOW,
    )
    if any(
        token in model.lower()
        for token in ("fable", "gpt-5", "reasoner", "o1", "o3", "o4")
    ):
        reasoning = (
            ReasoningEffort.NONE,
            ReasoningEffort.LOW,
            ReasoningEffort.MEDIUM,
            ReasoningEffort.HIGH,
        )
    strengths = (
        (TaskStrength(task=TaskType.GENERAL, score=0.6, samples=0),)
        if recognized
        else ()
    )
    return ModelProfile(
        alias=alias,
        provider=provider,
        provider_model_id=model,
        family=_family(provider, model),
        tier=tier,
        protocol=_protocol(provider, model),
        available=True,
        verified_at=time.time() if route.get("discovered") else None,
        modalities=("text", "image") if any(x in model.lower() for x in ("gpt-4o", "gemini", "claude")) else ("text",),
        context_window=max(0, int(str(route.get("context_length") or 0))),
        supports_tools=provider not in _LOCAL_PROVIDERS or provider == "ollama",
        supports_structured_output=provider not in {"claude_cli", "codex_cli", "gemini_cli"},
        supports_state=_protocol(provider, model) is ModelProtocol.RESPONSES,
        reasoning_efforts=reasoning,
        task_strengths=strengths,
        pricing=pricing_for(provider, model),
        cache_behavior="provider_managed" if provider in {"openai", "anthropic"} else "none",
        auto_enabled=recognized,
    )


class ModelRegistry:
    """Read-only registry built from effective routes and discovery evidence."""

    def __init__(self, profiles: Iterable[ModelProfile]):
        ordered = tuple(profiles)
        self._profiles = ordered
        self._by_alias = {profile.alias: profile for profile in ordered}

    @classmethod
    def from_routes(
        cls,
        routes: Mapping[str, Mapping[str, object]],
        discovered_model_ids: set[str] | None = None,
    ) -> "ModelRegistry":
        profiles = [_profile_from_route(alias, route) for alias, route in routes.items()]
        discovered = discovered_model_ids or set()
        for model_id, config in _PREVIEW_MODELS.items():
            if model_id not in discovered:
                continue
            alias = str(config["alias"])
            profiles = [profile for profile in profiles if profile.alias != alias]
            tier = config["tier"]
            efforts = (
                ReasoningEffort.NONE,
                ReasoningEffort.LOW,
                ReasoningEffort.MEDIUM,
                ReasoningEffort.HIGH,
                ReasoningEffort.MAX,
            )
            profiles.append(
                ModelProfile(
                    alias=alias,
                    provider="openai",
                    provider_model_id=model_id,
                    family="gpt-5.6",
                    tier=tier,
                    protocol=ModelProtocol.RESPONSES,
                    available=True,
                    verified_at=time.time(),
                    modalities=("text", "image"),
                    supports_tools=True,
                    supports_structured_output=True,
                    supports_state=True,
                    reasoning_efforts=efforts,
                    execution_modes=(ExecutionMode.STANDARD, ExecutionMode.ULTRA),
                    task_strengths=(TaskStrength(task=TaskType.GENERAL, score=0.7),),
                    pricing=config["pricing"],
                    cache_behavior="explicit_breakpoints_30m",
                    auto_enabled=tier is not ModelTier.FRONTIER,
                    restrictions=("preview",),
                )
            )
        return cls(profiles)

    @property
    def profiles(self) -> tuple[ModelProfile, ...]:
        return self._profiles

    def get(self, alias: str) -> ModelProfile | None:
        return self._by_alias.get(alias)

    def require(self, alias: str) -> ModelProfile:
        profile = self.get(alias)
        if profile is None:
            raise KeyError(alias)
        return profile

    def estimate_cost(self, alias: str, **usage: int) -> float:
        return self.require(alias).pricing.estimate(**usage)

    def public_profiles(self) -> list[dict]:
        return [profile.model_dump(mode="json") for profile in self._profiles]
