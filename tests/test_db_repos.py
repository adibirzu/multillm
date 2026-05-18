# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Concrete repo implementations (Plan 02b-01 Task 2).

Covers:
- TrackingRepoSqlite implements the TrackingRepo Protocol (isinstance check)
- record_usage isolates by tenant_id (zero cross-tenant rows)
- multillm/tracking.py module-level functions delegate writes under
  tenant_id="default" (D-2b-03)
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from multillm.db import (
    MemoryRepo,
    MemoryRepoSqlite,
    SessionRepo,
    SessionRepoSqlite,
    TrackingRepo,
    TrackingRepoSqlite,
)


@pytest.fixture
def fresh_db(tmp_path: Path) -> Path:
    """Provision a fresh usage.db with the canonical schema (no tenant_id yet)."""
    db = tmp_path / "usage.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE usage (
            id TEXT PRIMARY KEY,
            timestamp REAL NOT NULL,
            project TEXT NOT NULL DEFAULT 'unknown',
            model_alias TEXT NOT NULL,
            backend TEXT NOT NULL,
            real_model TEXT,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cache_read_input_tokens INTEGER DEFAULT 0,
            cache_creation_input_tokens INTEGER DEFAULT 0,
            latency_ms REAL DEFAULT 0,
            cost_estimate_usd REAL DEFAULT 0,
            status TEXT DEFAULT 'ok',
            error_message TEXT,
            session_id TEXT,
            tenant_id TEXT NOT NULL DEFAULT 'default'
        );
        CREATE INDEX idx_usage_tenant ON usage(tenant_id);
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            started_at REAL NOT NULL,
            last_active_at REAL NOT NULL,
            project TEXT NOT NULL DEFAULT 'unknown',
            caller TEXT DEFAULT 'claude-code',
            total_requests INTEGER DEFAULT 0,
            total_input_tokens INTEGER DEFAULT 0,
            total_output_tokens INTEGER DEFAULT 0,
            total_cache_read_input_tokens INTEGER DEFAULT 0,
            total_cache_creation_input_tokens INTEGER DEFAULT 0,
            total_cost_usd REAL DEFAULT 0,
            models_used TEXT DEFAULT '[]',
            tenant_id TEXT NOT NULL DEFAULT 'default'
        );
        """
    )
    conn.commit()
    conn.close()
    return db


def test_tracking_repo_implements_protocol(fresh_db: Path) -> None:
    """TrackingRepoSqlite must satisfy the runtime-checkable TrackingRepo Protocol."""
    repo = TrackingRepoSqlite(fresh_db)
    assert isinstance(repo, TrackingRepo)


def test_record_usage_isolates_by_tenant(fresh_db: Path) -> None:
    """AUTH-15: cross-tenant isolation. Two tenants → zero cross-bleed."""
    repo = TrackingRepoSqlite(fresh_db)

    # Insert one row per tenant
    repo.record_usage(
        "default",
        {
            "model_alias": "ollama/llama3",
            "backend": "ollama",
            "input_tokens": 100,
            "output_tokens": 50,
            "project": "p1",
        },
    )
    repo.record_usage(
        "other",
        {
            "model_alias": "openai/gpt-4o",
            "backend": "openai",
            "input_tokens": 200,
            "output_tokens": 60,
            "project": "p2",
        },
    )

    # Each tenant sees exactly its own row
    d_default = repo.get_dashboard("default", hours=24)
    d_other = repo.get_dashboard("other", hours=24)

    assert d_default["total_requests"] == 1
    assert d_default["total_input_tokens"] == 100
    assert d_default["total_output_tokens"] == 50
    assert len(d_default["per_backend"]) == 1
    assert d_default["per_backend"][0]["backend"] == "ollama"

    assert d_other["total_requests"] == 1
    assert d_other["total_input_tokens"] == 200
    assert d_other["total_output_tokens"] == 60
    assert len(d_other["per_backend"]) == 1
    assert d_other["per_backend"][0]["backend"] == "openai"


def test_get_summary_isolates_by_tenant(fresh_db: Path) -> None:
    """AUTH-15: summary path also includes WHERE tenant_id = ?."""
    repo = TrackingRepoSqlite(fresh_db)
    repo.record_usage(
        "default", {"model_alias": "ollama/llama3", "backend": "ollama", "input_tokens": 10}
    )
    repo.record_usage(
        "other", {"model_alias": "openai/gpt-4o", "backend": "openai", "input_tokens": 20}
    )

    s_default = repo.get_summary("default", hours=24)
    s_other = repo.get_summary("other", hours=24)

    assert len(s_default["models"]) == 1
    assert s_default["models"][0]["model_alias"] == "ollama/llama3"
    assert len(s_other["models"]) == 1
    assert s_other["models"][0]["model_alias"] == "openai/gpt-4o"


def test_tracking_module_delegates_with_default_tenant(monkeypatch, tmp_path: Path) -> None:
    """multillm/tracking.py record_usage inserts a row tagged tenant_id='default'."""
    # Redirect tracking.py at a tmp DB so we don't pollute the operator's home dir.
    db = tmp_path / "usage.db"
    monkeypatch.setattr("multillm.tracking.DB_PATH", db)

    import multillm.tracking as tracking

    tracking.record_usage(
        project="p1",
        model_alias="ollama/llama3",
        backend="ollama",
        real_model="llama3",
        input_tokens=10,
        output_tokens=5,
        latency_ms=42.0,
    )

    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT tenant_id, model_alias, input_tokens FROM usage WHERE tenant_id = 'default'"
        ).fetchone()
        # Other-tenant query must come back empty (cross-tenant isolation in the module path too)
        other = conn.execute(
            "SELECT COUNT(*) FROM usage WHERE tenant_id = 'other'"
        ).fetchone()[0]
    finally:
        conn.close()

    assert row is not None, "record_usage did not land any default-tenant row"
    assert row == ("default", "ollama/llama3", 10), f"unexpected row: {row}"
    assert other == 0, f"expected zero 'other'-tenant rows, found {other}"


# ── SessionRepoSqlite tests (Plan 02b-01 Task 3) ───────────────────


def test_session_repo_implements_protocol(fresh_db: Path) -> None:
    repo = SessionRepoSqlite(fresh_db)
    assert isinstance(repo, SessionRepo)


def test_sessions_cross_tenant_isolation(fresh_db: Path) -> None:
    """AUTH-15: SessionRepo isolates by tenant_id."""
    repo = SessionRepoSqlite(fresh_db)
    repo.create_session("default", {"project": "p1", "caller": "test"})
    repo.create_session("other", {"project": "p2", "caller": "test"})

    d = repo.list_sessions("default")
    o = repo.list_sessions("other")
    assert len(d) == 1 and d[0]["project"] == "p1", d
    assert len(o) == 1 and o[0]["project"] == "p2", o


def test_session_append_request_no_cross_bleed(fresh_db: Path) -> None:
    """append_request scoped to tenant — passing the wrong tenant_id is a no-op."""
    repo = SessionRepoSqlite(fresh_db)
    sess_default = repo.create_session("default", {"project": "p1"})
    # Attempting to append to default's session FROM another tenant context is a no-op
    repo.append_request("other", sess_default["id"], {"input_tokens": 10, "output_tokens": 5, "cost_usd": 0.01, "model_alias": "ollama/llama3"})
    after = repo.get_session("default", sess_default["id"])
    assert after is not None
    assert after["total_requests"] == 0, "cross-tenant append_request must be a no-op"


# ── MemoryRepoSqlite tests (Plan 02b-01 Task 3) ────────────────────


@pytest.fixture
def fresh_memory_db(tmp_path: Path) -> Path:
    """Provision a fresh memory.db with the schema multillm/memory.py uses."""
    db = tmp_path / "memory.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE memories (
            id TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            project TEXT NOT NULL DEFAULT 'global',
            source_llm TEXT,
            category TEXT DEFAULT 'general',
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata TEXT DEFAULT '{}',
            tenant_id TEXT NOT NULL DEFAULT 'default'
        );
        CREATE INDEX idx_memories_tenant ON memories(tenant_id);
        CREATE VIRTUAL TABLE memories_fts USING fts5(
            title, content, project, category,
            content=memories,
            content_rowid=rowid
        );
        CREATE TRIGGER memories_ai AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, title, content, project, category)
            VALUES (new.rowid, new.title, new.content, new.project, new.category);
        END;
        CREATE TRIGGER memories_ad AFTER DELETE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, title, content, project, category)
            VALUES ('delete', old.rowid, old.title, old.content, old.project, old.category);
        END;
        """
    )
    conn.commit()
    conn.close()
    return db


