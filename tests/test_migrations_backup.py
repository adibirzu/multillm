# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Tests for multillm.migrations.backup.create_backup.

These tests describe the contract for the automatic backup helper that runs
before every ``multillm migrate up``. Each test is hermetic: ``MULTILLM_HOME``
is monkeypatched to ``tmp_path`` so nothing touches the developer's real
``~/.multillm/`` directory.
"""

from __future__ import annotations

import importlib
import sqlite3
import time
from pathlib import Path

import pytest


@pytest.fixture
def home_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate MULTILLM_HOME to a temp directory for every test."""
    monkeypatch.setenv("MULTILLM_HOME", str(tmp_path))
    # Reload the modules so the env override is picked up at import time.
    import multillm.config as _config

    importlib.reload(_config)
    import multillm.migrations.backup as _backup  # noqa: F401  (forces RED until module exists)

    importlib.reload(_backup)
    return tmp_path


@pytest.fixture
def populated_db(tmp_path: Path) -> Path:
    """Create a real SQLite DB with one table and one row so byte-equality is meaningful."""
    db_path = tmp_path / "source.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE widget (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
        conn.execute("INSERT INTO widget (name) VALUES ('alpha')")
        conn.commit()
    finally:
        conn.close()
    return db_path


def test_create_backup_returns_path_under_backups_dir(
    home_dir: Path, populated_db: Path
) -> None:
    """Test 1: returned path lives under ~/.multillm/backups and matches the schema."""
    from multillm.migrations.backup import BACKUP_DIR, create_backup

    target = create_backup(populated_db, target_rev="0001_smoke_test")

    assert target.parent == BACKUP_DIR
    assert target.name.startswith("pre-0001_smoke_test-")
    assert target.suffix == ".db"


def test_create_backup_is_byte_identical_to_source(
    home_dir: Path, populated_db: Path
) -> None:
    """Test 2: the backup file is byte-identical to the source DB."""
    from multillm.migrations.backup import create_backup

    target = create_backup(populated_db, target_rev="0001_smoke_test")

    assert target.exists()
    assert target.read_bytes() == populated_db.read_bytes()


def test_create_backup_creates_backup_dir_with_restrictive_mode(
    home_dir: Path, populated_db: Path
) -> None:
    """Test 3: BACKUP_DIR is created on demand with mode 0o700."""
    from multillm.migrations.backup import BACKUP_DIR, create_backup

    assert not BACKUP_DIR.exists(), "Pre-condition: backups dir must not pre-exist"

    create_backup(populated_db, target_rev="0001_smoke_test")

    assert BACKUP_DIR.exists()
    assert BACKUP_DIR.stat().st_mode & 0o777 == 0o700


def test_create_backup_consecutive_calls_produce_distinct_filenames(
    home_dir: Path, populated_db: Path
) -> None:
    """Test 4: two consecutive backups never overwrite — timestamp resolution is sufficient."""
    from multillm.migrations.backup import create_backup

    first = create_backup(populated_db, target_rev="0001_smoke_test")
    # Sleep just over 1ms — backup filenames embed millisecond-resolution timestamps.
    time.sleep(0.002)
    second = create_backup(populated_db, target_rev="0001_smoke_test")

    assert first != second
    assert first.exists()
    assert second.exists()


def test_create_backup_raises_if_source_missing(home_dir: Path, tmp_path: Path) -> None:
    """Test 5: missing source DB raises FileNotFoundError — never a silent skip."""
    from multillm.migrations.backup import create_backup

    missing = tmp_path / "nope.db"
    assert not missing.exists()

    with pytest.raises(FileNotFoundError):
        create_backup(missing, target_rev="0001_smoke_test")


def test_create_backup_honors_multillm_home_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, populated_db: Path
) -> None:
    """Test 6: BACKUP_DIR follows MULTILLM_HOME override."""
    override = tmp_path / "alt-home"
    override.mkdir()
    monkeypatch.setenv("MULTILLM_HOME", str(override))

    # Reload modules so the override is honored.
    import multillm.config as _config

    importlib.reload(_config)
    import multillm.migrations.backup as backup_mod

    importlib.reload(backup_mod)

    target = backup_mod.create_backup(populated_db, target_rev="0001_smoke_test")

    assert override in target.parents
    assert backup_mod.BACKUP_DIR == override / "backups"
