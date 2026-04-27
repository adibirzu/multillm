"""
Gemini CLI stats integration — reads token usage and session history
from Gemini CLI's local session files (~/.gemini/tmp/*/chats/).

Provides read-only access to:
- Per-session token usage (input, output, cached, thoughts)
- Session history with project mapping
- Aggregated daily and per-project usage
"""

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from .stats_cache import ttl_cache

log = logging.getLogger("multillm.gemini_stats")

GEMINI_DIR = Path.home() / ".gemini"
SESSIONS_DIR = GEMINI_DIR / "tmp"
PROJECTS_FILE = GEMINI_DIR / "projects.json"

# Google Gemini API pricing per 1M tokens (USD) — source: ai.google.dev/pricing (March 2026)
# Gemini CLI uses Gemini 2.5 Pro by default
GEMINI_PRICING: dict[str, dict[str, float]] = {
    "gemini-2.5-pro":   {"input": 1.25, "output": 10.0, "cached_input": 0.3125},
    "gemini-2.5-flash": {"input": 0.15, "output": 0.60, "cached_input": 0.0375},
    "gemini-2.0-flash": {"input": 0.10, "output": 0.40, "cached_input": 0.025},
    # Default for Gemini CLI (typically 2.5 Pro)
    "default":          {"input": 1.25, "output": 10.0, "cached_input": 0.3125},
}


def _detect_session_model(data: dict) -> str:
    """Best-effort model detection for a Gemini CLI session."""
    model = data.get("model")
    if model:
        return str(model)

    for msg in reversed(data.get("messages", [])):
        for key in ("model", "modelId", "modelName"):
            value = msg.get(key)
            if value:
                return str(value)
        metadata = msg.get("metadata", {}) or {}
        for key in ("model", "modelId", "modelName"):
            value = metadata.get(key)
            if value:
                return str(value)

    return "gemini-2.5-pro"


def _load_project_map() -> dict[str, str]:
    """Load Gemini CLI project path → name mapping."""
    if not PROJECTS_FILE.exists():
        return {}
    try:
        with open(PROJECTS_FILE) as f:
            data = json.load(f)
        return data.get("projects", {})
    except (json.JSONDecodeError, OSError):
        return {}


def _resolve_project_name(proj_hash: str, project_map: dict[str, str]) -> str:
    """Resolve a project hash/name to a human-readable project name."""
    # If it's already a readable name (not a hash), return it
    if len(proj_hash) < 40:
        return proj_hash
    # Check reverse lookup from project_map (path → name)
    for path, name in project_map.items():
        # Gemini CLI uses hash of project path for tmp dirs
        import hashlib
        path_hash = hashlib.sha256(path.encode()).hexdigest()
        if path_hash == proj_hash:
            return name
    return proj_hash[:12] + "..."


def _estimate_cost(input_tokens: int, output_tokens: int, cached_tokens: int) -> float:
    """Estimate cost using Gemini 2.5 Pro pricing (default for CLI)."""
    pricing = GEMINI_PRICING["default"]
    # Cached tokens are a subset of input — subtract them for billing
    billable_input = max(0, input_tokens - cached_tokens)
    return (
        billable_input * pricing["input"]
        + cached_tokens * pricing["cached_input"]
        + output_tokens * pricing["output"]
    ) / 1_000_000


