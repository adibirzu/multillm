# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""End-to-end runner tests for ``multillm.migrations.runner``.

The smoke migration ``0001_smoke_test`` mutates the ``system`` table, so
each test pre-creates that table in the fixture DB. Every test is hermetic:
``MULTILLM_HOME`` is monkeypatched to ``tmp_path`` and the resolved DB path
lives inside the same dir.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Move MULTILLM_HOME (and the resolved DB) into tmp_path."""
    monkeypatch.setenv("MULTILLM_HOME", str(tmp_path))
    monkeypatch.delenv("MULTILLM_DB_PATH", raising=False)
    return tmp_path


@pytest.fixture
def bootstrapped_db(isolated_home: Path) -> Path:
    """Pre-create the system table so the smoke migration has a target."""
    db_path = isolated_home / "multillm.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE system (id INTEGER PRIMARY KEY, key TEXT, value TEXT)")
        conn.execute("INSERT INTO system (key, value) VALUES ('initialized', 'true')")
        conn.commit()
    finally:
        conn.close()
    return db_path


def _column_names(db_path: Path, table: str) -> list[str]:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(f"PRAGMA table_info({table})")
        return [row[1] for row in cur.fetchall()]
    finally:
        conn.close()


_HEAD_REVISION = "0002_setup_state"


def test_dry_run_lists_pending_migrations_on_fresh_db(bootstrapped_db: Path) -> None:
    """Test 1: dry-run returns every revision up to head on a brand-new DB."""
    from multillm.migrations.runner import migrate_dry_run

    pending = migrate_dry_run()

    assert pending == ["0001_smoke_test", "0002_setup_state"]


def test_migrate_up_runs_to_head_and_writes_one_backup(
    bootstrapped_db: Path, isolated_home: Path
) -> None:
    """Test 2: up runs the migration chain, adds the column, writes a backup."""
    from multillm.migrations.runner import migrate_up

    new_rev = migrate_up()

    assert new_rev == _HEAD_REVISION
    assert "_smoke_test_column" in _column_names(bootstrapped_db, "system")

    backups = list((isolated_home / "backups").iterdir())
    assert len(backups) == 1
    # Backup name is prefixed with the resolved head revision.
    assert backups[0].name.startswith(f"pre-{_HEAD_REVISION}-")


def test_current_revision_after_up(bootstrapped_db: Path) -> None:
    """Test 3: current_revision matches head after up."""
    from multillm.migrations.runner import current_revision, migrate_up

    migrate_up()
    assert current_revision() == _HEAD_REVISION


def test_migrate_down_reverses_smoke_cleanly(bootstrapped_db: Path) -> None:
    """Test 4: down to 'base' drops the column and clears the version."""
    from multillm.migrations.runner import current_revision, migrate_down, migrate_up

    migrate_up()
    assert "_smoke_test_column" in _column_names(bootstrapped_db, "system")

    migrate_down("base")

    assert "_smoke_test_column" not in _column_names(bootstrapped_db, "system")
    assert current_revision() is None


def test_migrate_up_is_idempotent(bootstrapped_db: Path, isolated_home: Path) -> None:
    """Test 5: re-running up on a head DB is a no-op (no second backup)."""
    from multillm.migrations.runner import current_revision, migrate_up

    first = migrate_up()
    backups_after_first = sorted((isolated_home / "backups").iterdir())
    assert len(backups_after_first) == 1

    second = migrate_up()

    assert first == second == _HEAD_REVISION
    assert current_revision() == _HEAD_REVISION

    backups_after_second = sorted((isolated_home / "backups").iterdir())
    assert backups_after_second == backups_after_first, (
        "Idempotent migrate_up must not produce a second backup file"
    )
