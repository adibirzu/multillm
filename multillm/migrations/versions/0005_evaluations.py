# SPDX-License-Identifier: Apache-2.0

"""Tenant-scoped encrypted evaluation suites, runs, outputs, and reviews.

Revision ID: 0005_evaluations
Revises: 0004_adaptive_orchestration
Create Date: 2026-07-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0005_evaluations"
down_revision: str | Sequence[str] | None = "0004_adaptive_orchestration"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_exists(table_name: str) -> bool:
    return table_name in sa.inspect(op.get_bind()).get_table_names()


def upgrade() -> None:
    if not _table_exists("evaluation_suites"):
        op.create_table(
            "evaluation_suites",
            sa.Column("tenant_id", sa.Text(), nullable=False),
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("version", sa.Text(), nullable=False),
            sa.Column("source", sa.Text(), nullable=False),
            sa.Column("license_id", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.PrimaryKeyConstraint("tenant_id", "id"),
        )

    if not _table_exists("evaluation_cases"):
        op.create_table(
            "evaluation_cases",
            sa.Column("tenant_id", sa.Text(), nullable=False),
            sa.Column("suite_id", sa.Text(), nullable=False),
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.Column("case_json", sa.Text(), nullable=False),
            sa.PrimaryKeyConstraint("tenant_id", "suite_id", "id"),
            sa.ForeignKeyConstraint(
                ["tenant_id", "suite_id"],
                ["evaluation_suites.tenant_id", "evaluation_suites.id"],
            ),
        )

    if not _table_exists("evaluation_runs"):
        op.create_table(
            "evaluation_runs",
            sa.Column("tenant_id", sa.Text(), nullable=False),
            sa.Column("id", sa.Text(), nullable=False),
            sa.Column("suite_id", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.Column("updated_at", sa.Float(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("request_json", sa.Text(), nullable=False),
            sa.Column("summary_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("worker_id", sa.Text(), nullable=True),
            sa.Column("lease_until", sa.Float(), nullable=True),
            sa.Column(
                "cancel_requested", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.PrimaryKeyConstraint("tenant_id", "id"),
            sa.ForeignKeyConstraint(
                ["tenant_id", "suite_id"],
                ["evaluation_suites.tenant_id", "evaluation_suites.id"],
            ),
        )
        op.create_index(
            "idx_evaluation_runs_queue",
            "evaluation_runs",
            ["status", "lease_until", "created_at"],
        )

    if not _table_exists("evaluation_outputs"):
        op.create_table(
            "evaluation_outputs",
            sa.Column("id", sa.Text(), primary_key=True, nullable=False),
            sa.Column("tenant_id", sa.Text(), nullable=False),
            sa.Column("run_id", sa.Text(), nullable=False),
            sa.Column("case_id", sa.Text(), nullable=False),
            sa.Column("target", sa.Text(), nullable=False),
            sa.Column("attempt", sa.Integer(), nullable=False),
            sa.Column("content_encrypted", sa.LargeBinary(), nullable=False),
            sa.Column("content_hash", sa.Text(), nullable=False),
            sa.Column("usage_json", sa.Text(), nullable=False),
            sa.Column("latency_json", sa.Text(), nullable=False),
            sa.Column("cost_json", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.UniqueConstraint(
                "tenant_id",
                "run_id",
                "case_id",
                "target",
                "attempt",
                name="uq_evaluation_output_attempt",
            ),
            sa.ForeignKeyConstraint(
                ["tenant_id", "run_id"],
                ["evaluation_runs.tenant_id", "evaluation_runs.id"],
            ),
        )
        op.create_index(
            "idx_evaluation_outputs_run",
            "evaluation_outputs",
            ["tenant_id", "run_id", "case_id", "target"],
        )

    if not _table_exists("evaluation_metrics"):
        op.create_table(
            "evaluation_metrics",
            sa.Column("id", sa.Text(), primary_key=True, nullable=False),
            sa.Column("tenant_id", sa.Text(), nullable=False),
            sa.Column("run_id", sa.Text(), nullable=False),
            sa.Column("case_id", sa.Text(), nullable=False),
            sa.Column("target", sa.Text(), nullable=False),
            sa.Column("metric", sa.Text(), nullable=False),
            sa.Column("value", sa.Float(), nullable=True),
            sa.Column("passed", sa.Integer(), nullable=True),
            sa.Column("details_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.UniqueConstraint(
                "tenant_id",
                "run_id",
                "case_id",
                "target",
                "metric",
                name="uq_evaluation_metric",
            ),
            sa.ForeignKeyConstraint(
                ["tenant_id", "run_id"],
                ["evaluation_runs.tenant_id", "evaluation_runs.id"],
            ),
        )

    if not _table_exists("evaluation_comparisons"):
        op.create_table(
            "evaluation_comparisons",
            sa.Column("id", sa.Text(), primary_key=True, nullable=False),
            sa.Column("tenant_id", sa.Text(), nullable=False),
            sa.Column("run_id", sa.Text(), nullable=False),
            sa.Column("case_id", sa.Text(), nullable=False),
            sa.Column("candidate_target", sa.Text(), nullable=False),
            sa.Column("baseline_target", sa.Text(), nullable=False),
            sa.Column("decision", sa.Text(), nullable=False, server_default="abstain"),
            sa.Column(
                "needs_human_review", sa.Integer(), nullable=False, server_default="1"
            ),
            sa.Column("human_decision", sa.Text(), nullable=True),
            sa.Column("details_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.UniqueConstraint(
                "tenant_id",
                "run_id",
                "case_id",
                "candidate_target",
                "baseline_target",
                name="uq_evaluation_comparison",
            ),
            sa.ForeignKeyConstraint(
                ["tenant_id", "run_id"],
                ["evaluation_runs.tenant_id", "evaluation_runs.id"],
            ),
        )

    if not _table_exists("evaluation_judgments"):
        op.create_table(
            "evaluation_judgments",
            sa.Column("id", sa.Text(), primary_key=True, nullable=False),
            sa.Column("tenant_id", sa.Text(), nullable=False),
            sa.Column("comparison_id", sa.Text(), nullable=False),
            sa.Column("judge", sa.Text(), nullable=False),
            sa.Column("ordering", sa.Text(), nullable=False),
            sa.Column("judgment_encrypted", sa.LargeBinary(), nullable=False),
            sa.Column("content_hash", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.UniqueConstraint(
                "tenant_id",
                "comparison_id",
                "judge",
                "ordering",
                name="uq_evaluation_judgment",
            ),
            sa.ForeignKeyConstraint(["comparison_id"], ["evaluation_comparisons.id"]),
        )

    if not _table_exists("evaluation_reviews"):
        op.create_table(
            "evaluation_reviews",
            sa.Column("id", sa.Text(), primary_key=True, nullable=False),
            sa.Column("tenant_id", sa.Text(), nullable=False),
            sa.Column("run_id", sa.Text(), nullable=False),
            sa.Column("comparison_id", sa.Text(), nullable=False),
            sa.Column("reviewer_id", sa.Text(), nullable=False),
            sa.Column("decision", sa.Text(), nullable=False),
            sa.Column("rationale_encrypted", sa.LargeBinary(), nullable=False),
            sa.Column("rationale_hash", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Float(), nullable=False),
            sa.UniqueConstraint(
                "tenant_id",
                "comparison_id",
                "reviewer_id",
                name="uq_evaluation_review",
            ),
            sa.ForeignKeyConstraint(
                ["tenant_id", "run_id"],
                ["evaluation_runs.tenant_id", "evaluation_runs.id"],
            ),
        )


def downgrade() -> None:
    for index_name, table_name in (
        ("idx_evaluation_outputs_run", "evaluation_outputs"),
        ("idx_evaluation_runs_queue", "evaluation_runs"),
    ):
        if _table_exists(table_name):
            op.execute(sa.text("DROP INDEX IF EXISTS " + index_name))
    for table_name in (
        "evaluation_reviews",
        "evaluation_judgments",
        "evaluation_comparisons",
        "evaluation_metrics",
        "evaluation_outputs",
        "evaluation_runs",
        "evaluation_cases",
        "evaluation_suites",
    ):
        if _table_exists(table_name):
            op.drop_table(table_name)
