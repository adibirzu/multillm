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

from multillm.db import TrackingRepo, TrackingRepoSqlite


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
