# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""End-to-end CLI tests for ``multillm migrate``.

Uses Click's ``CliRunner`` so the tests exercise the same dispatch the
``multillm`` console script will hit. ``MULTILLM_HOME`` is monkeypatched
per-test; the smoke migration's target ``system`` table is pre-created
inside a fixture DB.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MULTILLM_HOME", str(tmp_path))
    monkeypatch.delenv("MULTILLM_DB_PATH", raising=False)
    return tmp_path


@pytest.fixture
def bootstrapped_db(isolated_home: Path) -> Path:
    db_path = isolated_home / "multillm.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE system (id INTEGER PRIMARY KEY, key TEXT, value TEXT)"
        )
        conn.execute("INSERT INTO system (key, value) VALUES ('initialized', 'true')")
        conn.commit()
    finally:
        conn.close()
    return db_path


def test_cli_dry_run_lists_pending(bootstrapped_db: Path) -> None:
    """Test 1: --dry-run prints pending revisions and exits 0."""
    from multillm.cli import app

    result = CliRunner().invoke(app, ["migrate", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "0001_smoke_test" in result.output


def test_cli_up_emits_backup_and_revision_lines(
    bootstrapped_db: Path, isolated_home: Path
) -> None:
    """Test 2: up prints 'Backup written:' and 'Migrated to:' and exits 0."""
    from multillm.cli import app

    result = CliRunner().invoke(app, ["migrate", "up"])

    assert result.exit_code == 0, result.output
    assert "Backup written:" in result.output
    # head advances as migrations are added; assert on the chain rather than a specific id.
    assert "Migrated to: 0003_auth_tenancy" in result.output

    backups = list((isolated_home / "backups").iterdir())
    assert len(backups) == 1


def test_cli_down_reverses_cleanly(bootstrapped_db: Path) -> None:
    """Test 3: down --target=base reverses cleanly."""
    from multillm.cli import app

    runner = CliRunner()
    up = runner.invoke(app, ["migrate", "up"])
    assert up.exit_code == 0, up.output

    down = runner.invoke(app, ["migrate", "down", "--target", "base"])

    assert down.exit_code == 0, down.output

    # Confirm the column is gone.
    conn = sqlite3.connect(bootstrapped_db)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(system)").fetchall()}
    finally:
        conn.close()
    assert "_smoke_test_column" not in cols


def test_cli_status_reports_revision(bootstrapped_db: Path) -> None:
    """Test 4: status prints the current revision (or 'no migrations applied')."""
    from multillm.cli import app

    runner = CliRunner()
    fresh = runner.invoke(app, ["migrate", "status"])
    assert fresh.exit_code == 0, fresh.output
    assert "no migrations applied" in fresh.output.lower()

    runner.invoke(app, ["migrate", "up"])

    after = runner.invoke(app, ["migrate", "status"])
    assert after.exit_code == 0, after.output
    assert "0003_auth_tenancy" in after.output


def test_cli_help_lists_subcommands(bootstrapped_db: Path) -> None:
    """Test 5: top-level --help lists migrate + serve; migrate --help lists up/down/status."""
    from multillm.cli import app

    runner = CliRunner()

    top = runner.invoke(app, ["--help"])
    assert top.exit_code == 0, top.output
    assert "migrate" in top.output
    assert "serve" in top.output

    migrate_help = runner.invoke(app, ["migrate", "--help"])
    assert migrate_help.exit_code == 0, migrate_help.output
    assert "up" in migrate_help.output
    assert "down" in migrate_help.output
    assert "status" in migrate_help.output
    assert "--dry-run" in migrate_help.output
