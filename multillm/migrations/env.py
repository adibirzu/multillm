# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Alembic environment script for MultiLLM.

``render_as_batch=True`` enables SQLite's batch-alter-table workaround for
``ALTER COLUMN``-style operations (D-05). ``compare_type=True`` so type
changes are surfaced during autogenerate.

DB location: ``$MULTILLM_HOME/multillm.db`` (override via the
``MULTILLM_DB_PATH`` env var or the standard ``MULTILLM_HOME`` path
resolution in ``multillm.config``).
"""

from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# this is the Alembic Config object
config = context.config

# Interpret the config file for Python logging only if it actually points at
# something fileConfig can parse. The alembic.ini we ship omits logging
# sections to keep the file minimal.
if config.config_file_name is not None:
    try:
        fileConfig(config.config_file_name)
    except Exception:
        pass


def _resolve_db_path() -> Path:
    """Return the active SQLite path.

    Honors ``MULTILLM_DB_PATH`` env override; otherwise falls back to
    ``$MULTILLM_HOME/multillm.db`` (or ``~/.multillm/multillm.db``).
    """
    explicit = os.getenv("MULTILLM_DB_PATH", "").strip()
    if explicit:
        return Path(explicit)
    home = os.getenv("MULTILLM_HOME", "").strip()
    base = Path(home) if home else Path.home() / ".multillm"
    return base / "multillm.db"


def _sqlalchemy_url() -> str:
    return f"sqlite:///{_resolve_db_path()}"


# No declarative metadata in Phase 1 — migrations are hand-written.
target_metadata = None


def run_migrations_offline() -> None:
    """Run migrations in offline (SQL-emit) mode — drives ``--dry-run``."""
    url = _sqlalchemy_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live SQLite connection."""
    section = config.get_section(config.config_ini_section) or {}
    section["sqlalchemy.url"] = _sqlalchemy_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
