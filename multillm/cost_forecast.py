# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Cost forecasting for the MultiLLM gateway.

Three capabilities, all pure functions so they unit-test without a server:

1. **Burn-rate** — how fast tokens/$ are being spent right now (gateway live
   window + per-source today rate).
2. **Projection** — extrapolate today's rate to end-of-day and end-of-month so
   the user can predict real development cost.
3. **Quota-exhaustion ETA** — for each usage limit with tokens remaining, divide
   remaining by the source's burn-rate to estimate when the quota runs out.

Plus a **pre-flight estimate**: price a prompt across candidate backends *before*
sending, so routing decisions can be cost-aware (idea borrowed from tokencost).
"""

from __future__ import annotations

import math
from typing import Optional

from .model_registry import pricing_for
from .tracking import COST_TABLE

# Default assumed completion length when the caller doesn't specify one. Real
# output length is unknown pre-flight; this is a transparent, overridable guess.
DEFAULT_EXPECTED_OUTPUT_TOKENS = 500

_ENC = None


def _encoder():
    """Lazily load a tiktoken encoder; fall back to a char heuristic if absent."""
    global _ENC
    if _ENC is None:
        try:
            import tiktoken

            _ENC = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _ENC = False  # sentinel: tried and unavailable
    return _ENC


def estimate_tokens(text: str) -> int:
    """Estimate token count for a string.

    Uses tiktoken's ``cl100k_base`` (a good cross-model approximation) when
    available, else ~4 chars/token. Pre-flight estimates are inherently
    approximate; this is honest about that.
    """
    enc = _encoder()
    if enc:
        try:
            return len(enc.encode(text or ""))
        except Exception:
            pass
    return max(1, math.ceil(len(text or "") / 4))


def _humanize_eta(hours: float) -> str:
    if hours < 1:
        return f"~{int(round(hours * 60))} min"
    if hours < 48:
        return f"~{hours:.1f} h"
    return f"~{hours / 24:.1f} days"


def build_cost_forecast(
    *,
    unified: dict,
    gw_recent_1h: dict,
    gw_recent_24h: dict,
    window_hours: float,
) -> dict:
    """Compute burn-rate, projected spend, and quota-exhaustion ETAs.

    Uses the reliable per-source window aggregates in ``unified['sources']`` and
    the per-model usage limits in ``unified['limits']['items']``. ``unified`` is
    the dashboard bundle's payload (already cached), so this adds no history scan.

    Rates are averaged over the window (the only signal available for direct
    CLI sources, which expose daily rollups, not live counters); the gateway's
    own proxied traffic also gets a live last-hour rate.
    """
    window_hours = max(1.0, float(window_hours))
    window_days = window_hours / 24.0
    sources = {
        s.get("source"): s for s in (unified.get("sources") or []) if s.get("source")
    }

    # --- Gateway live burn-rate (proxied requests only) ---
    g1 = gw_recent_1h.get("totals", {}) or {}
    g1_tokens = (g1.get("total_input", 0) or 0) + (g1.get("total_output", 0) or 0)
    g24 = gw_recent_24h.get("totals", {}) or {}
    gateway_burn = {
        "tokensPerMin": round(g1_tokens / 60.0, 2),
        "tokensLastHour": g1_tokens,
        "costPerHourUSD": round((g24.get("total_cost", 0) or 0) / 24.0, 4),
        "window": "tokens=last 1h, cost=last 24h avg",
    }

    # --- Per-source window-average burn-rate ---
    by_source: dict[str, dict] = {}
    total_cost = 0.0
    for src, s in sources.items():
        tokens = int(s.get("tokens", 0) or 0)
        cost = float(s.get("actualCostUSD", s.get("costUSD", 0)) or 0)
        total_cost += cost
        by_source[src] = {
            "windowTokens": tokens,
            "windowCostUSD": round(cost, 4),
            "tokensPerHour": round(tokens / window_hours, 1),
            "tokensPerDay": round(tokens / window_days, 1),
            "costPerDayUSD": round(cost / window_days, 4),
        }

    # --- Projection: extrapolate the window's average daily spend ---
    daily_cost = total_cost / window_days
    projected = {
        "perDayUSD": round(daily_cost, 2),
        "perWeekUSD": round(daily_cost * 7, 2),
        "monthEndUSD": round(daily_cost * 30, 2),
        "windowSpendUSD": round(total_cost, 2),
        "windowDays": round(window_days, 2),
        "basis": f"average over the last {round(window_days, 1)} day(s)",
    }

    # --- Quota-exhaustion ETAs (only meaningful when tokens remain) ---
    items = (unified.get("limits") or {}).get("items") or []
    etas = []
    for item in items:
        remaining = item.get("remainingTokens")
        if not remaining or remaining <= 0:
            continue
        src = item.get("source")
        rate = (by_source.get(src) or {}).get("tokensPerHour", 0)
        if rate <= 0:
            continue
        eta_h = remaining / rate
        etas.append(
            {
                "label": item.get("label"),
                "source": src,
                "remainingTokens": remaining,
                "limitTokens": item.get("limitTokens"),
                "percentUsed": item.get("percentUsed"),
                "tokensPerHour": rate,
                "etaHours": round(eta_h, 2),
                "etaText": _humanize_eta(eta_h),
                "exhaustsToday": eta_h <= 24.0,
            }
        )
    etas.sort(key=lambda e: e["etaHours"])

    return {
        "burnRate": {"gateway": gateway_burn, "bySource": by_source},
        "projected": projected,
        "quotaETAs": etas,
        "windowHours": round(window_hours, 2),
    }


def _backend_price(backend: str) -> Optional[dict]:
    return COST_TABLE.get(backend)


def estimate_prompt_cost(
    *,
    prompt: str,
    routes: dict,
    candidates: Optional[list[str]] = None,
    expected_output_tokens: int = DEFAULT_EXPECTED_OUTPUT_TOKENS,
) -> dict:
    """Pre-flight cost estimate of ``prompt`` across candidate model aliases.

    ``routes`` maps alias -> {"backend", "model"} (the gateway's ROUTES). When
    ``candidates`` is omitted, every known route is priced. Returns a list
    sorted cheapest-first so a caller can route cost-aware.
    """
    input_tokens = estimate_tokens(prompt)
    out_tokens = max(0, int(expected_output_tokens))

    aliases = candidates if candidates else list(routes.keys())
    seen_pairs: set[tuple[str, str]] = set()
    estimates = []
    for alias in aliases:
        route = routes.get(alias)
        if not route:
            continue
        backend = route.get("backend", "")
        model = route.get("model", alias)
        pair = (backend, model)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        price = pricing_for(backend, model)
        in_cost = input_tokens * price.input_per_million / 1_000_000
        out_cost = out_tokens * price.output_per_million / 1_000_000
        total = in_cost + out_cost
        estimates.append(
            {
                "alias": alias,
                "backend": backend,
                "model": model,
                "inputTokens": input_tokens,
                "expectedOutputTokens": out_tokens,
                "inputCostUSD": round(in_cost, 6),
                "outputCostUSD": round(out_cost, 6),
                "estimatedCostUSD": round(total, 6),
                "isFree": total == 0,
            }
        )

    estimates.sort(key=lambda e: e["estimatedCostUSD"])
    free = [e for e in estimates if e["isFree"]]
    paid = [e for e in estimates if not e["isFree"]]
    return {
        "inputTokens": input_tokens,
        "expectedOutputTokens": out_tokens,
        "tokenizer": "tiktoken/cl100k_base" if _encoder() else "chars/4-heuristic",
        "estimates": estimates,
        "cheapest": estimates[0] if estimates else None,
        "cheapestPaid": paid[0] if paid else None,
        "freeOptionCount": len(free),
        "note": "Model-specific pricing with conservative provider fallbacks; output length is an estimate.",
    }
