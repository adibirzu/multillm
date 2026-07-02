# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Budget caps and alerts for gateway-metered LLM spend.

Budgets apply to spend the gateway can actually see and control — the per-token
cost of cloud API calls it proxies — not to flat-rate subscriptions (e.g. a
Claude Code plan), which would otherwise read as permanently "over budget" and
drown the signal.

Two windows are tracked: a rolling **day** (last 24h) and a rolling **month**
(last 30d). Each configured cap produces an alert state:

  - ``ok``       — under the first alert threshold
  - ``warn``     — at/over a warn threshold (default 80%) but under the cap
  - ``exceeded`` — at/over 100% of the cap

When enforcement is enabled, an ``exceeded`` global or project cap blocks new
gateway requests (the LiteLLM virtual-key pattern) until the window rolls over.
Pure functions here so the policy is unit-tested without a server.
"""

from __future__ import annotations

from typing import Optional

DEFAULT_ALERT_THRESHOLDS = [0.8, 1.0]


def _state_for(pct: float, thresholds: list[float]) -> str:
    """Map a fraction-used to an alert state using sorted thresholds."""
    warn = thresholds[0] if thresholds else 0.8
    if pct >= 1.0:
        return "exceeded"
    if pct >= warn:
        return "warn"
    return "ok"


def _window_status(
    cap: Optional[float], spent: float, thresholds: list[float]
) -> Optional[dict]:
    """Status for one (cap, spent) pair, or None when no cap is configured."""
    if cap is None or cap <= 0:
        return None
    pct = spent / cap if cap else 0.0
    return {
        "capUSD": round(cap, 4),
        "spentUSD": round(spent, 4),
        "remainingUSD": round(max(0.0, cap - spent), 4),
        "percentUsed": round(pct * 100, 2),
        "state": _state_for(pct, thresholds),
    }


def _scope_status(
    *,
    label: str,
    scope: str,
    daily_cap: Optional[float],
    monthly_cap: Optional[float],
    spent_today: float,
    spent_month: float,
    thresholds: list[float],
) -> dict:
    return {
        "label": label,
        "scope": scope,
        "daily": _window_status(daily_cap, spent_today, thresholds),
        "monthly": _window_status(monthly_cap, spent_month, thresholds),
    }


def _collect_alerts(status: dict) -> list[dict]:
    """Flatten warn/exceeded windows into a list of alert messages."""
    alerts = []
    for window in ("daily", "monthly"):
        w = status.get(window)
        if not w or w["state"] == "ok":
            continue
        verb = "exceeded" if w["state"] == "exceeded" else "is near"
        alerts.append(
            {
                "scope": status["scope"],
                "label": status["label"],
                "window": window,
                "state": w["state"],
                "percentUsed": w["percentUsed"],
                "message": f"{status['label']} {window} budget {verb}: "
                f"${w['spentUSD']:.2f} of ${w['capUSD']:.2f} ({w['percentUsed']:.0f}%)",
            }
        )
    return alerts


def evaluate_budgets(
    *,
    config: dict,
    spent_today: float,
    spent_month: float,
    project_spend: Optional[dict] = None,
) -> dict:
    """Evaluate configured budgets against actual spend.

    ``config`` shape::

        {
          "enabled": false,                # enforcement master switch
          "daily_usd": 10.0,               # global caps (gateway-metered)
          "monthly_usd": 200.0,
          "alert_thresholds": [0.8, 1.0],
          "per_project": {"proj": {"daily_usd": .., "monthly_usd": ..}},
        }

    ``project_spend`` maps project -> {"today": usd, "month": usd}.
    """
    config = config or {}
    thresholds = sorted(config.get("alert_thresholds") or DEFAULT_ALERT_THRESHOLDS)
    project_spend = project_spend or {}

    global_status = _scope_status(
        label="All gateway spend",
        scope="global",
        daily_cap=config.get("daily_usd"),
        monthly_cap=config.get("monthly_usd"),
        spent_today=spent_today,
        spent_month=spent_month,
        thresholds=thresholds,
    )

    projects = []
    for name, caps in (config.get("per_project") or {}).items():
        spend = project_spend.get(name, {})
        projects.append(
            _scope_status(
                label=name,
                scope=f"project:{name}",
                daily_cap=(caps or {}).get("daily_usd"),
                monthly_cap=(caps or {}).get("monthly_usd"),
                spent_today=float(spend.get("today", 0) or 0),
                spent_month=float(spend.get("month", 0) or 0),
                thresholds=thresholds,
            )
        )

    alerts = _collect_alerts(global_status)
    for p in projects:
        alerts.extend(_collect_alerts(p))

    enforce = bool(config.get("enabled"))
    blocked = enforce and any(a["state"] == "exceeded" for a in alerts)

    return {
        "enabled": enforce,
        "thresholds": thresholds,
        "global": global_status,
        "projects": projects,
        "alerts": alerts,
        "blocked": blocked,
    }


def _blocking_alert(result: dict, project: str) -> Optional[dict]:
    """Return the first exceeded alert that can block this project."""
    scopes = {"global", f"project:{project}"}
    return next(
        (
            alert
            for alert in result["alerts"]
            if alert["state"] == "exceeded" and alert["scope"] in scopes
        ),
        None,
    )


def _project_spend(project_spend: Optional[dict], project: str, cost: float) -> dict:
    """Return a copy of project spend with the anticipated request included."""
    spend = project_spend or {}
    current = spend.get(project, {}) or {}
    return {
        **spend,
        project: {
            **current,
            "today": float(current.get("today", 0) or 0) + cost,
            "month": float(current.get("month", 0) or 0) + cost,
        },
    }


def check_request_allowed(
    *,
    config: dict,
    project: str,
    spent_today: float,
    spent_month: float,
    project_spend: Optional[dict] = None,
    anticipated_cost: float = 0.0,
) -> tuple[bool, Optional[str]]:
    """Pre-dispatch gate: (allowed, reason).

    Returns ``(False, message)`` only when enforcement is enabled and a global
    or matching-project cap is already exceeded, or when ``anticipated_cost``
    would cross one of those caps. Otherwise returns ``(True, None)``.
    """
    if not config or not config.get("enabled"):
        return True, None

    result = evaluate_budgets(
        config=config,
        spent_today=spent_today,
        spent_month=spent_month,
        project_spend=project_spend,
    )
    if alert := _blocking_alert(result, project):
        return False, alert["message"]

    anticipated_cost = max(0.0, float(anticipated_cost or 0.0))
    if anticipated_cost == 0.0:
        return True, None

    projected = evaluate_budgets(
        config=config,
        spent_today=spent_today + anticipated_cost,
        spent_month=spent_month + anticipated_cost,
        project_spend=_project_spend(project_spend, project, anticipated_cost),
    )
    if alert := _blocking_alert(projected, project):
        return (
            False,
            f"The estimated ${anticipated_cost:.2f} request would exceed the "
            f"{alert['label']} {alert['window']} budget",
        )
    return True, None
