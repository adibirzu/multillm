# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Append-oriented, tenant-scoped persistence for orchestration traces."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
import math
from pathlib import Path
from typing import Any


class OrchestrationStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS orchestration_runs (
                    id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    prompt_hash TEXT NOT NULL,
                    policy_json TEXT NOT NULL,
                    task_features_json TEXT NOT NULL,
                    decision_json TEXT NOT NULL DEFAULT '{}',
                    totals_json TEXT NOT NULL DEFAULT '{}',
                    outcome TEXT NOT NULL DEFAULT 'running',
                    PRIMARY KEY (tenant_id, id)
                );
                CREATE INDEX IF NOT EXISTS idx_orchestration_runs_tenant_created
                    ON orchestration_runs (tenant_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS orchestration_calls (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    stage TEXT NOT NULL,
                    model TEXT NOT NULL,
                    effort TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
                    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
                    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
                    cost_usd REAL NOT NULL DEFAULT 0,
                    latency_ms REAL NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    FOREIGN KEY (tenant_id, run_id)
                        REFERENCES orchestration_runs (tenant_id, id)
                );
                CREATE INDEX IF NOT EXISTS idx_orchestration_calls_run
                    ON orchestration_calls (tenant_id, run_id, created_at);
                CREATE TABLE IF NOT EXISTS orchestration_feedback (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
                    issue_categories_json TEXT NOT NULL DEFAULT '[]',
                    preferred_model TEXT,
                    FOREIGN KEY (tenant_id, run_id)
                        REFERENCES orchestration_runs (tenant_id, id)
                );
                CREATE INDEX IF NOT EXISTS idx_orchestration_feedback_tenant
                    ON orchestration_feedback (tenant_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS model_scorecards (
                    tenant_id TEXT NOT NULL,
                    model TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    quality_mean REAL NOT NULL DEFAULT 0.5,
                    reliability_mean REAL NOT NULL DEFAULT 0.8,
                    avg_cost_usd REAL NOT NULL DEFAULT 0,
                    sample_count INTEGER NOT NULL DEFAULT 0,
                    confidence_lower REAL NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (tenant_id, model, task_type)
                );
                """
            )

    def create_run(
        self,
        tenant_id: str,
        prompt: str,
        policy: dict[str, Any],
        task_features: dict[str, Any],
    ) -> str:
        run_id = f"orch_{uuid.uuid4().hex[:20]}"
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO orchestration_runs
                   (id, tenant_id, created_at, prompt_hash, policy_json, task_features_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    run_id,
                    tenant_id,
                    time.time(),
                    prompt_hash,
                    json.dumps(policy, sort_keys=True),
                    json.dumps(task_features, sort_keys=True),
                ),
            )
        return run_id

    def record_call(
        self,
        *,
        tenant_id: str,
        run_id: str,
        stage: str,
        model: str,
        effort: str,
        usage: dict[str, int],
        cost_usd: float,
        latency_ms: float,
        status: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO orchestration_calls
                   (id, tenant_id, run_id, created_at, stage, model, effort,
                    input_tokens, output_tokens, cache_read_tokens,
                    cache_write_tokens, reasoning_tokens, cost_usd, latency_ms, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"call_{uuid.uuid4().hex[:20]}",
                    tenant_id,
                    run_id,
                    time.time(),
                    stage,
                    model,
                    effort,
                    int(usage.get("input_tokens", 0)),
                    int(usage.get("output_tokens", 0)),
                    int(usage.get("cache_read_tokens", 0)),
                    int(usage.get("cache_write_tokens", 0)),
                    int(usage.get("reasoning_tokens", 0)),
                    float(cost_usd),
                    float(latency_ms),
                    status,
                ),
            )

    def complete_run(
        self,
        tenant_id: str,
        run_id: str,
        *,
        decision: dict[str, Any],
        totals: dict[str, Any],
        outcome: str,
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE orchestration_runs
                   SET decision_json = ?, totals_json = ?, outcome = ?
                   WHERE tenant_id = ? AND id = ?""",
                (
                    json.dumps(decision, sort_keys=True),
                    json.dumps(totals, sort_keys=True),
                    outcome,
                    tenant_id,
                    run_id,
                ),
            )
        return cursor.rowcount == 1

    def get_trace(self, tenant_id: str, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM orchestration_runs WHERE tenant_id = ? AND id = ?",
                (tenant_id, run_id),
            ).fetchone()
            if row is None:
                return None
            calls = connection.execute(
                """SELECT stage, model, effort, input_tokens, output_tokens,
                          cache_read_tokens, cache_write_tokens, reasoning_tokens,
                          cost_usd, latency_ms, status
                   FROM orchestration_calls
                   WHERE tenant_id = ? AND run_id = ? ORDER BY created_at""",
                (tenant_id, run_id),
            ).fetchall()
        return {
            "runId": row["id"],
            "createdAt": row["created_at"],
            "promptHash": row["prompt_hash"],
            "policy": json.loads(row["policy_json"]),
            "taskFeatures": json.loads(row["task_features_json"]),
            "decision": json.loads(row["decision_json"]),
            "totals": json.loads(row["totals_json"]),
            "outcome": row["outcome"],
            "calls": [dict(call) for call in calls],
        }

    def add_feedback(
        self,
        tenant_id: str,
        run_id: str,
        *,
        rating: int,
        issue_categories: tuple[str, ...] = (),
        preferred_model: str | None = None,
    ) -> bool:
        if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
            raise ValueError("rating must be an integer from 1 to 5")
        normalized_issues = tuple(
            issue.strip().lower()
            for issue in issue_categories
            if isinstance(issue, str) and issue.strip()
        )
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM orchestration_runs WHERE tenant_id = ? AND id = ?",
                (tenant_id, run_id),
            ).fetchone()
            if exists is None:
                return False
            connection.execute(
                """INSERT INTO orchestration_feedback
                   (id, tenant_id, run_id, created_at, rating,
                    issue_categories_json, preferred_model)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"feedback_{uuid.uuid4().hex[:20]}",
                    tenant_id,
                    run_id,
                    time.time(),
                    rating,
                    json.dumps(normalized_issues),
                    preferred_model,
                ),
            )
        return True

    def list_feedback(self, tenant_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT run_id, created_at, rating, issue_categories_json,
                          preferred_model
                   FROM orchestration_feedback
                   WHERE tenant_id = ? ORDER BY created_at DESC""",
                (tenant_id,),
            ).fetchall()
        return [
            {
                "runId": row["run_id"],
                "createdAt": row["created_at"],
                "rating": row["rating"],
                "issueCategories": json.loads(row["issue_categories_json"]),
                "preferredModel": row["preferred_model"],
            }
            for row in rows
        ]

    def record_scorecard_observation(
        self,
        tenant_id: str,
        *,
        model: str,
        task_type: str,
        quality: float,
        reliable: bool,
        cost_usd: float = 0,
    ) -> None:
        quality = max(0.0, min(1.0, float(quality)))
        with self._connect() as connection:
            current = connection.execute(
                """SELECT quality_mean, reliability_mean, avg_cost_usd, sample_count
                   FROM model_scorecards
                   WHERE tenant_id = ? AND model = ? AND task_type = ?""",
                (tenant_id, model, task_type),
            ).fetchone()
            samples = int(current["sample_count"]) if current else 0
            next_samples = samples + 1
            old_quality = float(current["quality_mean"]) if current else 0.0
            old_reliability = float(current["reliability_mean"]) if current else 0.0
            old_cost = float(current["avg_cost_usd"]) if current else 0.0
            next_quality = old_quality + (quality - old_quality) / next_samples
            reliability_value = 1.0 if reliable else 0.0
            next_reliability = old_reliability + (
                reliability_value - old_reliability
            ) / next_samples
            next_cost = old_cost + (max(0.0, float(cost_usd)) - old_cost) / next_samples
            # Conservative lower confidence bound. It remains low during cold
            # start and only influences ranking after the minimum sample gate.
            confidence_lower = max(
                0.0, next_quality - 1.96 * math.sqrt(0.25 / next_samples)
            )
            connection.execute(
                """INSERT INTO model_scorecards
                   (tenant_id, model, task_type, quality_mean, reliability_mean,
                    avg_cost_usd, sample_count, confidence_lower, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (tenant_id, model, task_type) DO UPDATE SET
                     quality_mean = excluded.quality_mean,
                     reliability_mean = excluded.reliability_mean,
                     avg_cost_usd = excluded.avg_cost_usd,
                     sample_count = excluded.sample_count,
                     confidence_lower = excluded.confidence_lower,
                     updated_at = excluded.updated_at""",
                (
                    tenant_id,
                    model,
                    task_type,
                    next_quality,
                    next_reliability,
                    next_cost,
                    next_samples,
                    confidence_lower,
                    time.time(),
                ),
            )

    def get_scorecards(
        self, tenant_id: str, *, min_samples: int = 20
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT model, task_type, quality_mean, reliability_mean,
                          avg_cost_usd, sample_count, confidence_lower, updated_at
                   FROM model_scorecards
                   WHERE tenant_id = ? AND sample_count >= ?
                   ORDER BY confidence_lower DESC""",
                (tenant_id, max(1, int(min_samples))),
            ).fetchall()
        return [dict(row) for row in rows]
