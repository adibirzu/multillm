"""Cross-LLM observability helpers for dashboard and API summaries."""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Optional


DEFAULT_USAGE_LIMITS = {
    "claude_opus": 35_000_000,
    "claude_sonnet": 70_000_000,
    "claude_haiku": 140_000_000,
    "claude_other": 70_000_000,
    "gemini_cli": 14_000_000,
    "codex_cli_external": 70_000_000,
}


def _merge_usage_limits(settings: Optional[dict] = None) -> dict[str, int]:
    limits = dict(DEFAULT_USAGE_LIMITS)

    configured = None
    if isinstance(settings, dict):
        configured = settings.get("usage_limits")
    else:
        from .memory import get_setting

        configured = get_setting("usage_limits", {})

    if isinstance(configured, dict):
        for key, value in configured.items():
            try:
                limits[key] = max(0, int(value))
            except (TypeError, ValueError):
                continue

    return limits


def _window_start(hours: int, today: date) -> date:
    days = max(1, math.ceil(max(hours, 1) / 24))
    return today - timedelta(days=days - 1)


def _claude_limit_target(model: str) -> tuple[str, str]:
    lowered = (model or "").lower()
    if "opus" in lowered:
        return "Claude Opus", "claude_opus"
    if "sonnet" in lowered:
        return "Claude Sonnet", "claude_sonnet"
    if "haiku" in lowered:
        return "Claude Haiku", "claude_haiku"
    return "Claude Other", "claude_other"


def _build_limit_item(
    *,
    item_id: str,
    label: str,
    source: str,
    used_tokens: int,
    limit_tokens: int,
    available: bool,
    **extra,
) -> dict:
    percent_used = 0.0
    if limit_tokens > 0:
        percent_used = round((used_tokens / limit_tokens) * 100, 2)
    remaining_tokens = max(0, limit_tokens - used_tokens) if limit_tokens > 0 else None

    if not available:
        status = "unavailable"
    elif used_tokens > 0:
        status = "active"
    else:
        status = "idle"

    return {
        "id": item_id,
        "label": label,
        "source": source,
        "available": available,
        "status": status,
        "usedTokens": used_tokens,
        "limitTokens": limit_tokens,
        "remainingTokens": remaining_tokens,
        "percentUsed": percent_used,
        **extra,
    }


def _collect_claude_usage(
    claude_stats: dict,
    *,
    hours: int,
    today: date,
) -> tuple[dict[str, dict], dict[str, dict], Optional[str]]:
    family_usage: dict[str, dict] = {}
    model_usage: dict[str, dict] = {}
    latest_date: Optional[str] = None
    start = _window_start(hours, today)

    for entry in claude_stats.get("dailyModelTokens", []) or []:
        entry_date = entry.get("date")
        try:
            day = date.fromisoformat(entry_date)
        except (TypeError, ValueError):
            continue
        if day < start:
            continue
        if latest_date is None or entry_date > latest_date:
            latest_date = entry_date

        for model, count in (entry.get("tokensByModel") or {}).items():
            label, limit_key = _claude_limit_target(model)
            tokens = int(count or 0)
            aggregate = family_usage.setdefault(
                limit_key,
                {
                    "label": label,
                    "models": set(),
                    "usedTokens": 0,
                },
            )
            aggregate["models"].add(model)
            aggregate["usedTokens"] += tokens
            model_entry = model_usage.setdefault(
                model,
                {
                    "model": model,
                    "label": label,
                    "limitKey": limit_key,
                    "usedTokens": 0,
                },
            )
            model_entry["usedTokens"] += tokens

    for aggregate in family_usage.values():
        aggregate["models"] = sorted(aggregate["models"])

    return family_usage, model_usage, latest_date


def _build_model_limit_item(
    *,
    source: str,
    model: str,
    used_tokens: int,
    limit_tokens: int,
    remaining_tokens: Optional[int],
    available: bool,
    scope: str,
    shared_used_tokens: Optional[int] = None,
    **extra,
) -> dict:
    percent_used = 0.0
    if limit_tokens > 0 and shared_used_tokens is not None:
        percent_used = round((shared_used_tokens / limit_tokens) * 100, 2)
    elif limit_tokens > 0:
        percent_used = round((used_tokens / limit_tokens) * 100, 2)

    if not available:
        status = "unavailable"
    elif remaining_tokens == 0 and limit_tokens > 0:
        status = "exhausted"
    elif used_tokens > 0:
        status = "active"
    else:
        status = "idle"

    return {
        "source": source,
        "model": model,
        "available": available,
        "status": status,
        "usedTokens": used_tokens,
        "limitTokens": limit_tokens,
        "remainingTokens": remaining_tokens,
        "percentUsed": percent_used,
        "scope": scope,
        "sharedUsedTokens": shared_used_tokens,
        **extra,
    }


