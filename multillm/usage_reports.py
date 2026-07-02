# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Calendar and session usage reports across MultiLLM usage sources."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Iterable, Literal, Optional

ReportKind = Literal["daily", "weekly", "monthly", "session", "blocks"]


def _num(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _int(value: object) -> int:
    return int(_num(value))


def _round_cost(value: object) -> float:
    return round(_num(value), 4)


def _parse_date(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    candidate = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(candidate)
    except ValueError:
        pass
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d")
    except ValueError:
        return None


def _period_key(day: str, kind: ReportKind) -> str:
    parsed = _parse_date(day)
    if parsed is None:
        return day
    if kind == "weekly":
        iso = parsed.date().isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if kind == "monthly":
        return parsed.strftime("%Y-%m")
    return parsed.date().isoformat()


def _empty_row(period: str, source: str = "") -> dict[str, Any]:
    row: dict[str, Any] = {
        "period": period,
        "tokens": 0,
        "inputTokens": 0,
        "outputTokens": 0,
        "cacheTokens": 0,
        "requests": 0,
        "sessions": 0,
        "messages": 0,
        "actualCostUSD": 0.0,
        "listPriceUSD": 0.0,
        "models": set(),
        "sources": set(),
    }
    if source:
        row["source"] = source
    return row


def _add_row(target: dict[str, Any], source: str, row: dict[str, Any]) -> None:
    input_tokens = _int(row.get("inputTokens", row.get("input_tokens", 0)))
    output_tokens = _int(row.get("outputTokens", row.get("output_tokens", 0)))
    cache_tokens = _int(
        row.get(
            "cacheTokens",
            _int(row.get("cachedTokens", 0))
            + _int(row.get("cacheReadInputTokens", 0))
            + _int(row.get("cacheCreationInputTokens", 0))
            + _int(row.get("cache_read_input_tokens", 0))
            + _int(row.get("cache_creation_input_tokens", 0)),
        )
    )
    tokens = _int(row.get("tokens", row.get("totalTokens", 0)))
    if tokens <= 0:
        tokens = input_tokens + output_tokens + cache_tokens

    target["tokens"] += tokens
    target["inputTokens"] += input_tokens
    target["outputTokens"] += output_tokens
    target["cacheTokens"] += cache_tokens
    target["requests"] += _int(row.get("requests", row.get("request_count", 0)))
    target["sessions"] += _int(row.get("sessions", row.get("sessionCount", 0)))
    target["messages"] += _int(row.get("messages", row.get("messageCount", 0)))
    actual_cost = _num(
        row.get("actualCostUSD", row.get("costUSD", row.get("cost_usd", 0)))
    )
    target["actualCostUSD"] += actual_cost
    target["listPriceUSD"] += _num(row.get("listPriceUSD", actual_cost))
    target["sources"].add(source)

    model = row.get("model")
    if model:
        target["models"].add(str(model))
    for model_name in row.get("models", []) or []:
        target["models"].add(str(model_name))


def _finalize_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    finalized = []
    for row in rows:
        item = dict(row)
        item["actualCostUSD"] = _round_cost(item["actualCostUSD"])
        item["listPriceUSD"] = _round_cost(item["listPriceUSD"])
        item["models"] = sorted(item["models"])
        item["sources"] = sorted(item["sources"])
        finalized.append(item)
    return sorted(finalized, key=lambda item: item["period"])


def _gateway_daily_rows(stats: dict) -> Iterable[tuple[str, dict[str, Any]]]:
    for row in stats.get("daily", []) or []:
        day = row.get("day") or row.get("date")
        if day:
            yield (
                str(day),
                {
                    "inputTokens": row.get("input_tokens", 0),
                    "outputTokens": row.get("output_tokens", 0),
                    "cacheTokens": _int(row.get("cache_read_input_tokens", 0))
                    + _int(row.get("cache_creation_input_tokens", 0)),
                    "requests": row.get("requests", 0),
                    "costUSD": row.get("cost_usd", 0),
                },
            )


def _claude_daily_rows(claude: dict) -> Iterable[tuple[str, dict[str, Any]]]:
    activity_by_day = {
        row.get("date"): row for row in claude.get("dailyActivity", []) or []
    }
    for row in claude.get("dailyModelTokens", []) or []:
        day = row.get("date")
        if not day:
            continue
        tokens_by_model = row.get("tokensByModel", {}) or {}
        activity = activity_by_day.get(day, {})
        yield (
            str(day),
            {
                "tokens": sum(_int(v) for v in tokens_by_model.values()),
                "sessions": activity.get("sessionCount", 0),
                "messages": activity.get("messageCount", 0),
                "models": list(tokens_by_model),
            },
        )


def _codex_daily_rows(codex: dict) -> Iterable[tuple[str, dict[str, Any]]]:
    for row in codex.get("daily", []) or []:
        day = row.get("date")
        if day:
            yield str(day), row


def _gemini_daily_rows(gemini: dict) -> Iterable[tuple[str, dict[str, Any]]]:
    for row in gemini.get("daily", []) or []:
        day = row.get("date")
        if day:
            yield str(day), row


def build_calendar_report(bundle: dict, *, kind: ReportKind) -> dict[str, Any]:
    """Build daily, weekly, or monthly usage rows from a dashboard bundle."""
    rows: dict[str, dict[str, Any]] = {}
    by_source: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    source_rows = (
        ("gateway", _gateway_daily_rows(bundle.get("stats", {}))),
        ("claude_code", _claude_daily_rows(bundle.get("claudeStats", {}))),
        ("codex_cli", _codex_daily_rows(bundle.get("codexStats", {}))),
        ("gemini_cli", _gemini_daily_rows(bundle.get("geminiStats", {}))),
    )

    for source, daily_rows in source_rows:
        for day, source_row in daily_rows:
            period = _period_key(day, kind)
            row = rows.setdefault(period, _empty_row(period))
            _add_row(row, source, source_row)
            source_bucket = by_source[source].setdefault(
                period, _empty_row(period, source)
            )
            _add_row(source_bucket, source, source_row)

    return {
        "kind": kind,
        "hours": (bundle.get("unified") or {}).get("hours"),
        "project": (bundle.get("unified") or {}).get("project"),
        "rows": _finalize_rows(rows.values()),
        "bySource": {
            source: _finalize_rows(periods.values())
            for source, periods in sorted(by_source.items())
        },
    }


def _session_time(session: dict) -> str:
    for key in ("createdAt", "timestamp", "startTime", "startedAt"):
        value = session.get(key)
        if value:
            return str(value)
    if session.get("started_at"):
        return datetime.fromtimestamp(_num(session["started_at"])).isoformat()
    return ""


def _session_tokens(session: dict) -> int:
    return _int(
        session.get(
            "tokensUsed",
            session.get(
                "totalTokens",
                _int(session.get("total_input_tokens", 0))
                + _int(session.get("total_output_tokens", 0))
                + _int(session.get("inputTokens", 0))
                + _int(session.get("outputTokens", 0)),
            ),
        )
    )


def build_session_report(bundle: dict) -> dict[str, Any]:
    """Build a unified per-session report across gateway and direct CLIs."""
    rows: list[dict[str, Any]] = []
    for session in bundle.get("sessions", []) or []:
        rows.append(
            {
                "source": "gateway",
                "sessionId": session.get("id", ""),
                "project": session.get("project", ""),
                "startedAt": _session_time(session),
                "requests": _int(session.get("total_requests", 0)),
                "tokens": _session_tokens(session),
                "actualCostUSD": _round_cost(session.get("total_cost_usd", 0)),
                "models": session.get("models_used", []) or [],
            }
        )

    for source, payload in (
        ("claude_code", bundle.get("claudeStats", {})),
        ("codex_cli", bundle.get("codexStats", {})),
        ("gemini_cli", bundle.get("geminiStats", {})),
    ):
        key = "sessionHistory" if source == "claude_code" else "sessions"
        for session in payload.get(key, []) or []:
            models = session.get("models_used") or session.get("model") or []
            if isinstance(models, str):
                models = [models]
            rows.append(
                {
                    "source": source,
                    "sessionId": session.get("sessionId", session.get("id", "")),
                    "project": session.get("project", ""),
                    "startedAt": _session_time(session),
                    "requests": _int(
                        session.get("commandCount", session.get("messageCount", 0))
                    ),
                    "tokens": _session_tokens(session),
                    "actualCostUSD": _round_cost(
                        session.get(
                            "actualCostUSD",
                            session.get("estimatedCostUSD", session.get("costUSD", 0)),
                        )
                    ),
                    "models": models,
                }
            )

    rows.sort(key=lambda item: item.get("startedAt", ""), reverse=True)
    return {
        "kind": "session",
        "hours": (bundle.get("unified") or {}).get("hours"),
        "project": (bundle.get("unified") or {}).get("project"),
        "rows": rows,
    }


def build_blocks_report(bundle: dict, *, block_hours: int = 5) -> dict[str, Any]:
    """Approximate Claude-style billing windows from Claude Code sessions."""
    blocks: dict[str, dict[str, Any]] = {}
    for session in (bundle.get("claudeStats", {}) or {}).get(
        "sessionHistory", []
    ) or []:
        parsed = _parse_date(_session_time(session))
        if parsed is None:
            continue
        hour = (parsed.hour // block_hours) * block_hours
        start = parsed.replace(hour=hour, minute=0, second=0, microsecond=0)
        end = start + timedelta(hours=block_hours)
        key = start.isoformat()
        block = blocks.setdefault(
            key,
            {
                "period": key,
                "startsAt": key,
                "endsAt": end.isoformat(),
                "tokens": 0,
                "sessions": 0,
                "messages": 0,
                "actualCostUSD": 0.0,
                "models": set(),
            },
        )
        block["tokens"] += _session_tokens(session)
        block["sessions"] += 1
        block["messages"] += _int(session.get("messageCount", 0))
        block["actualCostUSD"] += _num(session.get("estimatedCostUSD", 0))
        for model in session.get("models_used", []) or []:
            block["models"].add(str(model))

    rows = []
    for block in blocks.values():
        item = dict(block)
        item["actualCostUSD"] = _round_cost(item["actualCostUSD"])
        item["models"] = sorted(item["models"])
        rows.append(item)

    return {
        "kind": "blocks",
        "source": "claude_code",
        "blockHours": block_hours,
        "hours": (bundle.get("unified") or {}).get("hours"),
        "project": (bundle.get("unified") or {}).get("project"),
        "rows": sorted(rows, key=lambda item: item["startsAt"]),
    }


def build_usage_report(bundle: dict, *, kind: ReportKind) -> dict[str, Any]:
    if kind in ("daily", "weekly", "monthly"):
        return build_calendar_report(bundle, kind=kind)
    if kind == "session":
        return build_session_report(bundle)
    if kind == "blocks":
        return build_blocks_report(bundle)
    raise ValueError(f"unsupported report kind: {kind}")
