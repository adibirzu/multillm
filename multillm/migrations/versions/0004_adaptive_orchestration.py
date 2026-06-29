# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Tenant-scoped adaptive orchestration traces, feedback, and scorecards.

Revision ID: 0004_adaptive_orchestration
Revises: 0003_auth_tenancy
Create Date: 2026-06-29
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0004_adaptive_orchestration"
down_revision: str | Sequence[str] | None = "0003_auth_tenancy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_exists(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _table_exists("orchestration_runs"):
        op.create_table(
            "orchestration_runs",
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("tenant_id", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("prompt_hash", sa.Text(), nullable=False),
            sa.Column("policy_json", sa.Text(), nullable=False),
            sa.Column("task_features_json", sa.Text(), nullable=False),
            sa.Column("decision_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("totals_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("outcome", sa.Text(), nullable=False, server_default="running"),
            sa.PrimaryKeyConstraint("tenant_id", "id"),
        )
        op.create_index(
            "idx_orchestration_runs_tenant_created",
            "orchestration_runs",
            ["tenant_id", "created_at"],
        )

    if not _table_exists("orchestration_calls"):
        op.create_table(
            "orchestration_calls",
            sa.Column("id", sa.Text(), primary_key=True, nullable=False),
            sa.Column("tenant_id", sa.Text(), nullable=False),
            sa.Column("run_id", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("stage", sa.Text(), nullable=False),
            sa.Column("model", sa.Text(), nullable=False),
            sa.Column("effort", sa.Text(), nullable=False),
            sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cache_write_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("reasoning_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
            sa.Column("latency_ms", sa.Float(), nullable=False, server_default="0"),
            sa.Column("status", sa.Text(), nullable=False),
            sa.ForeignKeyConstraint(
                ["tenant_id", "run_id"],
                ["orchestration_runs.tenant_id", "orchestration_runs.id"],
            ),
        )
        op.create_index(
            "idx_orchestration_calls_run",
            "orchestration_calls",
            ["tenant_id", "run_id", "created_at"],
        )

    if not _table_exists("orchestration_feedback"):
        op.create_table(
            "orchestration_feedback",
            sa.Column("id", sa.Text(), primary_key=True, nullable=False),
            sa.Column("tenant_id", sa.Text(), nullable=False),
            sa.Column("run_id", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("rating", sa.Integer(), nullable=False),
            sa.Column("issue_categories_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("preferred_model", sa.Text(), nullable=True),
            sa.CheckConstraint("rating BETWEEN 1 AND 5", name="ck_feedback_rating"),
            sa.ForeignKeyConstraint(
                ["tenant_id", "run_id"],
                ["orchestration_runs.tenant_id", "orchestration_runs.id"],
            ),
        )
        op.create_index(
            "idx_orchestration_feedback_tenant",
            "orchestration_feedback",
            ["tenant_id", "created_at"],
        )

    if not _table_exists("model_scorecards"):
        op.create_table(
            "model_scorecards",
            sa.Column("tenant_id", sa.Text(), nullable=False),
            sa.Column("model", sa.Text(), nullable=False),
            sa.Column("task_type", sa.Text(), nullable=False),
            sa.Column("quality_mean", sa.Float(), nullable=False, server_default="0.5"),
            sa.Column("reliability_mean", sa.Float(), nullable=False, server_default="0.8"),
            sa.Column("avg_cost_usd", sa.Float(), nullable=False, server_default="0"),
            sa.Column("sample_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("confidence_lower", sa.Float(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("tenant_id", "model", "task_type"),
        )


def downgrade() -> None:
    for index_name, table_name in (
        ("idx_orchestration_feedback_tenant", "orchestration_feedback"),
        ("idx_orchestration_calls_run", "orchestration_calls"),
        ("idx_orchestration_runs_tenant_created", "orchestration_runs"),
    ):
        if _table_exists(table_name):
            op.execute(sa.text("DROP INDEX IF EXISTS " + index_name))
    for table_name in (
        "model_scorecards",
        "orchestration_feedback",
        "orchestration_calls",
        "orchestration_runs",
    ):
        if _table_exists(table_name):
            op.drop_table(table_name)
