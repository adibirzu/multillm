# SPDX-License-Identifier: Apache-2.0

"""Retain one deterministic metric row per evaluation attempt.

Revision ID: 0006_evaluation_metric_attempts
Revises: 0005_evaluations
Create Date: 2026-07-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0006_evaluation_metric_attempts"
down_revision: str | Sequence[str] | None = "0005_evaluations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("evaluation_metrics")}
    if "attempt" in columns:
        return
    with op.batch_alter_table("evaluation_metrics", recreate="always") as batch:
        batch.add_column(
            sa.Column("attempt", sa.Integer(), nullable=False, server_default="1")
        )
        batch.drop_constraint("uq_evaluation_metric", type_="unique")
        batch.create_unique_constraint(
            "uq_evaluation_metric_attempt",
            ["tenant_id", "run_id", "case_id", "target", "attempt", "metric"],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("evaluation_metrics")}
    if "attempt" not in columns:
        return
    # The old key can retain only one attempt. Keep attempt 1 (or the earliest
    # available attempt) deterministically before restoring that constraint.
    op.execute(
        sa.text(
            """DELETE FROM evaluation_metrics
               WHERE attempt != (
                   SELECT MIN(candidate.attempt)
                   FROM evaluation_metrics AS candidate
                   WHERE candidate.tenant_id = evaluation_metrics.tenant_id
                     AND candidate.run_id = evaluation_metrics.run_id
                     AND candidate.case_id = evaluation_metrics.case_id
                     AND candidate.target = evaluation_metrics.target
                     AND candidate.metric = evaluation_metrics.metric
               )"""
        )
    )
    with op.batch_alter_table("evaluation_metrics", recreate="always") as batch:
        batch.drop_constraint("uq_evaluation_metric_attempt", type_="unique")
        batch.create_unique_constraint(
            "uq_evaluation_metric",
            ["tenant_id", "run_id", "case_id", "target", "metric"],
        )
        batch.drop_column("attempt")
