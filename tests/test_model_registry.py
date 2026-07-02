# SPDX-License-Identifier: Apache-2.0

from multillm.model_registry import ModelRegistry
from multillm.orchestration_contracts import ModelTier, ReasoningEffort


def test_preview_models_are_exposed_only_when_discovered():
    routes = {"openai/gpt-4o": {"backend": "openai", "model": "gpt-4o"}}
    without_access = ModelRegistry.from_routes(routes, discovered_model_ids=set())
    assert without_access.get("openai/luna") is None

    with_access = ModelRegistry.from_routes(
        routes, discovered_model_ids={"gpt-5.6-luna", "gpt-5.6-terra"}
    )
    luna = with_access.get("openai/luna")
    terra = with_access.get("openai/terra")

    assert luna is not None and luna.provider_model_id == "gpt-5.6-luna"
    assert luna.tier is ModelTier.ECONOMY
    assert terra is not None and terra.tier is ModelTier.BALANCED
    assert with_access.get("openai/sol") is None


def test_preview_pricing_and_critical_capabilities_match_discovered_tier():
    registry = ModelRegistry.from_routes(
        {}, discovered_model_ids={"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"}
    )

    sol = registry.require("openai/sol")
    assert sol.pricing.input_per_million == 5.0
    assert sol.pricing.output_per_million == 30.0
    assert ReasoningEffort.MAX in sol.reasoning_efforts
    assert "ultra" in {mode.value for mode in sol.execution_modes}
    assert sol.auto_enabled is False


def test_unknown_route_uses_conservative_profile_and_provider_pricing_fallback():
    registry = ModelRegistry.from_routes(
        {"vendor/mystery": {"backend": "openai", "model": "mystery-v1"}}
    )

    profile = registry.require("vendor/mystery")
    assert profile.tier is ModelTier.BALANCED
    assert profile.auto_enabled is False
    assert profile.pricing.input_per_million > 0
    assert profile.task_strengths == ()


def test_registry_estimates_model_specific_cost():
    registry = ModelRegistry.from_routes(
        {}, discovered_model_ids={"gpt-5.6-luna", "gpt-5.6-sol"}
    )

    luna = registry.estimate_cost(
        "openai/luna", input_tokens=1_000_000, output_tokens=1_000_000
    )
    sol = registry.estimate_cost(
        "openai/sol", input_tokens=1_000_000, output_tokens=1_000_000
    )
    assert luna == 7.0
    assert sol == 35.0


def test_claude_fable_is_a_frontier_claude_capability():
    registry = ModelRegistry.from_routes(
        {"claude-fable": {"backend": "anthropic", "model": "claude-fable-5"}}
    )

    fable = registry.require("claude-fable")
    assert fable.family == "claude"
    assert fable.tier is ModelTier.FRONTIER
    assert fable.auto_enabled is True
    assert fable.pricing.input_per_million == 10.0
    assert fable.pricing.output_per_million == 50.0
    assert fable.pricing.cached_read_per_million == 1.0
