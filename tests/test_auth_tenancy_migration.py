# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Migration tests for 0003_auth_tenancy (Plan 02b-01 Task 1).

Covers:
- Tables created on upgrade.
- ``tenant_id`` backfilled onto pre-existing legacy rows.
- ``upgrade → downgrade → upgrade`` cycle is a no-op (idempotent).
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Point the migration runner at an isolated SQLite file per test."""
    db_path = tmp_path / "multillm.db"
    monkeypatch.setenv("MULTILLM_DB_PATH", str(db_path))
    yield db_path


def _run_up(target: str = "head") -> None:
    from multillm.migrations.runner import migrate_up
    migrate_up(target)


def _run_down(target: str) -> None:
    from multillm.migrations.runner import migrate_down
    migrate_down(target)


def _table_names(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {r[0] for r in rows}
    finally:
        conn.close()


def test_upgrade_creates_tables(tmp_db: Path) -> None:
    _run_up()
    tables = _table_names(tmp_db)
    assert "api_keys" in tables, f"api_keys missing — got {tables}"
    assert "budgets" in tables, f"budgets missing — got {tables}"

    # Seed row present?
    conn = sqlite3.connect(tmp_db)
    try:
        row = conn.execute(
            "SELECT tenant_id, daily_cap_cents, monthly_cap_cents FROM budgets"
        ).fetchone()
        assert row == ("default", 0, 0), f"unexpected budgets seed: {row}"
    finally:
        conn.close()


def test_backfill_populates_existing_rows(tmp_db: Path) -> None:
    """Pre-seed a legacy ``usage`` table without tenant_id, run upgrade, verify backfill."""
    # Run migrations through 0002 first so the alembic_version row exists at
    # the prior head; we'll then create the legacy ``usage`` table by hand
    # to simulate a real install whose schema was created by
    # ``multillm/tracking.py`` before 0003 lands.
    _run_down("base")  # drop everything (no-op if nothing yet)
    _run_up("0002_setup_state")  # stamp at the pre-0003 revision

    # Now hand-create the legacy ``usage`` table — it does NOT have tenant_id.
    conn = sqlite3.connect(tmp_db)
    try:
        conn.executescript(
            """
            CREATE TABLE usage (
                ts TEXT,
                backend TEXT,
                model TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER
            );
            INSERT INTO usage (ts, backend, model, input_tokens, output_tokens)
            VALUES
                ('2026-05-18T10:00:00', 'ollama', 'llama3', 100, 50),
                ('2026-05-18T10:01:00', 'openai', 'gpt-4o',  200, 60),
                ('2026-05-18T10:02:00', 'anthropic','claude-3', 300, 70),
                ('2026-05-18T10:03:00', 'gemini', 'flash',  400, 80),
                ('2026-05-18T10:04:00', 'oca',    'gpt5',   500, 90);
            """
        )
        conn.commit()
    finally:
        conn.close()

    # Upgrade to head — 0003 should backfill the 5 rows.
    _run_up("head")

    conn = sqlite3.connect(tmp_db)
    try:
        # Every row tagged 'default', zero NULLs.
        all_default = conn.execute(
            "SELECT COUNT(*) FROM usage WHERE tenant_id = 'default'"
        ).fetchone()[0]
        nulls = conn.execute(
            "SELECT COUNT(*) FROM usage WHERE tenant_id IS NULL"
        ).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
        assert total == 5, f"expected 5 pre-existing rows, got {total}"
        assert nulls == 0, f"expected 0 NULL tenant_id rows, got {nulls}"
        assert all_default == 5, f"expected 5 default-tagged rows, got {all_default}"
    finally:
        conn.close()


def test_migration_idempotent(tmp_db: Path) -> None:
    """upgrade → downgrade → upgrade preserves the seed row via INSERT OR IGNORE."""
    _run_up()
    # Capture seed row identity (same tenant_id, same caps).
    conn = sqlite3.connect(tmp_db)
    try:
        before = conn.execute(
            "SELECT tenant_id, daily_cap_cents, monthly_cap_cents FROM budgets"
        ).fetchone()
    finally:
        conn.close()
    assert before == ("default", 0, 0)

    _run_down("0002_setup_state")  # drops api_keys + budgets
    _run_up("head")  # re-creates them and re-seeds

    conn = sqlite3.connect(tmp_db)
    try:
        after = conn.execute(
            "SELECT tenant_id, daily_cap_cents, monthly_cap_cents FROM budgets"
        ).fetchone()
    finally:
        conn.close()
    assert after == ("default", 0, 0), f"seed row drift after down/up cycle: {after}"

    # Run upgrade AGAIN (already at head) — should be a no-op, no exceptions.
    _run_up("head")
