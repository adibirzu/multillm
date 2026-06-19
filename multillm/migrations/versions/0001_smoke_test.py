# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Smoke-test migration (D-05).

Adds ``_smoke_test_column`` to the ``system`` table using
``op.batch_alter_table`` — this proves Alembic's SQLite ALTER COLUMN
workaround works against the real codebase. Phase 2b inherits the same
pattern for the tenant_id rollout.

The migration is a no-op on a brand-new DB that does not yet have a
``system`` table (Phase 1 has no bootstrap of that table yet); the runner
test fixture pre-creates one so the column add is exercised end-to-end.

Revision ID: 0001_smoke_test
Revises: <base>
Create Date: 2026-05-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0001_smoke_test"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TABLE = "system"
_COLUMN = "_smoke_test_column"


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    """Add the throwaway column via batch_alter_table (SQLite-safe ALTER)."""
    if not _table_exists(_TABLE):
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.add_column(sa.Column(_COLUMN, sa.String(), nullable=True))


def downgrade() -> None:
    """Drop the throwaway column via batch_alter_table."""
    if not _table_exists(_TABLE):
        return
    with op.batch_alter_table(_TABLE) as batch_op:
        batch_op.drop_column(_COLUMN)
