# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Append-oriented, tenant-scoped persistence for orchestration traces."""

from __future__ import annotations

import csv
import hashlib
import io
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
                CREATE TABLE IF NOT EXISTS scan_reports (
                    id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    source TEXT NOT NULL,
                    project TEXT NOT NULL,
                    title TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY (tenant_id, id)
                );
                CREATE INDEX IF NOT EXISTS idx_scan_reports_tenant_created
                    ON scan_reports (tenant_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS scan_findings (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    report_id TEXT NOT NULL,
                    external_id TEXT,
                    severity TEXT NOT NULL,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    resource TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY (tenant_id, report_id)
                        REFERENCES scan_reports (tenant_id, id)
                );
                CREATE INDEX IF NOT EXISTS idx_scan_findings_tenant_report
                    ON scan_findings (tenant_id, report_id, severity, status);
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
        self, tenant_id: str, *, min_samples: int = 20, task_type: str | None = None
    ) -> list[dict[str, Any]]:
        where = "WHERE tenant_id = ? AND sample_count >= ?"
        values: list[Any] = [tenant_id, max(1, int(min_samples))]
        if task_type:
            where += " AND task_type = ?"
            values.append(task_type)
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT model, task_type, quality_mean, reliability_mean,
                          avg_cost_usd, sample_count, confidence_lower, updated_at
                   FROM model_scorecards
                   """ + where + " ORDER BY confidence_lower DESC",
                values,
            ).fetchall()
        return [dict(row) for row in rows]

    def create_scan_report(self, tenant_id: str, payload: dict[str, Any]) -> str:
        """Persist a normalized scan report without cross-tenant visibility."""
        report_id = f"scan_{uuid.uuid4().hex[:20]}"
        findings = payload.get("findings", [])
        if not isinstance(findings, list):
            raise ValueError("findings must be an array")
        source = str(payload.get("source", "")).strip()
        project = str(payload.get("project", "")).strip()
        title = str(payload.get("title", "")).strip()
        if not tenant_id or not source or not project or not title:
            raise ValueError("tenant_id, source, project, and title are required")
        now = time.time()
        report_metadata = payload.get("metadata", {})
        if not isinstance(report_metadata, dict):
            raise ValueError("metadata must be an object")
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO scan_reports
                   (id, tenant_id, created_at, source, project, title, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (report_id, tenant_id, now, source, project, title,
                 json.dumps(report_metadata, sort_keys=True)),
            )
            for finding in findings:
                if not isinstance(finding, dict):
                    raise ValueError("each finding must be an object")
                severity = str(finding.get("severity", "")).lower().strip()
                category = str(finding.get("category", "")).strip()
                finding_title = str(finding.get("title", "")).strip()
                status = str(finding.get("status", "open")).lower().strip()
                if severity not in {"critical", "high", "medium", "low", "info"}:
                    raise ValueError("finding severity is invalid")
                if status not in {"open", "accepted", "resolved", "suppressed"}:
                    raise ValueError("finding status is invalid")
                if not category or not finding_title:
                    raise ValueError("finding category and title are required")
                metadata = finding.get("metadata", {})
                if not isinstance(metadata, dict):
                    raise ValueError("finding metadata must be an object")
                connection.execute(
                    """INSERT INTO scan_findings
                       (id, tenant_id, report_id, external_id, severity, category,
                        title, resource, status, metadata_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (f"finding_{uuid.uuid4().hex[:20]}", tenant_id, report_id,
                     str(finding.get("externalId", "")).strip() or None, severity,
                     category, finding_title, str(finding.get("resource", "")).strip(),
                     status, json.dumps(metadata, sort_keys=True)),
                )
        return report_id

    def get_scan_report(self, tenant_id: str, report_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            report = connection.execute(
                "SELECT * FROM scan_reports WHERE tenant_id = ? AND id = ?",
                (tenant_id, report_id),
            ).fetchone()
            if report is None:
                return None
            findings = connection.execute(
                """SELECT external_id, severity, category, title, resource, status,
                          metadata_json FROM scan_findings
                   WHERE tenant_id = ? AND report_id = ? ORDER BY severity, title""",
                (tenant_id, report_id),
            ).fetchall()
        finding_rows = [
            {"externalId": row["external_id"], "severity": row["severity"],
             "category": row["category"], "title": row["title"],
             "resource": row["resource"], "status": row["status"],
             "metadata": json.loads(row["metadata_json"])}
            for row in findings
        ]
        by_severity = {severity: sum(1 for row in finding_rows if row["severity"] == severity)
                       for severity in ("critical", "high", "medium", "low", "info")}
        return {"id": report["id"], "createdAt": report["created_at"],
                "source": report["source"], "project": report["project"],
                "title": report["title"], "metadata": json.loads(report["metadata_json"]),
                "findings": finding_rows,
                "summary": {**{key: value for key, value in by_severity.items() if value},
                            "total": len(finding_rows)}}

    def list_scan_reports(self, tenant_id: str, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT r.id, r.created_at, r.source, r.project, r.title,
                          COUNT(f.id) AS finding_count,
                          SUM(CASE WHEN f.severity = 'critical' THEN 1 ELSE 0 END) AS critical_count,
                          SUM(CASE WHEN f.severity = 'high' THEN 1 ELSE 0 END) AS high_count
                   FROM scan_reports r LEFT JOIN scan_findings f
                     ON f.tenant_id = r.tenant_id AND f.report_id = r.id
                   WHERE r.tenant_id = ? GROUP BY r.id
                   ORDER BY r.created_at DESC LIMIT ? OFFSET ?""",
                (tenant_id, limit, offset),
            ).fetchall()
        return [{"id": row["id"], "createdAt": row["created_at"], "source": row["source"],
                 "project": row["project"], "title": row["title"],
                 "findingCount": row["finding_count"], "criticalCount": row["critical_count"] or 0,
                 "highCount": row["high_count"] or 0} for row in rows]

    def get_scan_summary(self, tenant_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            report_count = connection.execute(
                "SELECT COUNT(*) FROM scan_reports WHERE tenant_id = ?", (tenant_id,)
            ).fetchone()[0]
            severity_rows = connection.execute(
                "SELECT severity, COUNT(*) AS count FROM scan_findings WHERE tenant_id = ? GROUP BY severity",
                (tenant_id,),
            ).fetchall()
            status_rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM scan_findings WHERE tenant_id = ? GROUP BY status",
                (tenant_id,),
            ).fetchall()
        return {"reports": report_count,
                "findingsBySeverity": {row["severity"]: row["count"] for row in severity_rows},
                "findingsByStatus": {row["status"]: row["count"] for row in status_rows}}

    def export_scan_findings(self, tenant_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT r.id AS report_id, r.created_at, r.source, r.project, r.title AS report_title,
                          f.external_id, f.severity, f.category, f.title, f.resource, f.status
                   FROM scan_reports r JOIN scan_findings f
                     ON f.tenant_id = r.tenant_id AND f.report_id = r.id
                   WHERE r.tenant_id = ? ORDER BY r.created_at DESC, f.severity, f.title""",
                (tenant_id,),
            ).fetchall()
        return [{"reportId": row["report_id"], "createdAt": row["created_at"],
                 "source": row["source"], "project": row["project"],
                 "reportTitle": row["report_title"], "externalId": row["external_id"] or "",
                 "severity": row["severity"], "category": row["category"],
                 "title": row["title"], "resource": row["resource"], "status": row["status"]}
                for row in rows]

    def scan_findings_csv(self, tenant_id: str) -> str:
        rows = self.export_scan_findings(tenant_id)
        output = io.StringIO()
        fields = ["reportId", "createdAt", "source", "project", "reportTitle", "externalId",
                  "severity", "category", "title", "resource", "status"]
        writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        return output.getvalue()
