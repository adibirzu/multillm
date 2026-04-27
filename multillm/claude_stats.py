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
from datetime import datetime, timezone, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .stats_cache import ttl_cache

log = logging.getLogger("multillm.claude_stats")

CLAUDE_DIR = Path.home() / ".claude"
STATS_FILE = CLAUDE_DIR / "stats-cache.json"
HISTORY_FILE = CLAUDE_DIR / "history.jsonl"
PROJECTS_DIR = CLAUDE_DIR / "projects"

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


def _normalize_usage(usage: dict) -> dict:
    return {
        "inputTokens": int(usage.get("inputTokens", 0) or 0),
        "outputTokens": int(usage.get("outputTokens", 0) or 0),
        "cacheReadInputTokens": int(usage.get("cacheReadInputTokens", 0) or 0),
        "cacheCreationInputTokens": int(usage.get("cacheCreationInputTokens", 0) or 0),
    }


def _with_estimated_cost(model_usage: dict[str, dict]) -> dict[str, dict]:
    enriched: dict[str, dict] = {}
    for model, usage in model_usage.items():
        normalized = _normalize_usage(usage)
        normalized["estimatedCostUSD"] = round(_estimate_cost(model, normalized), 4)
        enriched[model] = normalized
    return enriched


def _local_day(timestamp: datetime) -> str:
    return timestamp.astimezone().date().isoformat()


def _parse_timestamp(value: object) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _decode_project_dir(project_dir: str) -> str:
    parts = [part for part in project_dir.split("-") if part]
    return parts[-1] if parts else project_dir


def _resolve_project_name(cwd: object, project_dir: str) -> str:
    if isinstance(cwd, str) and cwd:
        name = Path(cwd).name
        if name:
            return name
    return _decode_project_dir(project_dir)


def _extract_user_text(message: object) -> str:
    if isinstance(message, str):
        return message
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str) and text:
                    return text
    return ""


def _empty_session(session_id: str, project: str, cwd: str) -> dict:
    return {
        "sessionId": session_id,
        "project": project,
        "cwd": cwd,
        "firstCommand": "",
        "timestamp": "",
        "lastTimestamp": "",
        "commandCount": 0,
        "messageCount": 0,
        "inputTokens": 0,
        "outputTokens": 0,
        "cacheReadInputTokens": 0,
        "cacheCreationInputTokens": 0,
        "estimatedCostUSD": 0.0,
        "models_used": set(),
        "_matched": False,
    }


def _add_usage(target: dict, usage: dict) -> None:
    target["inputTokens"] += int(usage.get("inputTokens", 0) or 0)
    target["outputTokens"] += int(usage.get("outputTokens", 0) or 0)
    target["cacheReadInputTokens"] += int(usage.get("cacheReadInputTokens", 0) or 0)
    target["cacheCreationInputTokens"] += int(usage.get("cacheCreationInputTokens", 0) or 0)


@lru_cache(maxsize=4096)
def _read_project_session_events_cached(path_str: str, mtime_ns: int, size: int, project_dir: str) -> tuple[dict, ...]:
    del mtime_ns, size

    events: list[dict] = []
    try:
        with open(path_str) as handle:
            for raw_line in handle:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    entry = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue

                timestamp = _parse_timestamp(entry.get("timestamp"))
                cwd = entry.get("cwd", "") or ""
                session_project = _resolve_project_name(cwd, project_dir)
                session_id = entry.get("sessionId") or Path(path_str).stem
                event = {
                    "timestamp": timestamp,
                    "cwd": cwd,
                    "project": session_project,
                    "sessionId": session_id,
                    "entryType": entry.get("type"),
                    "firstCommand": "",
                    "model": "",
                    "usage": None,
                }

                message = entry.get("message")
                if entry.get("type") == "user":
                    event["firstCommand"] = _extract_user_text(message)
                elif entry.get("type") == "assistant" and isinstance(message, dict):
                    usage_payload = message.get("usage") or {}
                    model = str(message.get("model") or "")
                    if model:
                        event["model"] = model
                        event["usage"] = {
                            "inputTokens": int(usage_payload.get("input_tokens", 0) or 0),
                            "outputTokens": int(usage_payload.get("output_tokens", 0) or 0),
                            "cacheReadInputTokens": int(usage_payload.get("cache_read_input_tokens", 0) or 0),
                            "cacheCreationInputTokens": int(usage_payload.get("cache_creation_input_tokens", 0) or 0),
                        }
                events.append(event)
    except OSError:
        return ()

    return tuple(events)


def _read_project_session_events(session_file: Path, project_dir: str) -> tuple[dict, ...]:
    try:
        stat = session_file.stat()
    except OSError:
        return ()
    return _read_project_session_events_cached(str(session_file), stat.st_mtime_ns, stat.st_size, project_dir)


