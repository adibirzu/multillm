"""Derived cost, latency, and token-amplification metrics."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenAmplification:
    total_amplification: float | None
    aggregator_context_expansion: float | None
    synthesis_compression: float | None
    prompt_tokens: int
    proposer_input_tokens: int
    proposer_output_tokens: int
    aggregator_input_tokens: int
    final_output_tokens: int
    total_moa_tokens: int
    baseline_total_tokens: int


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator > 0 else None


def token_amplification(
    *,
    prompt_tokens: int,
    proposer_input_tokens: int,
    proposer_output_tokens: int,
    aggregator_input_tokens: int,
    final_output_tokens: int,
    total_moa_tokens: int,
    baseline_total_tokens: int,
) -> TokenAmplification:
    values = (
        prompt_tokens,
        proposer_input_tokens,
        proposer_output_tokens,
        aggregator_input_tokens,
        final_output_tokens,
        total_moa_tokens,
        baseline_total_tokens,
    )
    if any(value < 0 for value in values):
        raise ValueError("token counts cannot be negative")
    return TokenAmplification(
        total_amplification=_ratio(total_moa_tokens, baseline_total_tokens),
        aggregator_context_expansion=_ratio(aggregator_input_tokens, prompt_tokens),
        synthesis_compression=_ratio(final_output_tokens, proposer_output_tokens),
        prompt_tokens=prompt_tokens,
        proposer_input_tokens=proposer_input_tokens,
        proposer_output_tokens=proposer_output_tokens,
        aggregator_input_tokens=aggregator_input_tokens,
        final_output_tokens=final_output_tokens,
        total_moa_tokens=total_moa_tokens,
        baseline_total_tokens=baseline_total_tokens,
    )
