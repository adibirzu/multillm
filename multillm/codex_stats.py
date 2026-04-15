"""
Codex CLI stats integration — reads token usage, costs, and session history
from Codex CLI's internal SQLite database (~/.codex/) and rollout JSONL files.

Provides read-only access to:
- Per-session token usage with input/output/cached breakdown
- Session history with project/cwd info
- Aggregated daily, provider, and model usage
"""

import json
import logging
import sqlite3
from datetime import date, datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional

log = logging.getLogger("multillm.codex_stats")

CODEX_DIR = Path.home() / ".codex"
STATE_DB = CODEX_DIR / "state_5.sqlite"

# OpenAI list pricing per 1M tokens (USD) — source: openai.com/api/pricing (March 2026)
OPENAI_LIST_PRICING: dict[str, dict[str, float]] = {
    # GPT-5.4 family
    "gpt-5.4-pro":  {"input": 30.0,  "output": 180.0, "cached_input": 0.0},
    "gpt-5.4":      {"input": 2.50,  "output": 15.0,  "cached_input": 0.25},
    "gpt-5.4-mini": {"input": 0.75,  "output": 4.50,  "cached_input": 0.075},
    "gpt-5.4-nano": {"input": 0.20,  "output": 1.25,  "cached_input": 0.02},
    # GPT-4.1 family
    "gpt-4.1":      {"input": 2.00,  "output": 8.00,  "cached_input": 0.50},
    "gpt-4.1-mini": {"input": 0.40,  "output": 1.60,  "cached_input": 0.10},
    "gpt-4.1-nano": {"input": 0.10,  "output": 0.40,  "cached_input": 0.025},
    # GPT-4o family
    "gpt-4o":       {"input": 2.50,  "output": 10.0,  "cached_input": 1.25},
    "gpt-4o-mini":  {"input": 0.15,  "output": 0.60,  "cached_input": 0.075},
    # Reasoning models (o-series)
    "o3":           {"input": 2.00,  "output": 8.00,  "cached_input": 0.50},
    "o3-mini":      {"input": 1.10,  "output": 4.40,  "cached_input": 0.55},
    "o4-mini":      {"input": 1.10,  "output": 4.40,  "cached_input": 0.275},
}

# OCA provider pricing (internal Oracle — effectively free)
OCA_PROVIDERS = {"oca-chicago", "oca", "oca-ashburn", "oca-frankfurt", "oca-london"}


def _get_list_pricing(model: str) -> dict[str, float]:
    """Get OpenAI list pricing for a model. Returns input/output per 1M tokens."""
    pricing = OPENAI_LIST_PRICING.get(model)
    if pricing:
        return pricing

    clean_model = model.split("/", 1)[-1] if "/" in model else model
    pricing = OPENAI_LIST_PRICING.get(clean_model)
    if pricing:
        return pricing

    for key, value in OPENAI_LIST_PRICING.items():
        if clean_model.startswith(key):
            return value

    return {"input": 2.50, "output": 10.0, "cached_input": 0.25}


def _empty_usage_breakdown() -> dict[str, int]:
    return {
        "inputTokens": 0,
        "cachedTokens": 0,
        "outputTokens": 0,
        "reasoningOutputTokens": 0,
        "totalTokens": 0,
        "realNetTokens": 0,
    }


def _usage_from_token_payload(payload: dict) -> dict[str, int]:
    """Normalize token-count payloads from rollout JSONL events."""
    input_tokens = int(payload.get("input_tokens", 0) or 0)
    cached_tokens = int(payload.get("cached_input_tokens", 0) or 0)
    output_tokens = int(payload.get("output_tokens", 0) or 0)
    reasoning_output_tokens = int(payload.get("reasoning_output_tokens", 0) or 0)
    total_tokens = int(payload.get("total_tokens", 0) or 0)
    if total_tokens <= 0 and (input_tokens or output_tokens):
        total_tokens = input_tokens + output_tokens

    return {
        "inputTokens": input_tokens,
        "cachedTokens": cached_tokens,
        "outputTokens": output_tokens,
        "reasoningOutputTokens": reasoning_output_tokens,
        "totalTokens": total_tokens,
        "realNetTokens": max(0, input_tokens - cached_tokens) + output_tokens,
    }


