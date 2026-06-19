# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Log-driven query routing: pick the best single model for a prompt.

This is the query-level fusion idea from FusionFactory — instead of always
using one fixed model, learn from the gateway's own usage logs which models
perform well and route each query accordingly, blended with live health and
cost and tuned by a quality/cost knob (RouteLLM-style).

Four signals per candidate:
  - **reliability** — 1 - errorRate from the usage log (how often it succeeds)
  - **health**      — live backend score (circuit breaker + latency probe)
  - **speed**       — inverse of recent avg latency, normalized across the pool
  - **cost**        — inverse of avg cost, normalized across the pool

``quality = reliability·0.6 + health·0.4`` and ``efficiency = speed·0.5 + cost·0.5``;
the final score is ``bias·quality + (1-bias)·efficiency``. Prompt complexity
nudges the effective bias toward quality, so hard prompts prefer reliable models
without a separate capability table.

Pure functions so routing decisions are unit-tested without a server.
"""

from __future__ import annotations

from typing import Callable, Optional

# Defaults for a model with no usage history yet — neutral, slightly optimistic.
_DEFAULT_RELIABILITY = 0.8
_DEFAULT_LATENCY_MS = 5000.0
_DEFAULT_COST = 0.0


def _normalize_inverse(values: dict[str, float]) -> dict[str, float]:
    """Map values to 0..1 where the *smallest* value scores 1 (lower is better).

    Used for latency and cost. A flat distribution (all equal) scores 1.0 for
    everyone so the signal simply doesn't discriminate.
    """
    if not values:
        return {}
    lo, hi = min(values.values()), max(values.values())
    if hi <= lo:
        return {k: 1.0 for k in values}
    return {k: 1.0 - (v - lo) / (hi - lo) for k, v in values.items()}


def choose_model(
    *,
    prompt_complexity: float,
    pool: list[str],
    stats: dict,
    health_fn: Callable[[str], float],
    cost_fn: Callable[[str], float],
    bias: float = 0.5,
    routes: Optional[dict] = None,
) -> dict:
    """Choose the best model from ``pool``.

    - ``stats``: ``{alias: {backend, avgLatencyMs, avgCostUSD, errorRate}}`` from
      ``tracking.get_model_routing_stats``.
    - ``health_fn(backend) -> 0..1`` live health score.
    - ``cost_fn(alias) -> float`` per-request cost estimate (fallback when no log).
    - ``bias`` 0..1: 0 = cheapest/fastest, 1 = highest quality/reliability.

    Returns a decision dict with the choice, score, per-candidate breakdown, and
    the effective bias used.
    """
    routes = routes or {}
    pool = [m for m in pool if m]
    if not pool:
        return {"model": None, "reason": "empty pool", "candidates": []}

    # Complexity raises the effective bias toward quality (hard → reliable model).
    eff_bias = max(0.0, min(1.0, bias + prompt_complexity * 0.3))

    latencies = {
        m: (stats.get(m, {}).get("avgLatencyMs") or _DEFAULT_LATENCY_MS) for m in pool
    }
    costs = {
        m: (stats.get(m, {}).get("avgCostUSD") or cost_fn(m) or _DEFAULT_COST)
        for m in pool
    }
    speed_score = _normalize_inverse(latencies)
    cost_score = _normalize_inverse(costs)

    candidates = []
    for m in pool:
        s = stats.get(m, {})
        backend = s.get("backend") or (routes.get(m, {}) or {}).get("backend", "")
        reliability = 1.0 - (s.get("errorRate", 1.0 - _DEFAULT_RELIABILITY))
        health = health_fn(backend)
        quality = reliability * 0.6 + health * 0.4
        efficiency = speed_score[m] * 0.5 + cost_score[m] * 0.5
        score = eff_bias * quality + (1.0 - eff_bias) * efficiency
        candidates.append(
            {
                "model": m,
                "backend": backend,
                "score": round(score, 4),
                "reliability": round(reliability, 3),
                "health": round(health, 3),
                "speed": round(speed_score[m], 3),
                "cost": round(cost_score[m], 3),
                "requests": s.get("requests", 0),
            }
        )

    candidates.sort(key=lambda c: c["score"], reverse=True)
    best = candidates[0]
    return {
        "model": best["model"],
        "backend": best["backend"],
        "score": best["score"],
        "effectiveBias": round(eff_bias, 3),
        "complexity": round(prompt_complexity, 3),
        "candidates": candidates,
        "reason": f"highest blended score at bias={round(eff_bias, 2)} "
        f"(reliability {best['reliability']}, health {best['health']}, "
        f"speed {best['speed']}, cost {best['cost']})",
    }
