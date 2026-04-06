"""Tests for Claude Code stats aggregation."""

import json
from datetime import datetime, timedelta, timezone

from multillm import claude_stats


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _write_jsonl(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n")


def _user_entry(timestamp: datetime, session_id: str, cwd: str, text: str) -> dict:
    return {
        "type": "user",
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "sessionId": session_id,
        "cwd": cwd,
        "message": {
            "role": "user",
            "content": text,
        },
    }


def _assistant_entry(
    timestamp: datetime,
    session_id: str,
    cwd: str,
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> dict:
    return {
        "type": "assistant",
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "sessionId": session_id,
        "cwd": cwd,
        "message": {
            "role": "assistant",
            "model": model,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cache_read_input_tokens": cache_read_tokens,
                "cache_creation_input_tokens": cache_creation_tokens,
            },
        },
    }


def test_get_claude_code_stats_applies_hours_and_project_filters(tmp_path, monkeypatch):
    stats_file = tmp_path / "stats-cache.json"
    history_file = tmp_path / "history.jsonl"
    projects_dir = tmp_path / "projects"

    _write_json(
        stats_file,
        {
            "totalSessions": 99,
            "totalMessages": 999,
            "firstSessionDate": "2026-01-01",
            "longestSession": {},
            "modelUsage": {
                "claude-sonnet-4-6": {
                    "inputTokens": 10_000,
                    "outputTokens": 5_000,
                    "cacheReadInputTokens": 1_000,
                    "cacheCreationInputTokens": 500,
                }
            },
            "dailyActivity": [],
            "dailyModelTokens": [],
        },
    )
    _write_jsonl(history_file, [])

    now = datetime.now(timezone.utc)
    recent = now - timedelta(minutes=20)
    old = now - timedelta(hours=3)

    _write_jsonl(
        projects_dir / "-Users-test-dev-multillm" / "session-a.jsonl",
        [
            _user_entry(recent - timedelta(minutes=1), "session-a", "/Users/test/dev/multillm", "Fix dashboard filters"),
            _assistant_entry(
                recent,
                "session-a",
                "/Users/test/dev/multillm",
                "claude-sonnet-4-6",
                input_tokens=120,
                output_tokens=80,
                cache_read_tokens=20,
                cache_creation_tokens=10,
            ),
        ],
    )
    _write_jsonl(
        projects_dir / "-Users-test-dev-other-project" / "session-b.jsonl",
        [
            _assistant_entry(
                recent,
                "session-b",
                "/Users/test/dev/other-project",
                "claude-opus-4-6",
                input_tokens=400,
                output_tokens=100,
            ),
        ],
    )
    _write_jsonl(
        projects_dir / "-Users-test-dev-multillm" / "session-c.jsonl",
        [
            _assistant_entry(
                old,
                "session-c",
                "/Users/test/dev/multillm",
                "claude-sonnet-4-6",
                input_tokens=900,
                output_tokens=100,
            ),
        ],
    )

    monkeypatch.setattr(claude_stats, "STATS_FILE", stats_file)
    monkeypatch.setattr(claude_stats, "HISTORY_FILE", history_file)
    monkeypatch.setattr(claude_stats, "PROJECTS_DIR", projects_dir)

    result = claude_stats.get_claude_code_stats(hours=1, project="multillm")

    assert result["available"] is True
    assert result["precision"] == "message_usage"
    assert result["totalSessions"] == 1
    assert result["totalMessages"] == 1
    assert set(result["modelUsage"]) == {"claude-sonnet-4-6"}
    assert result["modelUsage"]["claude-sonnet-4-6"]["inputTokens"] == 120
    assert result["modelUsage"]["claude-sonnet-4-6"]["outputTokens"] == 80
    assert result["modelUsage"]["claude-sonnet-4-6"]["cacheReadInputTokens"] == 20
    assert result["modelUsage"]["claude-sonnet-4-6"]["cacheCreationInputTokens"] == 10
    assert result["sessionHistory"][0]["project"] == "multillm"
    assert result["sessionHistory"][0]["firstCommand"] == "Fix dashboard filters"
    assert result["sessionHistory"][0]["inputTokens"] == 120
    assert result["sessionHistory"][0]["messageCount"] == 1

    day = recent.astimezone().date().isoformat()
    assert result["latestDate"] == day
    assert result["latestTokens"]["claude-sonnet-4-6"] == 230
    assert result["dailyModelTokens"] == [
        {"date": day, "tokensByModel": {"claude-sonnet-4-6": 230}}
    ]
    assert result["dailyActivity"] == [
        {"date": day, "messageCount": 1, "sessionCount": 1}
    ]