@ttl_cache(seconds=15.0, maxsize=128)
def get_gemini_stats(hours: Optional[int] = None, project: Optional[str] = None) -> dict:
    """Get comprehensive Gemini CLI usage stats."""
    if not SESSIONS_DIR.exists():
        return {"available": False, "error": "Gemini CLI sessions dir not found"}

    project_map = _load_project_map()

    cutoff_ts = None
    if hours:
        cutoff_ts = datetime.now().timestamp() - (hours * 3600)

    sessions = []
    by_project: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    daily: dict[str, dict] = {}
    total_input = 0
    total_output = 0
    total_cached = 0
    total_cost = 0.0

    # Scan all session files
    for session_file in SESSIONS_DIR.glob("*/chats/session-*.json"):
        try:
            with open(session_file) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        # Parse session metadata
        start_time = data.get("startTime", "")
        last_updated = data.get("lastUpdated", "")

        # Extract date from filename: session-YYYY-MM-DDTHH-MM-*.json
        fname = session_file.name
        try:
            session_date = fname.split("session-")[1][:10]
        except (IndexError, ValueError):
            session_date = ""

        # Apply time filter based on filename date
        if cutoff_ts and session_date:
            try:
                session_dt = datetime.strptime(session_date, "%Y-%m-%d")
                if session_dt.timestamp() < cutoff_ts:
                    continue
            except ValueError:
                pass

        # Determine project
        proj_dir = session_file.parent.parent.name  # tmp/<project>/chats/
        project_name = _resolve_project_name(proj_dir, project_map)
        if project and project_name != project:
            continue
        session_model = _detect_session_model(data)

        # Sum tokens across all messages
        ses_input = 0
        ses_output = 0
        ses_cached = 0
        ses_thoughts = 0
        msg_count = 0

        for msg in data.get("messages", []):
            tok = msg.get("tokens", {})
            ses_input += tok.get("input", 0)
            ses_output += tok.get("output", 0)
            ses_cached += tok.get("cached", 0)
            ses_thoughts += tok.get("thoughts", 0)
            msg_count += 1

        cost = _estimate_cost(ses_input, ses_output, ses_cached)

        sessions.append({
            "sessionId": data.get("sessionId", fname),
            "project": project_name,
            "projectHash": proj_dir,
            "model": session_model,
            "date": session_date,
            "startTime": start_time,
            "lastUpdated": last_updated,
            "messageCount": msg_count,
            "inputTokens": ses_input,
            "outputTokens": ses_output,
            "cachedTokens": ses_cached,
            "thoughtTokens": ses_thoughts,
            "totalTokens": ses_input + ses_output,
            "estimatedCostUSD": round(cost, 4),
        })

        total_input += ses_input
        total_output += ses_output
        total_cached += ses_cached
        total_cost += cost

        # Aggregate by project
        if project_name not in by_project:
            by_project[project_name] = {
                "sessions": 0, "inputTokens": 0, "outputTokens": 0,
                "cachedTokens": 0, "totalTokens": 0, "costUSD": 0.0,
            }
        agg = by_project[project_name]
        agg["sessions"] += 1
        agg["inputTokens"] += ses_input
        agg["outputTokens"] += ses_output
        agg["cachedTokens"] += ses_cached
        agg["totalTokens"] += ses_input + ses_output
        agg["costUSD"] += cost

        # Aggregate by model
        if session_model not in by_model:
            by_model[session_model] = {
                "sessions": 0,
                "inputTokens": 0,
                "outputTokens": 0,
                "cachedTokens": 0,
                "thoughtTokens": 0,
                "totalTokens": 0,
                "costUSD": 0.0,
            }
        model_agg = by_model[session_model]
        model_agg["sessions"] += 1
        model_agg["inputTokens"] += ses_input
        model_agg["outputTokens"] += ses_output
        model_agg["cachedTokens"] += ses_cached
        model_agg["thoughtTokens"] += ses_thoughts
        model_agg["totalTokens"] += ses_input + ses_output
        model_agg["costUSD"] += cost

        # Aggregate daily
        if session_date:
            if session_date not in daily:
                daily[session_date] = {
                    "date": session_date, "sessions": 0,
                    "inputTokens": 0, "outputTokens": 0,
                    "cachedTokens": 0, "totalTokens": 0, "costUSD": 0.0,
                }
            day = daily[session_date]
            day["sessions"] += 1
            day["inputTokens"] += ses_input
            day["outputTokens"] += ses_output
            day["cachedTokens"] += ses_cached
            day["totalTokens"] += ses_input + ses_output
            day["costUSD"] += cost

    # Sort sessions by date descending
    sessions.sort(key=lambda s: s.get("date", ""), reverse=True)

    # Round costs
    for v in by_project.values():
        v["costUSD"] = round(v["costUSD"], 4)
    for v in by_model.values():
        v["costUSD"] = round(v["costUSD"], 4)
    daily_list = []
    for d in sorted(daily.values(), key=lambda x: x["date"]):
        d["costUSD"] = round(d["costUSD"], 4)
        daily_list.append(d)

    if len(by_model) == 1:
        display_model = next(iter(by_model))
    elif len(by_model) > 1:
        display_model = "mixed"
    else:
        display_model = "gemini-2.5-pro (default)"

    return {
        "available": True,
        "totalSessions": len(sessions),
        "totalInputTokens": total_input,
        "totalOutputTokens": total_output,
        "totalCachedTokens": total_cached,
        "totalTokens": total_input + total_output,
        "totalEstimatedCostUSD": round(total_cost, 4),
        "model": display_model,
        "byModel": by_model,
        "byProject": by_project,
        "daily": daily_list,
        "sessions": sessions,
    }
