# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Programmatic Alembic runner used by the ``multillm migrate`` CLI.

Public surface
--------------
- ``current_revision()`` — alembic head currently stamped, or ``None``.
- ``migrate_dry_run()`` — pending revision IDs (no side effects).
- ``migrate_up(target='head')`` — backup THEN upgrade.
- ``migrate_down(target)`` — downgrade (no backup; D-05 says backup-before-up only).

All entry points are idempotent: ``migrate_up`` on an already-head DB is a
no-op and writes no backup, matching the test contract (Task 3 Test 5).
"""

from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from multillm.migrations.backup import create_backup

__all__ = [
    "alembic_config",
    "current_revision",
    "db_path",
    "migrate_down",
    "migrate_dry_run",
    "migrate_up",
]


def _project_root() -> Path:
    # multillm/migrations/runner.py -> multillm/ -> repo root
    return Path(__file__).resolve().parent.parent.parent


def db_path() -> Path:
    """Resolve the active SQLite DB path (env-overrideable)."""
    explicit = os.getenv("MULTILLM_DB_PATH", "").strip()
    if explicit:
        return Path(explicit)
    home = os.getenv("MULTILLM_HOME", "").strip()
    base = Path(home) if home else Path.home() / ".multillm"
    base.mkdir(parents=True, exist_ok=True)
    return base / "multillm.db"


def alembic_config() -> Config:
    """Build an Alembic ``Config`` resolved against the active DB path.

    We intentionally bypass alembic.ini for sqlalchemy.url so the runtime
    can switch DBs per-test (or per-process) without rewriting the file.
    """
    ini_path = _project_root() / "alembic.ini"
    cfg = Config(str(ini_path)) if ini_path.exists() else Config()
    # The script_location must resolve regardless of the caller's CWD.
    cfg.set_main_option(
        "script_location", str(_project_root() / "multillm" / "migrations")
    )
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path()}")
    return cfg


def current_revision() -> str | None:
    """Return the alembic head currently stamped on the DB, or ``None``."""
    path = db_path()
    if not path.exists():
        return None
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            return ctx.get_current_revision()
    finally:
        engine.dispose()


def migrate_dry_run() -> list[str]:
    """List revision IDs that ``migrate_up()`` would apply, head-first.

    No side effects: the live DB is opened read-only-style via a sqla
    connection but only to read the alembic_version row.
    """
    cfg = alembic_config()
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    if head is None:
        return []
    current = current_revision()
    pending: list[str] = []
    for revision in script.iterate_revisions(head, current):
        # iterate_revisions yields newest-first; reverse to apply order.
        pending.append(revision.revision)
    pending.reverse()
    return pending


def migrate_up(target: str = "head") -> str | None:
    """Back up the DB, then upgrade to ``target``.

    Returns the post-upgrade current revision (``None`` if the DB started
    empty and the migration was a no-op for any reason).

    Idempotency: if the DB is already at the requested revision, no backup
    is written and no DDL is issued. T-01-03-01 is mitigated because the
    backup happens BEFORE any alembic command.run call.
    """
    cfg = alembic_config()
    script = ScriptDirectory.from_config(cfg)

    resolved_target = script.get_current_head() if target == "head" else target
    current = current_revision()

    # No-op short-circuit (Test 5).
    if current is not None and current == resolved_target:
        return current

    # Resolve the source DB and ensure it exists before we can back it up.
    src = db_path()
    if src.exists():
        # Pre-DDL backup. If create_backup raises, we abort BEFORE upgrade.
        create_backup(src, target_rev=str(resolved_target or "head"))
    # If src does not exist yet (very fresh install), alembic's upgrade will
    # create it; no backup possible. The test fixture always pre-creates
    # the DB so this branch is only hit in true first-run scenarios.

    command.upgrade(cfg, target)
    return current_revision()


def migrate_down(target: str) -> str | None:
    """Downgrade to ``target``.

    Per D-05 the backup-before-migrate contract applies to ``up`` only;
    operators can pre-snapshot manually before destructive rollbacks.
    """
    cfg = alembic_config()
    command.downgrade(cfg, target)
    return current_revision()
