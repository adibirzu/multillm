"""Strict, immutable contracts shared by evaluation runners and APIs."""

from __future__ import annotations

from enum import Enum
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)


class EvaluationProfile(str, Enum):
    CI = "ci"
    NIGHTLY = "nightly"
    RELEASE = "release"


class ExecutionMode(str, Enum):
    FIXTURE = "fixture"
    REPLAY = "replay"
    LIVE_HOST = "live_host"


class PairwiseDecision(str, Enum):
    CANDIDATE = "candidate"
    BASELINE = "baseline"
    TIE = "tie"
    ABSTAIN = "abstain"


class ResponseChoice(str, Enum):
    RESPONSE_A = "response_a"
    RESPONSE_B = "response_b"
    TIE = "tie"
    ABSTAIN = "abstain"


class EvaluationCase(FrozenModel):
    id: str = Field(min_length=1, max_length=160)
    prompt: str = Field(min_length=1, max_length=100_000)
    category: str = Field(min_length=1, max_length=120)
    expected_tools: tuple[str, ...] = ()
    required_terms: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
    reference_answer: str | None = Field(default=None, max_length=100_000)
    tags: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id", "category")
    @classmethod
    def validate_identifier(cls, value: str) -> str:
        normalized = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", normalized):
            raise ValueError("identifier contains unsupported characters")
        return normalized

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("prompt cannot be empty")
        return value

    @field_validator(
        "expected_tools", "required_terms", "forbidden_terms", "tags", mode="before"
    )
    @classmethod
    def normalize_string_tuple(cls, value: Any) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        if not isinstance(value, (list, tuple, set)):
            raise ValueError("value must be an array")
        result: list[str] = []
        for item in value:
            normalized = str(item).strip()
            if not normalized or len(normalized) > 240:
                raise ValueError(
                    "array values must be non-empty and at most 240 characters"
                )
            if normalized not in result:
                result.append(normalized)
        if len(result) > 100:
            raise ValueError("array accepts at most 100 values")
        return tuple(result)


class EvaluationRunRequest(FrozenModel):
    suite_id: str = Field(min_length=1, max_length=160)
    profile: EvaluationProfile = EvaluationProfile.CI
    candidate_scope: str = "core"
    candidates: tuple[str, ...] = ()
    moa_variants: tuple[str, ...] = ("moa/quality",)
    judge_pool: tuple[str, ...] = ()
    execution_mode: ExecutionMode = ExecutionMode.FIXTURE
    live_authorized: bool = False
    preflight_receipt: str | None = Field(
        default=None, pattern=r"^evalpf_[A-Za-z0-9_-]{12,80}$"
    )
    repeats: int = Field(default=1, ge=1, le=5)
    seed: int = 17
    cache_mode: str = "cold"
    human_review_fraction: float = Field(default=0.1, ge=0, le=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("suite_id")
    @classmethod
    def validate_suite_id(cls, value: str) -> str:
        normalized = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]*", normalized):
            raise ValueError("suite_id contains unsupported characters")
        return normalized

    @field_validator("candidate_scope")
    @classmethod
    def validate_scope(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"core", "live", "explicit"}:
            raise ValueError("candidate_scope must be core, live, or explicit")
        return normalized

    @field_validator("cache_mode")
    @classmethod
    def validate_cache_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"cold", "warm", "both"}:
            raise ValueError("cache_mode must be cold, warm, or both")
        return normalized

    @field_validator("candidates", "moa_variants", "judge_pool", mode="before")
    @classmethod
    def normalize_targets(cls, value: Any) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        if not isinstance(value, (list, tuple, set)):
            raise ValueError("targets must be an array")
        targets: list[str] = []
        for item in value:
            target = str(item).strip()
            if not target or len(target) > 200:
                raise ValueError(
                    "target names must be non-empty and at most 200 characters"
                )
            if target not in targets:
                targets.append(target)
        if len(targets) > 100:
            raise ValueError("at most 100 targets are allowed")
        return tuple(targets)

    @model_validator(mode="after")
    def validate_execution(self):
        if self.execution_mode is ExecutionMode.LIVE_HOST and not self.live_authorized:
            raise ValueError("live_host execution requires explicit live authorization")
        if (
            self.execution_mode is ExecutionMode.LIVE_HOST
            and not self.preflight_receipt
        ):
            raise ValueError("live_host execution requires a valid preflight receipt")
        if self.candidate_scope == "explicit" and not self.candidates:
            raise ValueError("explicit candidate_scope requires candidates")
        if self.candidate_scope == "live" and not self.candidates:
            raise ValueError(
                "live candidate_scope requires execution-probed candidates"
            )
        if self.judge_pool and len(self.judge_pool) < 2:
            raise ValueError("judge_pool requires at least two judges")
        return self


class PairwiseJudgment(FrozenModel):
    decision: ResponseChoice
    correctness: float = Field(ge=0, le=1)
    completeness: float = Field(ge=0, le=1)
    depth: float = Field(ge=0, le=1)
    coherence: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(min_length=1, max_length=8_000)
    safety_flags: tuple[str, ...] = ()
