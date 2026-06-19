# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Tests for the FTS5 rebuild helper (D-06)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from multillm.migrations.fts5 import rebuild_fts5_indexes


@pytest.fixture
def fts5_db(tmp_path: Path) -> sqlite3.Connection:
    """Create a throwaway DB with an FTS5 virtual table populated with rows."""
    conn = sqlite3.connect(tmp_path / "fts5.db")
    # FTS5 may not be compiled into every sqlite3 build; skip cleanly if absent.
    try:
        conn.execute("CREATE VIRTUAL TABLE notes USING fts5(title, body)")
    except sqlite3.OperationalError as exc:
        conn.close()
        pytest.skip(f"FTS5 not available in this sqlite build: {exc}")
    conn.executemany(
        "INSERT INTO notes (title, body) VALUES (?, ?)",
        [("alpha", "the first note"), ("beta", "the second note")],
    )
    conn.commit()
    yield conn
    conn.close()


def test_rebuild_fts5_preserves_rows(fts5_db: sqlite3.Connection) -> None:
    """Test 1: rebuild succeeds and row count is preserved."""
    before = fts5_db.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    assert before == 2

    rebuild_fts5_indexes(fts5_db, "notes")

    after = fts5_db.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
    assert after == before


def test_rebuild_fts5_on_non_fts_table_raises_operational_error(tmp_path: Path) -> None:
    """Test 2: against a plain table, sqlite surfaces OperationalError."""
    conn = sqlite3.connect(tmp_path / "plain.db")
    try:
        conn.execute("CREATE TABLE plain (id INTEGER PRIMARY KEY, body TEXT)")
        conn.commit()
        with pytest.raises(sqlite3.OperationalError):
            rebuild_fts5_indexes(conn, "plain")
    finally:
        conn.close()


@pytest.mark.parametrize(
    "malicious",
    [
        "foo; DROP TABLE x",
        "foo'); DROP TABLE x;--",
        "",
        "1bad",  # cannot start with digit
        "bad name",  # space
        "bad-name",  # dash
        "foo`bar",  # backtick
    ],
)
def test_rebuild_fts5_rejects_unsafe_identifiers(
    fts5_db: sqlite3.Connection, malicious: str
) -> None:
    """Test 3: malicious table names raise ValueError before any SQL runs."""
    with pytest.raises(ValueError):
        rebuild_fts5_indexes(fts5_db, malicious)
