# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Strict, immutable contracts for adaptive orchestration."""

from __future__ import annotations

from enum import Enum
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class ModelTier(str, Enum):
    LOCAL = "local"
    ECONOMY = "economy"
    BALANCED = "balanced"
    FRONTIER = "frontier"


class ModelProtocol(str, Enum):
    ANTHROPIC = "anthropic"
    CHAT_COMPLETIONS = "chat_completions"
    RESPONSES = "responses"
    CLI = "cli"


class ReasoningEffort(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class ExecutionMode(str, Enum):
    STANDARD = "standard"
    ULTRA = "ultra"


class TaskType(str, Enum):
    FACTUAL = "factual"
    RESEARCH = "research"
    CODING = "coding"
    DEBUGGING = "debugging"
    ARCHITECTURE = "architecture"
    SUMMARIZATION = "summarization"
    EXTRACTION = "extraction"
    MULTIMODAL = "multimodal"
    TOOL_USE = "tool_use"
    GENERAL = "general"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ModelPricing(FrozenModel):
    """USD rates per million tokens."""

    input_per_million: float = Field(default=0.0, ge=0)
    output_per_million: float = Field(default=0.0, ge=0)
    cached_read_per_million: float = Field(default=0.0, ge=0)
    cache_write_per_million: float = Field(default=0.0, ge=0)
    reasoning_per_million: float = Field(default=0.0, ge=0)

    def estimate(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        reasoning_tokens: int = 0,
    ) -> float:
        weighted = (
            input_tokens * self.input_per_million
            + output_tokens * self.output_per_million
            + cached_read_tokens * self.cached_read_per_million
            + cache_write_tokens * self.cache_write_per_million
            + reasoning_tokens * self.reasoning_per_million
        )
        return weighted / 1_000_000


class TaskStrength(FrozenModel):
    task: TaskType
    score: float = Field(ge=0, le=1)
    samples: int = Field(default=0, ge=0)


class ModelProfile(FrozenModel):
    alias: str = Field(min_length=1, max_length=200)
    provider: str = Field(min_length=1, max_length=80)
    provider_model_id: str = Field(min_length=1, max_length=200)
    family: str = Field(min_length=1, max_length=100)
    tier: ModelTier
    protocol: ModelProtocol
    available: bool = True
    verified_at: float | None = None
    modalities: tuple[str, ...] = ("text",)
    context_window: int = Field(default=0, ge=0)
    max_output_tokens: int = Field(default=0, ge=0)
    supports_tools: bool = False
    supports_structured_output: bool = False
    supports_streaming: bool = True
    supports_state: bool = False
    reasoning_efforts: tuple[ReasoningEffort, ...] = (ReasoningEffort.NONE,)
    execution_modes: tuple[ExecutionMode, ...] = (ExecutionMode.STANDARD,)
    task_strengths: tuple[TaskStrength, ...] = ()
    pricing: ModelPricing = ModelPricing()
    cache_behavior: str = "none"
    data_residency: tuple[str, ...] = ()
    restrictions: tuple[str, ...] = ()
    auto_enabled: bool = True

    def task_score(self, task_type: TaskType) -> float:
        for strength in self.task_strengths:
            if strength.task is task_type:
                return strength.score
        return 0.5


class OrchestrationPolicy(FrozenModel):
    preset: str = "balanced"
    max_cost_usd: float = Field(default=1.0, gt=0, le=10_000)
    max_latency_ms: int = Field(default=30_000, gt=0, le=3_600_000)
    reasoning_ceiling: ReasoningEffort = ReasoningEffort.HIGH
    execution_mode: ExecutionMode = ExecutionMode.STANDARD
    require_sources: bool = False
    allowed_providers: tuple[str, ...] = ()
    require_vendor_diversity: bool = True
    retain_content: bool = False
    shadow: bool = False

    @field_validator("preset")
    @classmethod
    def validate_preset(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"economy", "balanced", "quality", "critical"}:
            raise ValueError("preset must be economy, balanced, quality, or critical")
        return normalized

    @field_validator("allowed_providers", mode="before")
    @classmethod
    def normalize_providers(cls, value: Any) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        if not isinstance(value, (list, tuple, set)):
            raise ValueError("allowed_providers must be an array")
        ordered: list[str] = []
        if len(value) > 32:
            raise ValueError("allowed_providers accepts at most 32 providers")
        for provider in value:
            normalized = str(provider).strip().lower()
            if not normalized:
                raise ValueError("provider names cannot be empty")
            if len(normalized) > 80 or not re.fullmatch(r"[a-z0-9_-]+", normalized):
                raise ValueError("provider names contain unsupported characters")
            if normalized not in ordered:
                ordered.append(normalized)
        return tuple(ordered)

    @model_validator(mode="after")
    def critical_controls_require_critical_preset(self):
        if self.reasoning_ceiling is ReasoningEffort.MAX and self.preset != "critical":
            raise ValueError("max reasoning requires the critical preset")
        if self.execution_mode is ExecutionMode.ULTRA and self.preset != "critical":
            raise ValueError("ultra execution requires the critical preset")
        return self


class TaskProfile(FrozenModel):
    task_type: TaskType = TaskType.GENERAL
    domains: tuple[str, ...] = ()
    freshness_required: bool = False
    risk: RiskLevel = RiskLevel.MEDIUM
    complexity: float = Field(default=0.5, ge=0, le=1)
    required_capabilities: tuple[str, ...] = ("text",)
    validators: tuple[str, ...] = ("nonempty",)
    prompt_injection_signals: tuple[str, ...] = ()


class VerifierVerdict(FrozenModel):
    correctness: float = Field(ge=0, le=1)
    completeness: float = Field(ge=0, le=1)
    evidence_support: float = Field(ge=0, le=1)
    uncertainty: float = Field(ge=0, le=1)
    defects: tuple[str, ...] = ()
    accepted: bool = False

    @property
    def confidence(self) -> float:
        return max(
            0.0,
            min(
                1.0,
                (self.correctness + self.completeness + self.evidence_support) / 3
                - self.uncertainty * 0.25,
            ),
        )


class StageResult(FrozenModel):
    stage: str
    model: str | None = None
    provider: str | None = None
    tier: ModelTier | None = None
    effort: ReasoningEffort = ReasoningEffort.NONE
    status: str
    accepted: bool = False
    confidence: float = Field(default=0, ge=0, le=1)
    estimated_cost_usd: float = Field(default=0, ge=0)
    actual_cost_usd: float = Field(default=0, ge=0)
    latency_ms: float = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    error: str | None = None


class DecisionTrace(FrozenModel):
    run_id: str
    policy: OrchestrationPolicy
    task: TaskProfile
    selected_models: tuple[str, ...] = ()
    skipped_models: tuple[str, ...] = ()
    stages: tuple[StageResult, ...] = ()
    early_exit_reason: str | None = None


class ModelScorecard(FrozenModel):
    model: str
    task_type: TaskType
    quality_mean: float = Field(default=0.5, ge=0, le=1)
    reliability_mean: float = Field(default=0.8, ge=0, le=1)
    avg_latency_ms: float = Field(default=5_000, ge=0)
    avg_cost_usd: float = Field(default=0, ge=0)
    sample_count: int = Field(default=0, ge=0)
    confidence_lower: float = Field(default=0, ge=0, le=1)
