"""
Claude Code stats integration — reads token usage, costs, and session history
from Claude Code's internal stats files (~/.claude/).

Provides read-only access to:
- Per-model token usage (input, output, cache read, cache creation)
- Daily activity (messages, sessions, tool calls)
- Daily model token breakdown
- Session history (from history.jsonl)
"""

import json
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger("multillm.claude_stats")

CLAUDE_DIR = Path.home() / ".claude"
STATS_FILE = CLAUDE_DIR / "stats-cache.json"
HISTORY_FILE = CLAUDE_DIR / "history.jsonl"

# Anthropic pricing per 1M tokens (estimated for Max plan / API)
CLAUDE_PRICING = {
    "claude-opus-4-5-20251101": {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_create": 18.75},
    "claude-opus-4-6":          {"input": 15.0, "output": 75.0, "cache_read": 1.5, "cache_create": 18.75},
    "claude-sonnet-4-6":        {"input": 3.0,  "output": 15.0, "cache_read": 0.3, "cache_create": 3.75},
    "claude-haiku-4-5-20251001": {"input": 0.80, "output": 4.0, "cache_read": 0.08, "cache_create": 1.0},
}


def _load_stats() -> dict:
    """Load Claude Code stats-cache.json."""
    if not STATS_FILE.exists():
        return {}
    try:
        with open(STATS_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.debug("Failed to load Claude stats: %s", e)
        return {}


def _estimate_cost(model: str, usage: dict) -> float:
    """Estimate cost based on model pricing."""
    pricing = CLAUDE_PRICING.get(model)
    if not pricing:
        # Try prefix matching
        for k, v in CLAUDE_PRICING.items():
            if model.startswith(k.rsplit("-", 1)[0]):
                pricing = v
                break
    if not pricing:
        return 0.0

    return (
        usage.get("inputTokens", 0) * pricing["input"]
        + usage.get("outputTokens", 0) * pricing["output"]
        + usage.get("cacheReadInputTokens", 0) * pricing["cache_read"]
        + usage.get("cacheCreationInputTokens", 0) * pricing["cache_create"]
    ) / 1_000_000


def get_claude_code_stats() -> dict:
    """Get comprehensive Claude Code usage stats."""
    stats = _load_stats()
    if not stats:
        return {"available": False, "error": "No Claude Code stats found"}

    # Model usage with estimated costs
    model_usage = {}
    for model, usage in stats.get("modelUsage", {}).items():
        cost = _estimate_cost(model, usage)
        model_usage[model] = {
            **usage,
            "estimatedCostUSD": round(cost, 2),
        }

    # Daily activity (last 30 days)
    daily = stats.get("dailyActivity", [])[-30:]

    # Daily model tokens (last 30 days)
    daily_model = stats.get("dailyModelTokens", [])[-30:]

    # Session history summary
    history = _load_session_history(limit=50)

    # Latest day's usage for limit tracking
    # Use today's data if available, otherwise fall back to the most recent day
    from datetime import date
    today_str = date.today().isoformat()
    latest_tokens = {}
    latest_activity = {}
    latest_date = ""
    for entry in daily_model:
        if entry.get("date") == today_str:
            latest_tokens = entry.get("tokensByModel", {})
            latest_date = today_str
    if not latest_tokens and daily_model:
        last = daily_model[-1]
        latest_tokens = last.get("tokensByModel", {})
        latest_date = last.get("date", "")
    for entry in daily:
        if entry.get("date") in (today_str, latest_date):
            latest_activity = entry

    return {
        "available": True,
        "totalSessions": stats.get("totalSessions", 0),
        "totalMessages": stats.get("totalMessages", 0),
        "firstSessionDate": stats.get("firstSessionDate"),
        "longestSession": stats.get("longestSession"),
        "modelUsage": model_usage,
        "dailyActivity": daily,
        "dailyModelTokens": daily_model,
        "sessionHistory": history,
        "latestTokens": latest_tokens,
        "latestDate": latest_date,
        "latestActivity": latest_activity,
    }


def _load_session_history(limit: int = 50) -> list[dict]:
    """Load recent session history from history.jsonl."""
    if not HISTORY_FILE.exists():
        return []

    entries = []
    try:
        with open(HISTORY_FILE) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    entries.append(entry)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []

    # Group by session, return last N sessions
    sessions: dict[str, dict] = {}
    for e in entries:
        sid = e.get("sessionId", "unknown")
        if sid not in sessions:
            sessions[sid] = {
                "sessionId": sid,
                "project": e.get("project", ""),
                "firstCommand": e.get("display", ""),
                "timestamp": e.get("timestamp", 0),
                "commandCount": 0,
            }
        sessions[sid]["commandCount"] += 1
        sessions[sid]["lastTimestamp"] = e.get("timestamp", 0)

    # Sort by timestamp descending, return last N
    sorted_sessions = sorted(sessions.values(), key=lambda s: s.get("timestamp", 0), reverse=True)
    return sorted_sessions[:limit]
