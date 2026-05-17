# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""First-run setup wizard schema (Plan 01-07 Task 1).

Creates three tables that back the wizard's state machine and seed the
``setup_complete`` flag the redirect middleware reads on every request:

- ``system``       — global key/value store (canonical key: ``setup_complete``)
- ``setup_state``  — per-pane payloads, cleared on ``complete()`` (T-01-07-03)
- ``admin_users``  — Phase 1 single-admin user (Phase 2b expands to multi-user)

Revision ID: 0002_setup_state
Revises: 0001_smoke_test
Create Date: 2026-05-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0002_setup_state"
down_revision: str | Sequence[str] | None = "0001_smoke_test"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    """Create system / setup_state / admin_users and seed setup_complete='0'."""
    # ``system`` may already exist from a fresh production install (or the
    # 0001 smoke fixture). Only create it if missing; the seed is upserted
    # below.
    if not _table_exists("system"):
        op.create_table(
            "system",
            sa.Column("key", sa.Text(), primary_key=True, nullable=False),
            sa.Column("value", sa.Text(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        )

    if not _table_exists("setup_state"):
        op.create_table(
            "setup_state",
            sa.Column("pane", sa.Text(), primary_key=True, nullable=False),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )

    if not _table_exists("admin_users"):
        op.create_table(
            "admin_users",
            sa.Column(
                "id", sa.Integer(), primary_key=True, autoincrement=True
            ),
            sa.Column(
                "email", sa.Text(), nullable=False, unique=True
            ),
            sa.Column("password_hash", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        )

    # Seed setup_complete='0' so the redirect middleware always has a row
    # to read. Use INSERT OR IGNORE so re-running the migration on an
    # already-stamped DB is a no-op.
    op.execute(
        sa.text(
            "INSERT OR IGNORE INTO system (key, value) "
            "VALUES ('setup_complete', '0')"
        )
    )


def downgrade() -> None:
    """Drop the three tables (leaves the legacy ``system`` table only if it pre-existed)."""
    if _table_exists("admin_users"):
        op.drop_table("admin_users")
    if _table_exists("setup_state"):
        op.drop_table("setup_state")
    # Intentional: do not drop ``system`` on downgrade because Phase 2b
    # and beyond rely on it. Removing the setup_complete row instead.
    op.execute(
        sa.text("DELETE FROM system WHERE key = 'setup_complete'")
    )