def test_memory_repo_implements_protocol(fresh_memory_db: Path) -> None:
    repo = MemoryRepoSqlite(fresh_memory_db)
    assert isinstance(repo, MemoryRepo)


def test_memory_cross_tenant_isolation(fresh_memory_db: Path) -> None:
    """AUTH-15: search by 'default' must not return 'other's rows even if they match the query."""
    repo = MemoryRepoSqlite(fresh_memory_db)
    repo.store_memory("default", {"title": "alpha-default", "content": "kappa keyword"})
    repo.store_memory("other", {"title": "alpha-other", "content": "kappa keyword"})

    d_hits = repo.search_memories("default", "kappa")
    o_hits = repo.search_memories("other", "kappa")
    assert len(d_hits) == 1 and d_hits[0]["title"] == "alpha-default"
    assert len(o_hits) == 1 and o_hits[0]["title"] == "alpha-other"


def test_memory_fts_query_is_parameterized(fresh_memory_db: Path) -> None:
    """AUTH-16: an attacker-controlled FTS query must not bypass tenant_id.

    The malicious string is treated as an FTS literal, not interpolated into SQL.
    """
    repo = MemoryRepoSqlite(fresh_memory_db)
    repo.store_memory("default", {"title": "safe", "content": "harmless content"})
    repo.store_memory("other", {"title": "secret", "content": "should never appear cross-tenant"})

    # Inject a SQL-shaped string into the FTS query. Should be parsed as FTS, not SQL.
    # FTS5 may reject the string with a SyntaxError; either way no cross-tenant rows.
    try:
        hits = repo.search_memories("default", "safe' OR 1=1 --")
    except sqlite3.OperationalError:
        # Acceptable: FTS5 refused the malformed query. The point is no injection happened.
        hits = []
    # Cannot return 'other's row regardless of what FTS does with the malicious string
    assert all(h.get("tenant_id", "default") == "default" for h in hits), (
        f"cross-tenant bleed: {hits}"
    )
    titles = {h["title"] for h in hits}
    assert "secret" not in titles, f"injection-bypassed isolation: titles={titles}"


def test_memory_module_delegates_with_default_tenant(monkeypatch, tmp_path: Path) -> None:
    """multillm/memory.py store_memory inserts a row tagged tenant_id='default'."""
    db = tmp_path / "memory.db"
    monkeypatch.setattr("multillm.memory.MEMORY_DB", db)

    import multillm.memory as memory

    mem_id = memory.store_memory(title="t1", content="c1")

    conn = sqlite3.connect(str(db))
    try:
        row = conn.execute(
            "SELECT tenant_id, title FROM memories WHERE id = ?", (mem_id,)
        ).fetchone()
    finally:
        conn.close()

    assert row is not None and row == ("default", "t1"), f"unexpected row: {row}"
