"""Durable same-prompt evaluation runner with fail-closed live execution."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
import hashlib
import logging
import math
from typing import Any

from .contracts import (
    EvaluationCase,
    EvaluationProfile,
    EvaluationRunRequest,
    PairwiseDecision,
)
from .judging import build_blind_judge_prompt, parse_judgment, resolve_position_swaps
from .statistics import (
    bootstrap_win_rate,
    holm_bonferroni,
    one_sided_sign_test,
    pass_at_k,
    pass_power_k,
)
from .store import EvaluationStore


CORE_MODEL_ALIASES = (
    "claude-cli/sonnet",
    "codex/gpt-5-5",
    "codex/gpt-5-6-sol",
    "codex/gpt-5-6-terra",
    "gemini-cli/flash",
    "antigravity/pro",
)


@dataclass(frozen=True)
class EvaluationStageUsage:
    stage: str
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class EvaluationResponse:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    total_ms: float = 0
    ttft_ms: float | None = None
    ttft_unavailable_reason: str | None = None
    actual_cost_usd: float | None = None
    normalized_cost_usd: float | None = None
    pricing_version: str | None = None
    resolved_model: str | None = None
    participant_models: tuple[str, ...] = ()
    stage_usage: tuple[EvaluationStageUsage, ...] = ()


ExecuteFn = Callable[
    [str, EvaluationCase, EvaluationRunRequest], Awaitable[EvaluationResponse]
]
JudgeFn = Callable[[str, str, EvaluationRunRequest], Awaitable[str]]
CompletionFn = Callable[[dict[str, Any]], None]


log = logging.getLogger("multillm.evaluation.runner")


def deduplicate_targets(catalog: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate aliases by provider model and material execution profile."""
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    for raw in catalog:
        item = dict(raw)
        alias = str(item.get("alias", "")).strip()
        if not alias:
            continue
        key = (
            str(item.get("provider", "")).strip().lower(),
            str(item.get("providerModel", item.get("model", alias))).strip(),
            str(item.get("reasoning", item.get("reasoningEffort", "default"))).strip(),
        )
        if key not in grouped:
            grouped[key] = {**item, "equivalentAliases": [alias]}
        else:
            grouped[key]["equivalentAliases"] = [
                *grouped[key]["equivalentAliases"],
                alias,
            ]
    return list(grouped.values())


def validate_live_targets(
    targets: Sequence[str], catalog: Mapping[str, Mapping[str, Any]]
) -> tuple[str, ...]:
    """Fail closed unless every requested target passed a host execution probe."""
    validated: list[str] = []
    for target in targets:
        capability = catalog.get(target)
        if not capability or not capability.get("available"):
            raise ValueError(f"{target}: model is not available")
        if not capability.get("executionVerified"):
            raise ValueError(f"{target}: live execution probe has not succeeded")
        if capability.get("executionMode") != "live_host":
            raise ValueError(f"{target}: execution mode must be live_host")
        validated.append(target)
    return tuple(validated)


def select_human_calibration(
    comparisons: Sequence[Mapping[str, Any]], *, fraction: float, seed: int
) -> frozenset[str]:
    """Select a reproducible max(30, fraction) release-calibration sample."""
    if not comparisons or fraction <= 0:
        return frozenset()
    if fraction > 1:
        raise ValueError("human calibration fraction cannot exceed 1")
    desired = min(len(comparisons), max(30, math.ceil(len(comparisons) * fraction)))
    identifiers = [str(item["id"]) for item in comparisons]
    ranked = sorted(
        identifiers,
        key=lambda identifier: hashlib.sha256(
            f"{seed}:{identifier}".encode("utf-8")
        ).hexdigest(),
    )
    return frozenset(ranked[:desired])


def evaluate_release_gate(
    profile: EvaluationProfile | str,
    pairwise_summary: Sequence[Mapping[str, Any]],
    *,
    pending_reviews: bool,
) -> str:
    """Return an evidence state without claiming success before calibration."""
    if EvaluationProfile(profile) is not EvaluationProfile.RELEASE:
        return "not_evaluated"
    if pending_reviews:
        return "pending_human_review"
    demonstrated = bool(pairwise_summary) and all(
        item.get("lower95") is not None
        and float(item["lower95"]) > 0.5
        and float(item.get("adjustedPValue", 1.0)) <= 0.05
        for item in pairwise_summary
    )
    return "pass" if demonstrated else "not_demonstrated"


