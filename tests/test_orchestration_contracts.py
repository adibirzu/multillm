# SPDX-License-Identifier: Apache-2.0

import pytest
from pydantic import ValidationError

from multillm.orchestration_contracts import (
    ExecutionMode,
    ModelPricing,
    OrchestrationPolicy,
    ReasoningEffort,
)


def test_policy_rejects_unknown_fields_and_noncritical_max_reasoning():
    with pytest.raises(ValidationError):
        OrchestrationPolicy.model_validate({"unexpected": True})

    with pytest.raises(ValidationError):
        OrchestrationPolicy.model_validate({"reasoning_ceiling": "max"})


def test_critical_policy_is_required_for_max_and_ultra():
    policy = OrchestrationPolicy.model_validate(
        {
            "preset": "critical",
            "reasoning_ceiling": "max",
            "execution_mode": "ultra",
            "max_cost_usd": 2.0,
        }
    )

    assert policy.reasoning_ceiling is ReasoningEffort.MAX
    assert policy.execution_mode is ExecutionMode.ULTRA


def test_policy_is_frozen_and_normalizes_provider_allowlist():
    policy = OrchestrationPolicy.model_validate(
        {"allowed_providers": [" OpenAI ", "anthropic", "openai"]}
    )

    assert policy.allowed_providers == ("openai", "anthropic")
    with pytest.raises(ValidationError):
        policy.max_cost_usd = 99

    with pytest.raises(ValidationError):
        OrchestrationPolicy.model_validate(
            {"allowed_providers": ["openai", "bad provider!"]}
        )


def test_model_pricing_accounts_for_cache_reads_writes_and_reasoning():
    pricing = ModelPricing(
        input_per_million=2.0,
        output_per_million=10.0,
        cached_read_per_million=0.2,
        cache_write_per_million=2.5,
        reasoning_per_million=10.0,
    )

    assert pricing.estimate(
        input_tokens=1_000_000,
        output_tokens=100_000,
        cached_read_tokens=500_000,
        cache_write_tokens=200_000,
        reasoning_tokens=50_000,
    ) == pytest.approx(4.1)
