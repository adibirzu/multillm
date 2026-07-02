"""Small deterministic statistical helpers for evaluation reports."""

from __future__ import annotations

from dataclasses import dataclass
import math
import random
from collections.abc import Mapping, Sequence

from .contracts import PairwiseDecision


@dataclass(frozen=True)
class WinRateSummary:
    win_rate: float
    lower_95: float
    upper_95: float
    wins: int
    losses: int
    ties: int
    abstentions: int
    sample_count: int


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise ValueError("cannot compute a percentile for an empty sample")
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def bootstrap_win_rate(
    decisions: Sequence[PairwiseDecision | str], *, samples: int = 10_000, seed: int = 17
) -> WinRateSummary:
    """Return a tie-aware win rate and prompt-level percentile bootstrap CI."""
    if samples < 100:
        raise ValueError("bootstrap samples must be at least 100")
    normalized = [PairwiseDecision(item) for item in decisions]
    abstentions = sum(item is PairwiseDecision.ABSTAIN for item in normalized)
    scored = [
        1.0
        if item is PairwiseDecision.CANDIDATE
        else 0.5
        if item is PairwiseDecision.TIE
        else 0.0
        for item in normalized
        if item is not PairwiseDecision.ABSTAIN
    ]
    if not scored:
        raise ValueError("at least one non-abstaining decision is required")
    rng = random.Random(seed)
    resampled = [
        sum(rng.choice(scored) for _ in scored) / len(scored) for _ in range(samples)
    ]
    return WinRateSummary(
        win_rate=sum(scored) / len(scored),
        lower_95=_percentile(resampled, 0.025),
        upper_95=_percentile(resampled, 0.975),
        wins=sum(item is PairwiseDecision.CANDIDATE for item in normalized),
        losses=sum(item is PairwiseDecision.BASELINE for item in normalized),
        ties=sum(item is PairwiseDecision.TIE for item in normalized),
        abstentions=abstentions,
        sample_count=len(scored),
    )


def pass_at_k(*, successes: int, attempts: int, k: int) -> float:
    """Unbiased pass@k estimator used by code-generation evaluations."""
    if attempts < 0 or successes < 0 or successes > attempts:
        raise ValueError("successes must be between zero and attempts")
    if k < 1 or k > attempts:
        raise ValueError("k must be between one and attempts")
    failures = attempts - successes
    if failures < k:
        return 1.0
    return 1.0 - math.comb(failures, k) / math.comb(attempts, k)


def pass_power_k(*, success_probability: float, k: int) -> float:
    """Probability that every one of k independent attempts succeeds."""
    if not 0 <= success_probability <= 1:
        raise ValueError("success_probability must be between zero and one")
    if k < 1:
        raise ValueError("k must be positive")
    return success_probability**k


def one_sided_sign_test(*, wins: int, losses: int) -> float:
    """Exact one-sided p-value for candidate wins among non-tied pairs."""
    if wins < 0 or losses < 0:
        raise ValueError("wins and losses cannot be negative")
    observations = wins + losses
    if observations == 0 or wins <= losses:
        return 1.0
    return sum(math.comb(observations, count) for count in range(wins, observations + 1)) / (
        2**observations
    )


def holm_bonferroni(p_values: Mapping[str, float]) -> dict[str, float]:
    """Return monotonic Holm-adjusted p-values keyed like the input."""
    for value in p_values.values():
        if not 0 <= value <= 1:
            raise ValueError("p-values must be between zero and one")
    ordered = sorted(p_values.items(), key=lambda item: item[1])
    count = len(ordered)
    running = 0.0
    adjusted: dict[str, float] = {}
    for index, (key, value) in enumerate(ordered):
        running = max(running, (count - index) * value)
        adjusted[key] = min(1.0, running)
    return adjusted