def _load_windowed_stats(hours: Optional[int], project: Optional[str]) -> Optional[dict]:
    if not PROJECTS_DIR.exists():
        return None

    cutoff = None
    if hours:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    model_usage: dict[str, dict] = {}
    daily_tokens: dict[str, dict[str, int]] = {}
    daily_activity: dict[str, dict] = {}
    sessions: dict[str, dict] = {}

    for session_file in PROJECTS_DIR.glob("*/*.jsonl"):
        try:
            if cutoff is not None and session_file.stat().st_mtime < cutoff.timestamp():
                continue
        except OSError:
            continue

        project_dir = session_file.parent.name
        for event in _read_project_session_events(session_file, project_dir):
            timestamp = event.get("timestamp")
            if cutoff is not None and (timestamp is None or timestamp < cutoff):
                continue

            session_project = event.get("project", "unknown")
            if project and session_project != project:
                continue

            session_id = event.get("sessionId") or session_file.stem
            cwd = event.get("cwd", "") or ""
            session = sessions.setdefault(session_id, _empty_session(session_id, session_project, cwd))
            session["_matched"] = True

            if timestamp is not None:
                iso_timestamp = timestamp.isoformat()
                if not session["timestamp"] or iso_timestamp < session["timestamp"]:
                    session["timestamp"] = iso_timestamp
                if not session["lastTimestamp"] or iso_timestamp > session["lastTimestamp"]:
                    session["lastTimestamp"] = iso_timestamp
                day = _local_day(timestamp)
                day_activity = daily_activity.setdefault(
                    day,
                    {"date": day, "messageCount": 0, "sessionIds": set()},
                )
                day_activity["sessionIds"].add(session_id)

            if event.get("entryType") == "user":
                session["commandCount"] += 1
                if not session["firstCommand"]:
                    session["firstCommand"] = event.get("firstCommand", "")
                continue

            model = event.get("model", "")
            usage = event.get("usage")
            if not model or not isinstance(usage, dict) or timestamp is None:
                continue

            session["messageCount"] += 1
            _add_usage(session, usage)
            session["estimatedCostUSD"] += _estimate_cost(model, usage)
            session["models_used"].add(model)

            aggregate = model_usage.setdefault(model, _normalize_usage({}))
            _add_usage(aggregate, usage)

            day = _local_day(timestamp)
            token_total = (
                usage["inputTokens"]
                + usage["outputTokens"]
                + usage["cacheReadInputTokens"]
                + usage["cacheCreationInputTokens"]
            )
            daily_tokens.setdefault(day, {})
            daily_tokens[day][model] = daily_tokens[day].get(model, 0) + token_total
            daily_activity.setdefault(day, {"date": day, "messageCount": 0, "sessionIds": set()})
            daily_activity[day]["messageCount"] += 1

    filtered_sessions = []
    for session in sessions.values():
        if not session.pop("_matched", False):
            continue
        session["models_used"] = sorted(session["models_used"])
        session["estimatedCostUSD"] = round(session["estimatedCostUSD"], 4)
        filtered_sessions.append(session)

    filtered_sessions.sort(key=lambda item: item.get("lastTimestamp", ""), reverse=True)

    daily_model_tokens = [
        {"date": day, "tokensByModel": daily_tokens[day]}
        for day in sorted(daily_tokens)
    ]
    daily_activity_rows = []
    for day in sorted(daily_activity):
        row = daily_activity[day]
        daily_activity_rows.append({
            "date": day,
            "messageCount": row.get("messageCount", 0),
            "sessionCount": len(row.get("sessionIds", set())),
        })

    latest_tokens = {}
    latest_activity = {}
    latest_date = daily_model_tokens[-1]["date"] if daily_model_tokens else ""
    if latest_date:
        latest_tokens = daily_model_tokens[-1]["tokensByModel"]
        latest_activity = next((row for row in daily_activity_rows if row.get("date") == latest_date), {})

    return {
        "totalSessions": len(filtered_sessions),
        "totalMessages": sum(session.get("messageCount", 0) for session in filtered_sessions),
        "modelUsage": _with_estimated_cost(model_usage),
        "dailyActivity": daily_activity_rows,
        "dailyModelTokens": daily_model_tokens,
        "sessionHistory": filtered_sessions[:50],
        "latestTokens": latest_tokens,
        "latestDate": latest_date,
        "latestActivity": latest_activity,
        "precision": "message_usage",
    }


def _build_latest_day_summary(daily: list[dict], daily_model: list[dict]) -> tuple[dict, str, dict]:
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
    return latest_tokens, latest_date, latest_activity


@ttl_cache(seconds=15.0, maxsize=128)
def get_claude_code_stats(hours: Optional[int] = None, project: Optional[str] = None) -> dict:
    """Get comprehensive Claude Code usage stats."""
    stats = _load_stats()
    if not stats:
        return {"available": False, "error": "No Claude Code stats found"}

    lifetime_model_usage = _with_estimated_cost(stats.get("modelUsage", {}))
    lifetime_daily = stats.get("dailyActivity", [])[-30:]
    lifetime_daily_model = stats.get("dailyModelTokens", [])[-30:]
    lifetime_history = _load_session_history(limit=50)
    latest_tokens, latest_date, latest_activity = _build_latest_day_summary(
        lifetime_daily,
        lifetime_daily_model,
    )

    filtered = _load_windowed_stats(hours=hours, project=project) if (hours or project) else None

    response = {
        "available": True,
        "totalSessions": stats.get("totalSessions", 0),
        "totalMessages": stats.get("totalMessages", 0),
        "firstSessionDate": stats.get("firstSessionDate"),
        "longestSession": stats.get("longestSession"),
        "modelUsage": lifetime_model_usage,
        "dailyActivity": lifetime_daily,
        "dailyModelTokens": lifetime_daily_model,
        "sessionHistory": lifetime_history,
        "latestTokens": latest_tokens,
        "latestDate": latest_date,
        "latestActivity": latest_activity,
    }

    if filtered is not None:
        response.update(filtered)
        response["windowHours"] = hours
        response["project"] = project

    return response


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
