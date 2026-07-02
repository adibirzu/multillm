"""Blinded, structured pairwise judging utilities."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from pydantic import ValidationError

from .contracts import PairwiseDecision, PairwiseJudgment, ResponseChoice


def build_blind_judge_prompt(
    *,
    user_prompt: str,
    response_a: str,
    response_b: str,
    criteria: Sequence[str] = ("correctness", "completeness", "depth", "coherence"),
) -> str:
    """Build an identity-free judge prompt with a machine-validated result contract."""
    criterion_text = ", ".join(
        str(item).strip() for item in criteria if str(item).strip()
    )
    return (
        "You are an independent evaluator. Compare two anonymous responses to the "
        "same user request. Do not infer authorship. Ignore response length, markdown "
        "quantity, or self-identification unless it affects the requested task.\n\n"
        f"USER REQUEST:\n{user_prompt.strip()}\n\n"
        f"Response A:\n{response_a.strip()}\n\n"
        f"Response B:\n{response_b.strip()}\n\n"
        f"Evaluate: {criterion_text}.\n"
        "Return one JSON object only, with no markdown or prose outside it:\n"
        '{"decision":"response_a|response_b|tie|abstain",'
        '"correctness":0.0,"completeness":0.0,"depth":0.0,"coherence":0.0,'
        '"confidence":0.0,"rationale":"brief evidence-based reason",'
        '"safety_flags":[]}\n'
        "All numeric scores must be between 0 and 1. Abstain when the responses "
        "cannot be compared reliably."
    )


def parse_judgment(text: str) -> PairwiseJudgment:
    """Parse a raw or fenced JSON judgment; reject winner-text heuristics."""
    candidate = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", candidate, flags=re.DOTALL)
    if fenced:
        candidate = fenced.group(1)
    if not candidate.startswith("{") or not candidate.endswith("}"):
        raise ValueError("judge must return structured JSON")
    try:
        payload = json.loads(candidate)
        return PairwiseJudgment.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError(f"judge returned invalid structured JSON: {exc}") from exc


def _normal_choice(choice: ResponseChoice) -> PairwiseDecision:
    return {
        ResponseChoice.RESPONSE_A: PairwiseDecision.CANDIDATE,
        ResponseChoice.RESPONSE_B: PairwiseDecision.BASELINE,
        ResponseChoice.TIE: PairwiseDecision.TIE,
        ResponseChoice.ABSTAIN: PairwiseDecision.ABSTAIN,
    }[choice]


def _swapped_choice(choice: ResponseChoice) -> PairwiseDecision:
    return {
        ResponseChoice.RESPONSE_A: PairwiseDecision.BASELINE,
        ResponseChoice.RESPONSE_B: PairwiseDecision.CANDIDATE,
        ResponseChoice.TIE: PairwiseDecision.TIE,
        ResponseChoice.ABSTAIN: PairwiseDecision.ABSTAIN,
    }[choice]


def resolve_position_swaps(
    *, normal: PairwiseJudgment, swapped: PairwiseJudgment
) -> PairwiseDecision:
    """Resolve A/B and B/A judgments, abstaining on position inconsistency."""
    first = _normal_choice(normal.decision)
    second = _swapped_choice(swapped.decision)
    return first if first is second else PairwiseDecision.ABSTAIN