def build_llm_observability_summary(
    *,
    hours: int,
    gateway_stats: dict,
    claude_stats: dict,
    codex_stats: dict,
    gemini_stats: dict,
    settings: Optional[dict] = None,
    today: Optional[date] = None,
) -> dict:
    """Build direct-client usage status and limit summaries."""
    limits = _merge_usage_limits(settings=settings)
    today_value = today or date.today()

    gateway_totals = gateway_stats.get("totals", {}) or {}
    gateway_tokens = int(gateway_totals.get("total_input", 0) or 0) + int(gateway_totals.get("total_output", 0) or 0)
    gateway_requests = int(gateway_totals.get("total_requests", 0) or 0)

    claude_available = bool(claude_stats.get("available"))
    claude_family_usage, claude_model_usage, claude_latest_date = _collect_claude_usage(
        claude_stats,
        hours=hours,
        today=today_value,
    )
    claude_tokens = sum(entry["usedTokens"] for entry in claude_family_usage.values())

    codex_available = bool(codex_stats.get("available"))
    codex_by_provider = codex_stats.get("byProvider", {}) or {}
    codex_by_model = codex_stats.get("byModel", {}) or {}
    codex_external_tokens = 0
    codex_external_sessions = 0
    codex_external_actual_cost = 0.0
    codex_external_list_price = 0.0
    codex_external_providers: list[str] = []
    codex_oca_tokens = 0
    codex_oca_sessions = 0
    codex_oca_providers: list[str] = []
    for provider, aggregate in codex_by_provider.items():
        tokens = int(aggregate.get("tokens", 0) or 0)
        sessions = int(aggregate.get("sessions", 0) or 0)
        if aggregate.get("isOCA"):
            codex_oca_tokens += tokens
            codex_oca_sessions += sessions
            if tokens > 0 or sessions > 0:
                codex_oca_providers.append(provider)
            continue
        codex_external_tokens += tokens
        codex_external_sessions += sessions
        codex_external_actual_cost += float(aggregate.get("actualCostUSD", 0) or 0)
        codex_external_list_price += float(aggregate.get("listPriceUSD", 0) or 0)
        if tokens > 0 or sessions > 0:
            codex_external_providers.append(provider)

    if not codex_available:
        codex_status = "unavailable"
    elif codex_external_tokens > 0 or codex_external_sessions > 0:
        codex_status = "external_usage"
    elif codex_oca_tokens > 0 or codex_oca_sessions > 0:
        codex_status = "oca_only"
    else:
        codex_status = "idle"

    gemini_available = bool(gemini_stats.get("available"))
    gemini_tokens = int(gemini_stats.get("totalTokens", 0) or 0)
    gemini_sessions = int(gemini_stats.get("totalSessions", 0) or 0)
    gemini_by_model = gemini_stats.get("byModel", {}) or {}
    if not gemini_available:
        gemini_status = "unavailable"
    elif gemini_tokens > 0 or gemini_sessions > 0:
        gemini_status = "active"
    else:
        gemini_status = "idle"

    status_by_source = {
        "gateway": {
            "available": True,
            "status": "active" if gateway_requests > 0 else "idle",
            "requests": gateway_requests,
            "tokens": gateway_tokens,
            "sessions": int(gateway_stats.get("session_count", 0) or 0),
        },
        "claude_code": {
            "available": claude_available,
            "status": "active" if claude_available and claude_tokens > 0 else ("idle" if claude_available else "unavailable"),
            "usedTokens": claude_tokens,
            "sessions": int(claude_stats.get("totalSessions", 0) or 0),
            "messages": int(claude_stats.get("totalMessages", 0) or 0),
            "latestDate": claude_latest_date,
        },
        "codex_cli": {
            "available": codex_available,
            "status": codex_status,
            "usedTokens": codex_external_tokens + codex_oca_tokens,
            "externalTokens": codex_external_tokens,
            "externalSessions": codex_external_sessions,
            "externalProviders": sorted(codex_external_providers),
            "ocaTokens": codex_oca_tokens,
            "ocaSessions": codex_oca_sessions,
            "ocaProviders": sorted(codex_oca_providers),
            "sessions": int(codex_stats.get("totalSessions", 0) or 0),
        },
        "gemini_cli": {
            "available": gemini_available,
            "status": gemini_status,
            "usedTokens": gemini_tokens,
            "sessions": gemini_sessions,
            "model": gemini_stats.get("model", ""),
        },
    }

    limit_items = [
        _build_limit_item(
            item_id="codex_cli_external",
            label="Codex CLI (External)",
            source="codex_cli",
            used_tokens=codex_external_tokens,
            limit_tokens=limits["codex_cli_external"],
            available=codex_available,
            sessions=codex_external_sessions,
            providers=sorted(codex_external_providers),
            actualCostUSD=round(codex_external_actual_cost, 4),
            listPriceUSD=round(codex_external_list_price, 4),
            externalOnly=True,
        ),
        _build_limit_item(
            item_id="gemini_cli",
            label="Gemini CLI",
            source="gemini_cli",
            used_tokens=gemini_tokens,
            limit_tokens=limits["gemini_cli"],
            available=gemini_available,
            sessions=gemini_sessions,
            model=gemini_stats.get("model", ""),
            estimatedCostUSD=round(float(gemini_stats.get("totalEstimatedCostUSD", 0) or 0), 4),
        ),
    ]

    for limit_key, aggregate in sorted(claude_family_usage.items(), key=lambda item: item[1]["label"]):
        limit_items.append(
            _build_limit_item(
                item_id=limit_key,
                label=aggregate["label"],
                source="claude_code",
                used_tokens=aggregate["usedTokens"],
                limit_tokens=limits.get(limit_key, 0),
                available=claude_available,
                models=aggregate["models"],
                dataAsOf=claude_latest_date,
                precision="daily_rollup",
            )
        )

    model_items = []
    for model, aggregate in sorted(claude_model_usage.items(), key=lambda item: item[0]):
        family_limit = limits.get(aggregate["limitKey"], 0)
        family_used = claude_family_usage.get(aggregate["limitKey"], {}).get("usedTokens", 0)
        model_items.append(
            _build_model_limit_item(
                source="claude_code",
                model=model,
                used_tokens=aggregate["usedTokens"],
                limit_tokens=family_limit,
                remaining_tokens=max(0, family_limit - family_used) if family_limit > 0 else None,
                available=claude_available,
                scope="family",
                shared_used_tokens=family_used,
                scopeLabel=aggregate["label"],
            )
        )

    codex_remaining = max(0, limits["codex_cli_external"] - codex_external_tokens) if limits["codex_cli_external"] > 0 else None
    for model, aggregate in sorted(codex_by_model.items(), key=lambda item: item[0]):
        external_tokens = int(aggregate.get("externalTokens", aggregate.get("tokens", 0)) or 0)
        oca_tokens = int(aggregate.get("ocaTokens", 0) or 0)
        providers = aggregate.get("providers", [])
        if external_tokens > 0:
            model_items.append(
                _build_model_limit_item(
                    source="codex_cli",
                    model=model,
                    used_tokens=external_tokens,
                    limit_tokens=limits["codex_cli_external"],
                    remaining_tokens=codex_remaining,
                    available=codex_available,
                    scope="shared_provider",
                    shared_used_tokens=codex_external_tokens,
                    scopeLabel="Codex CLI external",
                    providers=providers,
                    ocaTokens=oca_tokens,
                )
            )
        elif oca_tokens > 0:
            model_items.append(
                _build_model_limit_item(
                    source="codex_cli",
                    model=model,
                    used_tokens=oca_tokens,
                    limit_tokens=0,
                    remaining_tokens=None,
                    available=codex_available,
                    scope="unlimited",
                    shared_used_tokens=None,
                    scopeLabel="OCA",
                    providers=providers,
                )
            )

    gemini_remaining = max(0, limits["gemini_cli"] - gemini_tokens) if limits["gemini_cli"] > 0 else None
    for model, aggregate in sorted(gemini_by_model.items(), key=lambda item: item[0]):
        model_items.append(
            _build_model_limit_item(
                source="gemini_cli",
                model=model,
                used_tokens=int(aggregate.get("totalTokens", 0) or 0),
                limit_tokens=limits["gemini_cli"],
                remaining_tokens=gemini_remaining,
                available=gemini_available,
                scope="shared_provider",
                shared_used_tokens=gemini_tokens,
                scopeLabel="Gemini CLI",
                sessions=int(aggregate.get("sessions", 0) or 0),
            )
        )

    limit_items.sort(key=lambda item: (item["source"], item["label"]))
    model_items.sort(key=lambda item: (item["source"], -int(item.get("usedTokens", 0) or 0), item["model"]))

    return {
        "statusBySource": status_by_source,
        "limits": {
            "windowHours": hours,
            "windowStart": _window_start(hours, today_value).isoformat(),
            "windowEnd": today_value.isoformat(),
            "items": limit_items,
            "modelItems": model_items,
        },
    }
