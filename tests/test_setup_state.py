# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Tests for ``multillm.setup.state`` (setup state machine + migration 0002).

Each test starts from a fresh DB at ``$MULTILLM_HOME/multillm.db`` with
migrations applied through head (which means 0001 + 0002 land). Tests then
exercise advance(), complete(), reset_setup() against a real sqlite3
connection.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("MULTILLM_HOME", str(tmp_path))
    monkeypatch.delenv("MULTILLM_DB_PATH", raising=False)
    return tmp_path


@pytest.fixture
def migrated_db(isolated_home: Path) -> Path:
    """Run alembic upgrade head so 0001 + 0002 are applied."""
    from multillm.migrations.runner import db_path, migrate_up

    # Ensure DB file exists so 0001's batch_alter_table has a target.
    path = db_path()
    conn = sqlite3.connect(path)
    try:
        # 0001 expects an existing `system` table; production install
        # bootstraps it. We do the same here so the smoke migration is a
        # no-op (column already gets added).
        conn.execute(
            "CREATE TABLE IF NOT EXISTS system "
            "(key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP)"
        )
        conn.commit()
    finally:
        conn.close()
    migrate_up()
    return path


@pytest.fixture
def conn(migrated_db: Path):
    c = sqlite3.connect(migrated_db)
    c.row_factory = sqlite3.Row
    try:
        yield c
    finally:
        c.close()


# ── Migration 0002 schema ────────────────────────────────────────────────────


def _table_exists(c: sqlite3.Connection, name: str) -> bool:
    row = c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def test_migration_0002_creates_setup_state_table(conn: sqlite3.Connection) -> None:
    assert _table_exists(conn, "setup_state")


def test_migration_0002_creates_admin_users_table(conn: sqlite3.Connection) -> None:
    assert _table_exists(conn, "admin_users")


def test_migration_0002_seeds_setup_complete_zero(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT value FROM system WHERE key=?", ("setup_complete",)
    ).fetchone()
    assert row is not None
    assert row["value"] == "0"


# ── State machine semantics ──────────────────────────────────────────────────


def test_fresh_db_state_is_pending_and_not_complete(conn: sqlite3.Connection) -> None:
    from multillm.setup.state import SetupState, get_state, is_complete

    assert is_complete(conn) is False
    assert get_state(conn) is SetupState.PENDING


def test_advance_admin_persists_row_and_admin_user(conn: sqlite3.Connection) -> None:
    from multillm.setup.state import SetupState, advance, get_state

    advance(
        conn,
        "admin",
        {"email": "a@example.test", "password_hash": "$argon2id$test"},
    )
    row = conn.execute(
        "SELECT payload_json FROM setup_state WHERE pane=?", ("admin",)
    ).fetchone()
    assert row is not None

    admin = conn.execute(
        "SELECT email, password_hash FROM admin_users WHERE id=1"
    ).fetchone()
    assert admin is not None
    assert admin["email"] == "a@example.test"
    assert admin["password_hash"] == "$argon2id$test"

    assert get_state(conn) is SetupState.ADMIN_CREATED


def test_advance_backends_persists_payload_after_admin(conn: sqlite3.Connection) -> None:
    import json

    from multillm.setup.state import advance

    advance(
        conn,
        "admin",
        {"email": "a@example.test", "password_hash": "$argon2id$test"},
    )
    advance(conn, "backends", {"openai": "sk-test", "anthropic": ""})

    row = conn.execute(
        "SELECT payload_json FROM setup_state WHERE pane=?", ("backends",)
    ).fetchone()
    payload = json.loads(row["payload_json"])
    assert payload == {"openai": "sk-test", "anthropic": ""}


def test_advance_panes_2_through_4_are_order_independent(conn: sqlite3.Connection) -> None:
    from multillm.setup.state import advance

    advance(
        conn,
        "admin",
        {"email": "a@example.test", "password_hash": "$argon2id$test"},
    )
    # Panes can be completed in any order after admin.
    advance(conn, "observability", {"prometheus_enabled": True})
    advance(conn, "backends", {"openai": "sk-..."})
    advance(conn, "local_probe", {"ollama": {"reachable": False}})

    panes = {
        r["pane"]
        for r in conn.execute("SELECT pane FROM setup_state").fetchall()
    }
    assert panes == {"admin", "backends", "local_probe", "observability"}


def test_complete_sets_flag_and_clears_setup_state(conn: sqlite3.Connection) -> None:
    from multillm.setup.state import advance, complete, is_complete

    advance(
        conn,
        "admin",
        {"email": "a@example.test", "password_hash": "$argon2id$test"},
    )
    advance(conn, "backends", {"openai": "sk-secret"})
    advance(conn, "observability", {"prometheus_enabled": False})

    complete(conn)

    assert is_complete(conn) is True
    # Ephemeral state must be wiped (T-01-07-03 mitigation).
    rows = conn.execute("SELECT COUNT(*) AS n FROM setup_state").fetchone()
    assert rows["n"] == 0


def test_reset_clears_admin_users_and_re_enables_wizard(conn: sqlite3.Connection) -> None:
    from multillm.setup.state import (
        advance,
        complete,
        is_complete,
        reset_setup,
    )

    advance(
        conn,
        "admin",
        {"email": "a@example.test", "password_hash": "$argon2id$test"},
    )
    complete(conn)
    assert is_complete(conn) is True

    reset_setup(conn)

    assert is_complete(conn) is False
    admins = conn.execute("SELECT COUNT(*) AS n FROM admin_users").fetchone()
    assert admins["n"] == 0
    panes = conn.execute("SELECT COUNT(*) AS n FROM setup_state").fetchone()
    assert panes["n"] == 0
