# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Tests for Codex CLI stats helpers."""

import json
import sqlite3

from multillm import codex_stats


def test_get_codex_stats_reads_rollout_token_breakdowns(tmp_path, monkeypatch):
    db_path = tmp_path / "state_5.sqlite"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE threads (
            id TEXT PRIMARY KEY,
            rollout_path TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            source TEXT NOT NULL,
            model_provider TEXT NOT NULL,
            cwd TEXT NOT NULL,
            title TEXT NOT NULL,
            sandbox_policy TEXT NOT NULL,
            approval_mode TEXT NOT NULL,
            tokens_used INTEGER NOT NULL DEFAULT 0,
            has_user_event INTEGER NOT NULL DEFAULT 0,
            archived INTEGER NOT NULL DEFAULT 0,
            archived_at INTEGER,
            git_sha TEXT,
            git_branch TEXT,
            git_origin_url TEXT,
            cli_version TEXT NOT NULL DEFAULT '',
            first_user_message TEXT NOT NULL DEFAULT '',
            agent_nickname TEXT,
            agent_role TEXT,
            memory_mode TEXT NOT NULL DEFAULT 'enabled',
            model TEXT,
            reasoning_effort TEXT,
            agent_path TEXT
        );
        """
    )

    rollout_external = tmp_path / "rollout-external.jsonl"
    rollout_external.write_text(
        "\n".join(
            [
                json.dumps({
                    "type": "event_msg",
                    "payload": {"type": "token_count", "info": None},
                }),
                json.dumps({
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": 60,
                                "cached_input_tokens": 10,
                                "output_tokens": 15,
                                "reasoning_output_tokens": 4,
                                "total_tokens": 75,
                            },
                        },
                    },
                }),
                json.dumps({
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": 60,
                                "cached_input_tokens": 10,
                                "output_tokens": 15,
                                "reasoning_output_tokens": 4,
                                "total_tokens": 75,
                            },
                        },
                    },
                }),
                json.dumps({
                    "type": "event_msg",
                    "payload": {
                        "type": "token_count",
                        "info": {
                            "last_token_usage": {
                                "input_tokens": 40,
                                "cached_input_tokens": 5,
                                "output_tokens": 10,
                                "reasoning_output_tokens": 2,
                                "total_tokens": 50,
                            },
                        },
                    },
                }),
            ]
        )
        + "\n"
    )

    conn.executemany(
        """
        INSERT INTO threads (
            id, rollout_path, created_at, updated_at, source, model_provider, cwd,
            title, sandbox_policy, approval_mode, tokens_used, model
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "thr-external", str(rollout_external), 1_775_470_000, 1_775_470_100, "cli", "openai",
                "/Users/test/dev/multillm", "External", "danger-full-access", "never",
                125, "gpt-5.4",
            ),
        ],
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(codex_stats, "STATE_DB", db_path)

    stats = codex_stats.get_codex_stats()

    assert stats["available"] is True
    assert stats["precision"] == "rollout_usage"
    assert stats["detailedSessionCount"] == 1
    assert stats["totalSessions"] == 1
    assert stats["totalTokens"] == 125
    assert stats["totalInputTokens"] == 100
    assert stats["totalOutputTokens"] == 25
    assert stats["totalCachedTokens"] == 15
    assert stats["totalRealNetTokens"] == 110

    usage = stats["byModel"]["gpt-5.4"]
    assert usage["tokens"] == 125
    assert usage["inputTokens"] == 100
    assert usage["outputTokens"] == 25
    assert usage["cachedTokens"] == 15
    assert usage["realNetTokens"] == 110
    assert usage["externalTokens"] == 125
    assert usage["externalSessions"] == 1
    assert usage["providers"] == ["openai"]

    provider_usage = stats["byProvider"]["openai"]
    assert provider_usage["tokens"] == 125
    assert provider_usage["inputTokens"] == 100
    assert provider_usage["outputTokens"] == 25
    assert provider_usage["cachedTokens"] == 15
    assert provider_usage["realNetTokens"] == 110

    external_session = next(session for session in stats["sessions"] if session["provider"] == "openai")
    assert external_session["tokensUsed"] == 125
    assert external_session["inputTokens"] == 100
    assert external_session["outputTokens"] == 25
    assert external_session["cachedTokens"] == 15
    assert external_session["realNetTokens"] == 110
    assert external_session["hasDetailedUsage"] is True
    assert external_session["usagePrecision"] == "rollout_events"
