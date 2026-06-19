# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Auth + tenancy schema (Plan 02b-01 Task 1).

Creates two new tables and backfills ``tenant_id`` onto the three pre-existing
data-access tables so the Phase 2a Protocol shape can be implemented against
real storage:

- ``api_keys``   — per-tenant API keys, hash-only storage (D-2b-02).
- ``budgets``    — per-tenant daily/monthly spend caps, cents-as-integer (D-2b-04).
- backfill       — adds ``tenant_id TEXT NOT NULL DEFAULT 'default'`` to
                   ``usage``, ``sessions``, and ``memories`` (the actual table
                   names in ``multillm/tracking.py`` and ``multillm/memory.py``;
                   the plan referenced ``memory`` but the live table is
                   ``memories``).

Per D-2b-05 / AUTH-18 the migration is idempotent: re-running upgrade after
upgrade is a no-op, and downgrade + upgrade preserves the seeded budgets row
because of ``INSERT OR IGNORE``.

Revision ID: 0003_auth_tenancy
Revises: 0002_setup_state
Create Date: 2026-05-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0003_auth_tenancy"
down_revision: str | Sequence[str] | None = "0002_setup_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Tables that get ``tenant_id`` backfilled. ``memories`` (not ``memory``) is the
# actual table name used by ``multillm/memory.py:33``.
_BACKFILL_TABLES = ("usage", "sessions", "memories")


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _column_exists(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def upgrade() -> None:
    """Create api_keys + budgets and backfill tenant_id on legacy tables."""
    # ── 1. api_keys ────────────────────────────────────────────────
    if not _table_exists("api_keys"):
        op.create_table(
            "api_keys",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("tenant_id", sa.Text(), nullable=False, server_default="default"),
            sa.Column("key_hash", sa.Text(), nullable=False, unique=True),
            sa.Column("key_prefix", sa.Text(), nullable=False),
            sa.Column("label", sa.Text(), nullable=True),
            sa.Column("scopes", sa.Text(), nullable=False, server_default='["*"]'),
            sa.Column("created_at", sa.Text(), nullable=False),
            sa.Column("revoked_at", sa.Text(), nullable=True),
        )
        # Partial index covers the hot-path SELECT in the auth middleware:
        # WHERE key_hash = ? AND revoked_at IS NULL.
        op.execute(
            sa.text(
                "CREATE INDEX IF NOT EXISTS idx_api_keys_hash_active "
                "ON api_keys (key_hash) WHERE revoked_at IS NULL"
            )
        )

    # ── 2. budgets ─────────────────────────────────────────────────
    if not _table_exists("budgets"):
        op.create_table(
            "budgets",
            sa.Column("tenant_id", sa.Text(), primary_key=True, nullable=False),
            sa.Column(
                "daily_cap_cents", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column(
                "monthly_cap_cents", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column(
                "daily_remaining_cents",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "monthly_remaining_cents",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column("day_started_at", sa.Text(), nullable=False),
            sa.Column("month_started_at", sa.Text(), nullable=False),
        )

    # Seed the default-tenant row. cap = 0 means unlimited; budget middleware
    # in Plan 02b-02 short-circuits when cap == 0. INSERT OR IGNORE keeps this
    # idempotent across re-runs.
    op.execute(
        sa.text(
            "INSERT OR IGNORE INTO budgets "
            "(tenant_id, daily_cap_cents, monthly_cap_cents, "
            " daily_remaining_cents, monthly_remaining_cents, "
            " day_started_at, month_started_at) "
            "VALUES ('default', 0, 0, 0, 0, "
            " date('now'), date('now', 'start of month'))"
        )
    )

    # ── 3. Backfill tenant_id onto legacy data-access tables ──────
    # SQLite cannot ALTER COLUMN to add NOT NULL natively, so we use
    # batch_alter_table which copies the table under the hood (per the
    # project CLAUDE.md alembic note).
    for table in _BACKFILL_TABLES:
        if not _table_exists(table):
            continue  # Fresh install hasn't created the legacy table yet.
        if _column_exists(table, "tenant_id"):
            continue  # Idempotency: already backfilled.
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(
                sa.Column(
                    "tenant_id",
                    sa.Text(),
                    nullable=False,
                    server_default="default",
                )
            )
        # Ensure pre-existing rows are explicitly tagged (server_default only
        # applies to new rows in some SQLite paths; an UPDATE is safe-by-
        # construction here).
        op.execute(
            sa.text(
                f"UPDATE {table} SET tenant_id = 'default' WHERE tenant_id IS NULL OR tenant_id = ''"
            )
        )


def downgrade() -> None:
    """Reverse the upgrade: drop new tables and drop the backfilled column."""
    for table in _BACKFILL_TABLES:
        if not _table_exists(table):
            continue
        if not _column_exists(table, "tenant_id"):
            continue
        with op.batch_alter_table(table) as batch_op:
            batch_op.drop_column("tenant_id")

    if _table_exists("budgets"):
        op.drop_table("budgets")

    if _table_exists("api_keys"):
        # Drop the partial index first (some SQLite versions complain if the
        # table goes before the dependent index is gone).
        op.execute(sa.text("DROP INDEX IF EXISTS idx_api_keys_hash_active"))
        op.drop_table("api_keys")