def _merge_usage(target: dict[str, int], source: dict[str, int]) -> None:
    for key in (
        "inputTokens",
        "cachedTokens",
        "outputTokens",
        "reasoningOutputTokens",
        "totalTokens",
        "realNetTokens",
    ):
        target[key] += int(source.get(key, 0) or 0)


def _resolve_rollout_path(raw_path: str) -> Optional[Path]:
    if not raw_path:
        return None

    candidate = Path(raw_path).expanduser()
    candidates = [candidate]
    if not candidate.is_absolute():
        candidates.append(CODEX_DIR / candidate)
        candidates.append(CODEX_DIR / "sessions" / candidate)

    for path in candidates:
        if path.exists():
            return path
    return None


@lru_cache(maxsize=2048)
def _read_rollout_usage_cached(path_str: str, mtime_ns: int, size: int) -> dict[str, int]:
    """Read the latest token counters from a rollout JSONL file.

    Codex emits repeated token_count events. Prefer the latest cumulative
    total_token_usage when available. Otherwise fall back to summing unique
    last_token_usage increments, skipping duplicated consecutive events.
    """
    del mtime_ns, size

    latest_total: Optional[dict[str, int]] = None
    incremental = _empty_usage_breakdown()
    previous_increment_key: Optional[tuple[int, int, int, int, int]] = None

    try:
        with open(path_str) as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                payload = entry.get("payload") or {}
                if payload.get("type") != "token_count":
                    continue

                info = payload.get("info") or {}
                if not isinstance(info, dict):
                    continue

                total_usage = info.get("total_token_usage")
                if isinstance(total_usage, dict) and total_usage:
                    latest_total = _usage_from_token_payload(total_usage)
                    continue

                last_usage = info.get("last_token_usage")
                if isinstance(last_usage, dict) and last_usage:
                    increment = _usage_from_token_payload(last_usage)
                    increment_key = (
                        increment["inputTokens"],
                        increment["cachedTokens"],
                        increment["outputTokens"],
                        increment["reasoningOutputTokens"],
                        increment["totalTokens"],
                    )
                    if increment_key != previous_increment_key:
                        _merge_usage(incremental, increment)
                        previous_increment_key = increment_key
    except OSError as e:
        log.debug("Failed to read Codex rollout %s: %s", path_str, e)

    return latest_total or incremental


def _load_rollout_usage(raw_path: str) -> dict[str, int]:
    path = _resolve_rollout_path(raw_path)
    if not path:
        return _empty_usage_breakdown()

    try:
        stat = path.stat()
    except OSError:
        return _empty_usage_breakdown()

    return dict(_read_rollout_usage_cached(str(path), stat.st_mtime_ns, stat.st_size))


def _estimate_session_cost(
    model: str,
    provider: str,
    usage: Optional[dict[str, int]],
    tokens_used: int,
) -> tuple[float, float]:
    """Estimate cost for a session. Returns (actual_cost, list_price_equivalent)."""
    pricing = _get_list_pricing(model)

    has_detailed_usage = bool(
        usage and any(int(usage.get(key, 0) or 0) for key in ("inputTokens", "cachedTokens", "outputTokens"))
    )

    if has_detailed_usage and usage is not None:
        input_tokens = int(usage.get("inputTokens", 0) or 0)
        cached_tokens = min(input_tokens, int(usage.get("cachedTokens", 0) or 0))
        output_tokens = int(usage.get("outputTokens", 0) or 0)
        uncached_input_tokens = max(0, input_tokens - cached_tokens)
        list_price = (
            uncached_input_tokens * pricing["input"]
            + cached_tokens * pricing["cached_input"]
            + output_tokens * pricing["output"]
        ) / 1_000_000
    else:
        input_tokens = int(tokens_used * 0.3)
        output_tokens = tokens_used - input_tokens
        list_price = (
            input_tokens * pricing["input"]
            + output_tokens * pricing["output"]
        ) / 1_000_000

    actual_cost = 0.0 if provider.lower() in OCA_PROVIDERS else list_price
    return actual_cost, list_price


