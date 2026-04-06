"""Tests for Gemini CLI stats helpers."""

import json

from multillm import gemini_stats


def test_get_gemini_stats_aggregates_usage_by_model(tmp_path, monkeypatch):
    gemini_dir = tmp_path / ".gemini"
    sessions_dir = gemini_dir / "tmp" / "demo-project" / "chats"
    sessions_dir.mkdir(parents=True)
    projects_file = gemini_dir / "projects.json"
    projects_file.write_text(json.dumps({"projects": {}}))

    session_payload = {
        "sessionId": "sess-1",
        "projectHash": "demo-project",
        "startTime": "2026-04-06T10:00:00Z",
        "lastUpdated": "2026-04-06T10:05:00Z",
        "messages": [
            {
                "id": "m1",
                "timestamp": "2026-04-06T10:00:01Z",
                "type": "assistant",
                "model": "gemini-2.5-pro",
                "tokens": {
                    "input": 100,
                    "output": 40,
                    "cached": 10,
                    "thoughts": 5,
                    "total": 155,
                },
            },
            {
                "id": "m2",
                "timestamp": "2026-04-06T10:03:00Z",
                "type": "assistant",
                "model": "gemini-2.5-pro",
                "tokens": {
                    "input": 200,
                    "output": 60,
                    "cached": 20,
                    "thoughts": 7,
                    "total": 287,
                },
            },
        ],
    }
    (sessions_dir / "session-2026-04-06T10-00-sess-1.json").write_text(json.dumps(session_payload))

    monkeypatch.setattr(gemini_stats, "SESSIONS_DIR", gemini_dir / "tmp")
    monkeypatch.setattr(gemini_stats, "PROJECTS_FILE", projects_file)

    stats = gemini_stats.get_gemini_stats()

    assert stats["available"] is True
    assert stats["totalTokens"] == 400
    assert stats["byModel"]["gemini-2.5-pro"]["sessions"] == 1
    assert stats["byModel"]["gemini-2.5-pro"]["inputTokens"] == 300
    assert stats["byModel"]["gemini-2.5-pro"]["outputTokens"] == 100
    assert stats["byModel"]["gemini-2.5-pro"]["cachedTokens"] == 30
    assert stats["byModel"]["gemini-2.5-pro"]["totalTokens"] == 400