def _score_terms(case: EvaluationCase, text: str) -> tuple[float, bool, list[str]]:
    lowered = text.casefold()
    missing = [term for term in case.required_terms if term.casefold() not in lowered]
    if not case.required_terms:
        return 1.0, True, []
    score = (len(case.required_terms) - len(missing)) / len(case.required_terms)
    return score, not missing, missing


def _score_forbidden(case: EvaluationCase, text: str) -> tuple[float, bool, list[str]]:
    lowered = text.casefold()
    found = [term for term in case.forbidden_terms if term.casefold() in lowered]
    return (0.0 if found else 1.0), not found, found


class EvaluationRunner:
    def __init__(
        self,
        *,
        store: EvaluationStore,
        execute: ExecuteFn,
        worker_id: str,
        judge: JudgeFn | None = None,
        on_complete: CompletionFn | None = None,
    ):
        self.store = store
        self.execute = execute
        self.worker_id = worker_id
        self.judge = judge
        self.on_complete = on_complete

    def _notify_complete(self, tenant_id: str, run_id: str) -> None:
        if self.on_complete is None:
            return
        run = self.store.get_run(tenant_id, run_id, include_content=False)
        if run is None:
            return
        try:
            self.on_complete(run)
        except Exception as exc:  # telemetry must never change run status
            log.debug("evaluation completion callback failed: %s", exc)

    @staticmethod
    def _targets(request: EvaluationRunRequest) -> tuple[str, ...]:
        candidates = request.candidates
        if request.candidate_scope == "core" and not candidates:
            candidates = CORE_MODEL_ALIASES
        targets = tuple(dict.fromkeys((*candidates, *request.moa_variants)))
        if not targets:
            raise ValueError("evaluation run has no targets")
        return targets

    async def run_once(self) -> str | None:
        claimed = self.store.claim_next_run(self.worker_id, lease_seconds=120)
        if claimed is None:
            return None
        tenant_id = claimed["tenantId"]
        run_id = claimed["id"]
        request = EvaluationRunRequest.model_validate(claimed["request"])
        suite = self.store.get_suite(tenant_id, request.suite_id)
        if suite is None:
            self.store.complete_run(
                tenant_id, run_id, summary={"error": "suite not found"}, status="failed"
            )
            self._notify_complete(tenant_id, run_id)
            return run_id

        outputs = 0
        metric_passes = 0
        metric_count = 0
        failures: list[dict[str, str]] = []
        generated: dict[tuple[str, str, int], EvaluationResponse] = {}
        attempt_outcomes: dict[tuple[str, str], list[bool]] = {}
        cancelled = False
        for raw_case in suite["cases"]:
            case = EvaluationCase.model_validate(raw_case)
            for target in self._targets(request):
                for attempt in range(1, request.repeats + 1):
                    if self.store.is_cancelled(tenant_id, run_id):
                        cancelled = True
                        break
                    try:
                        response = await self.execute(target, case, request)
                    except Exception as exc:  # one model cannot abort the complete matrix
                        failures.append(
                            {"caseId": case.id, "target": target, "error": type(exc).__name__}
                        )
                        continue
                    self.store.record_output(
                        tenant_id,
                        run_id,
                        case_id=case.id,
                        target=target,
                        attempt=attempt,
                        output_text=response.text,
                        usage={
                            "input_tokens": response.input_tokens,
                            "output_tokens": response.output_tokens,
                            "reasoning_tokens": response.reasoning_tokens,
                            "cache_read_tokens": response.cache_read_tokens,
                            "cache_write_tokens": response.cache_write_tokens,
                            "stages": [
                                {
                                    "stage": stage.stage,
                                    "input_tokens": stage.input_tokens,
                                    "output_tokens": stage.output_tokens,
                                }
                                for stage in response.stage_usage
                            ],
                        },
                        latency={
                            "total_ms": response.total_ms,
                            "ttft_ms": response.ttft_ms,
                            "ttft_unavailable_reason": response.ttft_unavailable_reason,
                        },
                        cost={
                            "actual_usd": response.actual_cost_usd,
                            "normalized_usd": response.normalized_cost_usd,
                            "pricing_version": response.pricing_version,
                        },
                        status="succeeded",
                    )
                    generated[(case.id, target, attempt)] = response
                    outputs += 1
                    required_score, required_passed, missing = _score_terms(case, response.text)
                    forbidden_score, forbidden_passed, found = _score_forbidden(
                        case, response.text
                    )
                    attempt_outcomes.setdefault((case.id, target), []).append(
                        required_passed and forbidden_passed
                    )
                    for metric, value, passed, details in (
                        ("required_terms", required_score, required_passed, {"missing": missing}),
                        ("forbidden_terms", forbidden_score, forbidden_passed, {"found": found}),
                    ):
                        self.store.record_metric(
                            tenant_id,
                            run_id,
                            case_id=case.id,
                            target=target,
                            attempt=attempt,
                            metric=metric,
                            value=value,
                            passed=passed,
                            details=details,
                        )
                        metric_count += 1
                        metric_passes += int(passed)
                if cancelled:
                    break
            if cancelled:
                break
            self.store.heartbeat(tenant_id, run_id, self.worker_id, lease_seconds=120)

        if cancelled:
            self._notify_complete(tenant_id, run_id)
            return run_id

        comparisons = await self._judge_pairs(
            tenant_id=tenant_id,
            run_id=run_id,
            request=request,
            cases=tuple(EvaluationCase.model_validate(item) for item in suite["cases"]),
            generated=generated,
        )
        pairwise_summary = self._pairwise_summary(comparisons)
        release_gate = evaluate_release_gate(
            request.profile,
            pairwise_summary,
            pending_reviews=any(
                comparison["needsHumanReview"] for comparison in comparisons
            ),
        )
        summary = {
            "outputs": outputs,
            "failures": failures,
            "deterministicPassRate": metric_passes / metric_count if metric_count else 0.0,
            "executionMode": request.execution_mode.value,
            "reliability": self._reliability_summary(
                attempt_outcomes, repeats=request.repeats
            ),
            "pairwise": pairwise_summary,
            "releaseGate": release_gate,
        }
        status = "completed" if outputs and not failures else "incomplete" if outputs else "failed"
        self.store.complete_run(tenant_id, run_id, summary=summary, status=status)
        self._notify_complete(tenant_id, run_id)
        return run_id

    @staticmethod
    def _reliability_summary(
        outcomes: Mapping[tuple[str, str], Sequence[bool]], *, repeats: int
    ) -> list[dict[str, Any]]:
        grouped: dict[str, list[Sequence[bool]]] = {}
        for (_case_id, target), attempts in outcomes.items():
            grouped.setdefault(target, []).append(attempts)
        summaries: list[dict[str, Any]] = []
        for target, cases in sorted(grouped.items()):
            pass_at_values: list[float] = []
            pass_power_values: list[float] = []
            successes = 0
            attempts_count = 0
            for attempts in cases:
                case_successes = sum(bool(item) for item in attempts)
                case_attempts = len(attempts)
                if not case_attempts:
                    continue
                k = min(repeats, case_attempts)
                pass_at_values.append(
                    pass_at_k(successes=case_successes, attempts=case_attempts, k=k)
                )
                pass_power_values.append(
                    pass_power_k(
                        success_probability=case_successes / case_attempts,
                        k=k,
                    )
                )
                successes += case_successes
                attempts_count += case_attempts
            summaries.append(
                {
                    "target": target,
                    "k": repeats,
                    "caseCount": len(cases),
                    "attemptPassRate": successes / attempts_count if attempts_count else 0.0,
                    "passAtK": sum(pass_at_values) / len(pass_at_values)
                    if pass_at_values
                    else 0.0,
                    "passPowerK": sum(pass_power_values) / len(pass_power_values)
                    if pass_power_values
                    else 0.0,
                }
            )
        return summaries

    async def _judge_pairs(
        self,
        *,
        tenant_id: str,
        run_id: str,
        request: EvaluationRunRequest,
        cases: tuple[EvaluationCase, ...],
        generated: dict[tuple[str, str, int], EvaluationResponse],
    ) -> list[dict[str, Any]]:
        if not request.moa_variants or not request.candidates:
            return []
        for case in cases:
            for candidate_target in request.moa_variants:
                candidate = generated.get((case.id, candidate_target, 1))
                if candidate is None:
                    continue
                for baseline_target in request.candidates:
                    baseline = generated.get((case.id, baseline_target, 1))
                    if baseline is None:
                        continue
                    comparison_id = self.store.create_comparison(
                        tenant_id,
                        run_id,
                        case_id=case.id,
                        candidate_target=candidate_target,
                        baseline_target=baseline_target,
                    )
                    excluded = {
                        candidate_target,
                        baseline_target,
                        *candidate.participant_models,
                        *baseline.participant_models,
                    }
                    judges = [alias for alias in request.judge_pool if alias not in excluded][:2]
                    resolved: list[PairwiseDecision] = []
                    if self.judge is not None and len(judges) == 2:
                        for judge_alias in judges:
                            normal_prompt = build_blind_judge_prompt(
                                user_prompt=case.prompt,
                                response_a=candidate.text,
                                response_b=baseline.text,
                            )
                            swapped_prompt = build_blind_judge_prompt(
                                user_prompt=case.prompt,
                                response_a=baseline.text,
                                response_b=candidate.text,
                            )
                            try:
                                normal = parse_judgment(
                                    await self.judge(judge_alias, normal_prompt, request)
                                )
                                swapped = parse_judgment(
                                    await self.judge(judge_alias, swapped_prompt, request)
                                )
                            except (ValueError, RuntimeError):
                                resolved.append(PairwiseDecision.ABSTAIN)
                                continue
                            self.store.record_judgment(
                                tenant_id,
                                comparison_id,
                                judge=judge_alias,
                                ordering="normal",
                                judgment=normal,
                            )
                            self.store.record_judgment(
                                tenant_id,
                                comparison_id,
                                judge=judge_alias,
                                ordering="swapped",
                                judgment=swapped,
                            )
                            resolved.append(
                                resolve_position_swaps(normal=normal, swapped=swapped)
                            )
                    decision = (
                        resolved[0]
                        if len(resolved) == 2
                        and resolved[0] is resolved[1]
                        and resolved[0] is not PairwiseDecision.ABSTAIN
                        else PairwiseDecision.ABSTAIN
                    )
                    self.store.finalize_comparison(
                        tenant_id,
                        comparison_id,
                        decision=decision,
                        needs_human_review=decision is PairwiseDecision.ABSTAIN,
                        details={
                            "judgesRequested": 2,
                            "judgesCompleted": len(resolved),
                            "sampledHumanCalibration": False,
                        },
                    )
        comparisons = self.store.list_comparisons(tenant_id, run_id)
        if request.profile is EvaluationProfile.RELEASE:
            sampled = select_human_calibration(
                comparisons,
                fraction=request.human_review_fraction,
                seed=request.seed,
            )
            for comparison in comparisons:
                if comparison["id"] not in sampled:
                    continue
                self.store.finalize_comparison(
                    tenant_id,
                    comparison["id"],
                    decision=comparison["decision"],
                    needs_human_review=True,
                    details={
                        **comparison["details"],
                        "sampledHumanCalibration": True,
                    },
                )
            if sampled:
                comparisons = self.store.list_comparisons(tenant_id, run_id)
        return comparisons

    @staticmethod
    def _pairwise_summary(comparisons: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], list[PairwiseDecision]] = {}
        for comparison in comparisons:
            decision = comparison.get("humanDecision") or comparison["decision"]
            grouped.setdefault(
                (comparison["candidateTarget"], comparison["baselineTarget"]), []
            ).append(PairwiseDecision(decision))
        result: list[dict[str, Any]] = []
        p_values: dict[str, float] = {}
        for (candidate, baseline), decisions in sorted(grouped.items()):
            scored = [item for item in decisions if item is not PairwiseDecision.ABSTAIN]
            if not scored:
                result.append(
                    {
                        "candidate": candidate,
                        "baseline": baseline,
                        "winRate": None,
                        "lower95": None,
                        "upper95": None,
                        "sampleCount": 0,
                        "pValue": None,
                        "adjustedPValue": None,
                    }
                )
                continue
            summary = bootstrap_win_rate(scored, samples=1_000, seed=17)
            comparison_key = f"{candidate}\0{baseline}"
            p_value = one_sided_sign_test(wins=summary.wins, losses=summary.losses)
            p_values[comparison_key] = p_value
            result.append(
                {
                    "comparisonKey": comparison_key,
                    "candidate": candidate,
                    "baseline": baseline,
                    "winRate": summary.win_rate,
                    "lower95": summary.lower_95,
                    "upper95": summary.upper_95,
                    "sampleCount": summary.sample_count,
                    "pValue": p_value,
                }
            )
        adjusted = holm_bonferroni(p_values)
        for item in result:
            key = item.pop("comparisonKey", None)
            if key is not None:
                item["adjustedPValue"] = adjusted[key]
        return result