def _connect_readonly() -> Optional[sqlite3.Connection]:
    """Open Codex state DB in read-only mode."""
    if not STATE_DB.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{STATE_DB}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        log.debug("Failed to open Codex state DB: %s", e)
        return None


def get_codex_stats(hours: Optional[int] = None, project: Optional[str] = None) -> dict:
    """Get comprehensive Codex CLI usage stats."""
    conn = _connect_readonly()
    if not conn:
        return {"available": False, "error": "Codex CLI state DB not found"}

    try:
        where = ""
        params: list = []
        if hours:
            cutoff = int(datetime.now(timezone.utc).timestamp()) - (hours * 3600)
            where = "WHERE created_at > ?"
            params = [cutoff]

        rows = conn.execute(
            f"""SELECT id, rollout_path, created_at, updated_at, model, model_provider,
                       tokens_used, title, cwd, cli_version, sandbox_policy,
                       approval_mode, agent_nickname, first_user_message
                FROM threads {where}
                ORDER BY created_at DESC""",
            params,
        ).fetchall()

        sessions = []
        by_model: dict[str, dict] = {}
        by_provider: dict[str, dict] = {}
        total_tokens = 0
        total_input_tokens = 0
        total_output_tokens = 0
        total_cached_tokens = 0
        total_reasoning_output_tokens = 0
        total_real_net_tokens = 0
        total_actual_cost = 0.0
        total_list_price = 0.0
        detailed_session_count = 0

        project_filter = project

        for row in rows:
            model = row["model"] or "unknown"
            provider = row["model_provider"] or "unknown"
            cwd = row["cwd"] or ""
            session_project = cwd.rstrip("/").rsplit("/", 1)[-1] if cwd else "unknown"
            if project_filter and session_project != project_filter:
                continue

            usage = _load_rollout_usage(row["rollout_path"] or "")
            has_detailed_usage = bool(
                any(int(usage.get(key, 0) or 0) for key in ("inputTokens", "cachedTokens", "outputTokens", "totalTokens"))
            )

            tokens_used = int(row["tokens_used"] or 0)
            if usage["totalTokens"] <= 0 and tokens_used > 0:
                usage["totalTokens"] = tokens_used
                if usage["realNetTokens"] <= 0 and not has_detailed_usage:
                    usage["realNetTokens"] = tokens_used

            actual_cost, list_price = _estimate_session_cost(
                model,
                provider,
                usage if has_detailed_usage else None,
                usage["totalTokens"] or tokens_used,
            )

            created = row["created_at"] or 0
            created_dt = datetime.fromtimestamp(created)
            session_tokens = usage["totalTokens"] or tokens_used
            session_actual_cost = round(actual_cost, 4)
            session_list_price = round(list_price, 4)

            if has_detailed_usage:
                detailed_session_count += 1

            sessions.append({
                "sessionId": row["id"],
                "model": model,
                "provider": provider,
                "tokensUsed": session_tokens,
                "inputTokens": usage["inputTokens"],
                "outputTokens": usage["outputTokens"],
                "cachedTokens": usage["cachedTokens"],
                "reasoningOutputTokens": usage["reasoningOutputTokens"],
                "realNetTokens": usage["realNetTokens"],
                "hasDetailedUsage": has_detailed_usage,
                "usagePrecision": "rollout_events" if has_detailed_usage else "thread_totals",
                "actualCostUSD": session_actual_cost,
                "listPriceUSD": session_list_price,
                "project": session_project,
                "cwd": cwd,
                "title": row["title"] or "",
                "firstMessage": row["first_user_message"] or "",
                "createdAt": created_dt.isoformat(),
                "date": created_dt.strftime("%Y-%m-%d"),
                "cliVersion": row["cli_version"] or "",
            })

            total_tokens += session_tokens
            total_input_tokens += usage["inputTokens"]
            total_output_tokens += usage["outputTokens"]
            total_cached_tokens += usage["cachedTokens"]
            total_reasoning_output_tokens += usage["reasoningOutputTokens"]
            total_real_net_tokens += usage["realNetTokens"]
            total_actual_cost += actual_cost
            total_list_price += list_price

            if model not in by_model:
                by_model[model] = {
                    "tokens": 0,
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "cachedTokens": 0,
                    "reasoningOutputTokens": 0,
                    "realNetTokens": 0,
                    "sessions": 0,
                    "actualCostUSD": 0.0,
                    "listPriceUSD": 0.0,
                    "externalTokens": 0,
                    "externalSessions": 0,
                    "externalActualCostUSD": 0.0,
                    "externalListPriceUSD": 0.0,
                    "ocaTokens": 0,
                    "ocaSessions": 0,
                    "providers": set(),
                }
            model_agg = by_model[model]
            model_agg["tokens"] += session_tokens
            model_agg["inputTokens"] += usage["inputTokens"]
            model_agg["outputTokens"] += usage["outputTokens"]
            model_agg["cachedTokens"] += usage["cachedTokens"]
            model_agg["reasoningOutputTokens"] += usage["reasoningOutputTokens"]
            model_agg["realNetTokens"] += usage["realNetTokens"]
            model_agg["sessions"] += 1
            model_agg["actualCostUSD"] += actual_cost
            model_agg["listPriceUSD"] += list_price
            model_agg["providers"].add(provider)
            if provider.lower() in OCA_PROVIDERS:
                model_agg["ocaTokens"] += session_tokens
                model_agg["ocaSessions"] += 1
            else:
                model_agg["externalTokens"] += session_tokens
                model_agg["externalSessions"] += 1
                model_agg["externalActualCostUSD"] += actual_cost
                model_agg["externalListPriceUSD"] += list_price

            if provider not in by_provider:
                by_provider[provider] = {
                    "tokens": 0,
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "cachedTokens": 0,
                    "reasoningOutputTokens": 0,
                    "realNetTokens": 0,
                    "sessions": 0,
                    "actualCostUSD": 0.0,
                    "listPriceUSD": 0.0,
                    "isOCA": provider.lower() in OCA_PROVIDERS,
                }
            provider_agg = by_provider[provider]
            provider_agg["tokens"] += session_tokens
            provider_agg["inputTokens"] += usage["inputTokens"]
            provider_agg["outputTokens"] += usage["outputTokens"]
            provider_agg["cachedTokens"] += usage["cachedTokens"]
            provider_agg["reasoningOutputTokens"] += usage["reasoningOutputTokens"]
            provider_agg["realNetTokens"] += usage["realNetTokens"]
            provider_agg["sessions"] += 1
            provider_agg["actualCostUSD"] += actual_cost
            provider_agg["listPriceUSD"] += list_price

        daily: dict[str, dict] = {}
        for session in sessions:
            day_key = session["date"]
            if day_key not in daily:
                daily[day_key] = {
                    "date": day_key,
                    "tokens": 0,
                    "inputTokens": 0,
                    "outputTokens": 0,
                    "cachedTokens": 0,
                    "reasoningOutputTokens": 0,
                    "realNetTokens": 0,
                    "sessions": 0,
                    "actualCostUSD": 0.0,
                    "listPriceUSD": 0.0,
                    "models": set(),
                }
            day = daily[day_key]
            day["tokens"] += session["tokensUsed"]
            day["inputTokens"] += session["inputTokens"]
            day["outputTokens"] += session["outputTokens"]
            day["cachedTokens"] += session["cachedTokens"]
            day["reasoningOutputTokens"] += session["reasoningOutputTokens"]
            day["realNetTokens"] += session["realNetTokens"]
            day["sessions"] += 1
            day["actualCostUSD"] += session["actualCostUSD"]
            day["listPriceUSD"] += session["listPriceUSD"]
            day["models"].add(session["model"])

        daily_list = []
        for day in sorted(daily.values(), key=lambda item: item["date"]):
            day["models"] = sorted(day["models"])
            day["actualCostUSD"] = round(day["actualCostUSD"], 4)
            day["listPriceUSD"] = round(day["listPriceUSD"], 4)
            daily_list.append(day)

        for aggregate in by_model.values():
            aggregate["actualCostUSD"] = round(aggregate["actualCostUSD"], 4)
            aggregate["listPriceUSD"] = round(aggregate["listPriceUSD"], 4)
            aggregate["externalActualCostUSD"] = round(aggregate["externalActualCostUSD"], 4)
            aggregate["externalListPriceUSD"] = round(aggregate["externalListPriceUSD"], 4)
            aggregate["providers"] = sorted(aggregate["providers"])

        for aggregate in by_provider.values():
            aggregate["actualCostUSD"] = round(aggregate["actualCostUSD"], 4)
            aggregate["listPriceUSD"] = round(aggregate["listPriceUSD"], 4)

        precision = "thread_totals"
        if detailed_session_count:
            precision = "rollout_usage" if detailed_session_count == len(sessions) else "mixed"

        return {
            "available": True,
            "precision": precision,
            "detailedSessionCount": detailed_session_count,
            "totalSessions": len(sessions),
            "totalTokens": total_tokens,
            "totalInputTokens": total_input_tokens,
            "totalOutputTokens": total_output_tokens,
            "totalCachedTokens": total_cached_tokens,
            "totalReasoningOutputTokens": total_reasoning_output_tokens,
            "totalRealNetTokens": total_real_net_tokens,
            "totalActualCostUSD": round(total_actual_cost, 4),
            "totalListPriceUSD": round(total_list_price, 4),
            "savedByOCA": round(total_list_price - total_actual_cost, 4),
            "byModel": by_model,
            "byProvider": by_provider,
            "daily": daily_list,
            "sessions": sessions,
        }

    except sqlite3.Error as e:
        log.error("Error reading Codex stats: %s", e)
        return {"available": False, "error": str(e)}
    finally:
        conn.close()


def get_codex_today() -> dict:
    """Get today's Codex usage only."""
    stats = get_codex_stats()
    if not stats.get("available"):
        return stats

    today_str = date.today().isoformat()
    today_sessions = [session for session in stats["sessions"] if session["date"] == today_str]
    today_tokens = sum(session["tokensUsed"] for session in today_sessions)
    today_input = sum(session["inputTokens"] for session in today_sessions)
    today_output = sum(session["outputTokens"] for session in today_sessions)
    today_cached = sum(session["cachedTokens"] for session in today_sessions)
    today_real_net = sum(session["realNetTokens"] for session in today_sessions)
    today_actual = sum(session["actualCostUSD"] for session in today_sessions)
    today_list = sum(session["listPriceUSD"] for session in today_sessions)

    return {
        "available": True,
        "date": today_str,
        "sessions": len(today_sessions),
        "tokens": today_tokens,
        "inputTokens": today_input,
        "outputTokens": today_output,
        "cachedTokens": today_cached,
        "realNetTokens": today_real_net,
        "actualCostUSD": round(today_actual, 4),
        "listPriceUSD": round(today_list, 4),
        "savedByOCA": round(today_list - today_actual, 4),
        "details": today_sessions,
    }
