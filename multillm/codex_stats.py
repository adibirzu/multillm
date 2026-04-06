"""
Codex CLI stats integration — reads token usage, costs, and session history
from Codex CLI's internal SQLite database (~/.codex/).

Provides read-only access to:
- Per-session token usage, model, provider
- Session history with project/cwd info
- Aggregated daily usage
"""

import logging
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("multillm.codex_stats")

CODEX_DIR = Path.home() / ".codex"
STATE_DB = CODEX_DIR / "state_5.sqlite"
HISTORY_FILE = CODEX_DIR / "history.jsonl"

# Pricing per 1M tokens (USD) — OCA is internal/free, OpenAI has public pricing
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
    # Direct match
    pricing = OPENAI_LIST_PRICING.get(model)
    if pricing:
        return pricing
    # Strip oca/ prefix for OCA-hosted models (e.g., "oca/gpt-5.4-pro" → "gpt-5.4-pro")
    clean_model = model.split("/", 1)[-1] if "/" in model else model
    pricing = OPENAI_LIST_PRICING.get(clean_model)
    if pricing:
        return pricing
    # Prefix match (e.g., "gpt-5.4-20260301" → "gpt-5.4")
    for k, v in OPENAI_LIST_PRICING.items():
        if clean_model.startswith(k):
            return v
    # Default fallback
    return {"input": 2.50, "output": 10.0, "cached_input": 0.25}


def _estimate_session_cost(
    model: str, provider: str, tokens_used: int,
) -> tuple[float, float]:
    """Estimate cost for a session. Returns (actual_cost, list_price_equivalent).

    actual_cost: what you actually pay (0 for OCA).
    list_price_equivalent: what it would cost at OpenAI list prices.
    """
    pricing = _get_list_pricing(model)

    # Without in/out split, assume ~30% input / 70% output (typical for code gen)
    input_tokens = int(tokens_used * 0.3)
    output_tokens = tokens_used - input_tokens
    list_price = (
        input_tokens * pricing["input"]
        + output_tokens * pricing["output"]
    ) / 1_000_000

    # OCA providers = free
    is_oca = provider.lower() in OCA_PROVIDERS
    actual_cost = 0.0 if is_oca else list_price

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
        # Build time filter
        where = ""
        params: list = []
        if hours:
            cutoff = int(datetime.now(timezone.utc).timestamp()) - (hours * 3600)
            where = "WHERE created_at > ?"
            params = [cutoff]

        # Get all sessions
        rows = conn.execute(
            f"""SELECT id, created_at, updated_at, model, model_provider,
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
        total_actual_cost = 0.0
        total_list_price = 0.0

        project_filter = project

        for r in rows:
            model = r["model"] or "unknown"
            provider = r["model_provider"] or "unknown"
            tokens = r["tokens_used"] or 0
            actual_cost, list_price = _estimate_session_cost(model, provider, tokens)
            cwd = r["cwd"] or ""
            session_project = cwd.rstrip("/").rsplit("/", 1)[-1] if cwd else "unknown"
            if project_filter and session_project != project_filter:
                continue

            # Parse timestamp (Codex uses epoch seconds)
            created = r["created_at"] or 0
            created_dt = datetime.fromtimestamp(created)

            sessions.append({
                "sessionId": r["id"],
                "model": model,
                "provider": provider,
                "tokensUsed": tokens,
                "actualCostUSD": round(actual_cost, 4),
                "listPriceUSD": round(list_price, 4),
                "project": session_project,
                "cwd": cwd,
                "title": r["title"] or "",
                "firstMessage": r["first_user_message"] or "",
                "createdAt": created_dt.isoformat(),
                "date": created_dt.strftime("%Y-%m-%d"),
                "cliVersion": r["cli_version"] or "",
            })

            total_tokens += tokens
            total_actual_cost += actual_cost
            total_list_price += list_price

            # Aggregate by model
            if model not in by_model:
                by_model[model] = {
                    "tokens": 0,
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
            by_model[model]["tokens"] += tokens
            by_model[model]["sessions"] += 1
            by_model[model]["actualCostUSD"] += actual_cost
            by_model[model]["listPriceUSD"] += list_price
            by_model[model]["providers"].add(provider)
            if provider.lower() in OCA_PROVIDERS:
                by_model[model]["ocaTokens"] += tokens
                by_model[model]["ocaSessions"] += 1
            else:
                by_model[model]["externalTokens"] += tokens
                by_model[model]["externalSessions"] += 1
                by_model[model]["externalActualCostUSD"] += actual_cost
                by_model[model]["externalListPriceUSD"] += list_price

            # Aggregate by provider
            if provider not in by_provider:
                by_provider[provider] = {
                    "tokens": 0, "sessions": 0,
                    "actualCostUSD": 0.0, "listPriceUSD": 0.0,
                    "isOCA": provider.lower() in OCA_PROVIDERS,
                }
            by_provider[provider]["tokens"] += tokens
            by_provider[provider]["sessions"] += 1
            by_provider[provider]["actualCostUSD"] += actual_cost
            by_provider[provider]["listPriceUSD"] += list_price

        # Daily breakdown
        daily: dict[str, dict] = {}
        for s in sessions:
            d = s["date"]
            if d not in daily:
                daily[d] = {
                    "date": d, "tokens": 0, "sessions": 0,
                    "actualCostUSD": 0.0, "listPriceUSD": 0.0, "models": set(),
                }
            daily[d]["tokens"] += s["tokensUsed"]
            daily[d]["sessions"] += 1
            daily[d]["actualCostUSD"] += s["actualCostUSD"]
            daily[d]["listPriceUSD"] += s["listPriceUSD"]
            daily[d]["models"].add(s["model"])

        # Convert sets to lists, round costs
        daily_list = []
        for d in sorted(daily.values(), key=lambda x: x["date"]):
            d["models"] = sorted(d["models"])
            d["actualCostUSD"] = round(d["actualCostUSD"], 4)
            d["listPriceUSD"] = round(d["listPriceUSD"], 4)
            daily_list.append(d)

        for v in by_model.values():
            v["actualCostUSD"] = round(v["actualCostUSD"], 4)
            v["listPriceUSD"] = round(v["listPriceUSD"], 4)
            v["externalActualCostUSD"] = round(v["externalActualCostUSD"], 4)
            v["externalListPriceUSD"] = round(v["externalListPriceUSD"], 4)
            v["providers"] = sorted(v["providers"])
        for v in by_provider.values():
            v["actualCostUSD"] = round(v["actualCostUSD"], 4)
            v["listPriceUSD"] = round(v["listPriceUSD"], 4)

        return {
            "available": True,
            "totalSessions": len(sessions),
            "totalTokens": total_tokens,
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
    today_sessions = [s for s in stats["sessions"] if s["date"] == today_str]
    today_tokens = sum(s["tokensUsed"] for s in today_sessions)
    today_actual = sum(s["actualCostUSD"] for s in today_sessions)
    today_list = sum(s["listPriceUSD"] for s in today_sessions)

    return {
        "available": True,
        "date": today_str,
        "sessions": len(today_sessions),
        "tokens": today_tokens,
        "actualCostUSD": round(today_actual, 4),
        "listPriceUSD": round(today_list, 4),
        "savedByOCA": round(today_list - today_actual, 4),
        "details": today_sessions,
    }
