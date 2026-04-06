"""Tests for Codex CLI stats helpers."""

import sqlite3

from multillm import codex_stats


def test_get_codex_stats_splits_model_usage_by_provider_scope(tmp_path, monkeypatch):
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
    conn.executemany(
        """
        INSERT INTO threads (
            id, rollout_path, created_at, updated_at, source, model_provider, cwd,
            title, sandbox_policy, approval_mode, tokens_used, model
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "thr-external", "/tmp/a", 1_775_470_000, 1_775_470_100, "cli", "openai",
                "/Users/abirzu/dev/multillm", "External", "danger-full-access", "never",
                1_500, "gpt-5.4",
            ),
            (
                "thr-oca", "/tmp/b", 1_775_470_200, 1_775_470_300, "cli", "oca-chicago",
                "/Users/abirzu/dev/multillm", "OCA", "danger-full-access", "never",
                2_500, "gpt-5.4",
            ),
        ],
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(codex_stats, "STATE_DB", db_path)

    stats = codex_stats.get_codex_stats()

    assert stats["available"] is True
    usage = stats["byModel"]["gpt-5.4"]
    assert usage["tokens"] == 4_000
    assert usage["externalTokens"] == 1_500
    assert usage["externalSessions"] == 1
    assert usage["ocaTokens"] == 2_500
    assert usage["ocaSessions"] == 1
    assert usage["providers"] == ["oca-chicago", "openai"]
