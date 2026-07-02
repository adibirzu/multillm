"""Tenant-scoped durable storage for suites, runs, outputs, metrics, and reviews."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from .artifacts import ArtifactCipher
from .contracts import (
    EvaluationCase,
    EvaluationRunRequest,
    PairwiseDecision,
    PairwiseJudgment,
)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _csv_safe(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


class EvaluationStore:
    def __init__(
        self, path: str | Path, *, artifact_cipher: ArtifactCipher | None = None
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.artifact_cipher = artifact_cipher
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
                CREATE TABLE IF NOT EXISTS evaluation_suites (
                    tenant_id TEXT NOT NULL,
                    id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    source TEXT NOT NULL,
                    license_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY (tenant_id, id)
                );
                CREATE TABLE IF NOT EXISTS evaluation_cases (
                    tenant_id TEXT NOT NULL,
                    suite_id TEXT NOT NULL,
                    id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    case_json TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, suite_id, id),
                    FOREIGN KEY (tenant_id, suite_id)
                        REFERENCES evaluation_suites (tenant_id, id)
                );
                CREATE TABLE IF NOT EXISTS evaluation_runs (
                    tenant_id TEXT NOT NULL,
                    id TEXT NOT NULL,
                    suite_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    summary_json TEXT NOT NULL DEFAULT '{}',
                    worker_id TEXT,
                    lease_until REAL,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (tenant_id, id),
                    FOREIGN KEY (tenant_id, suite_id)
                        REFERENCES evaluation_suites (tenant_id, id)
                );
                CREATE INDEX IF NOT EXISTS idx_evaluation_runs_queue
                    ON evaluation_runs (status, lease_until, created_at);
                CREATE TABLE IF NOT EXISTS evaluation_outputs (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    target TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    content_encrypted BLOB NOT NULL,
                    content_hash TEXT NOT NULL,
                    usage_json TEXT NOT NULL,
                    latency_json TEXT NOT NULL,
                    cost_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE (tenant_id, run_id, case_id, target, attempt),
                    FOREIGN KEY (tenant_id, run_id)
                        REFERENCES evaluation_runs (tenant_id, id)
                );
                CREATE INDEX IF NOT EXISTS idx_evaluation_outputs_run
                    ON evaluation_outputs (tenant_id, run_id, case_id, target);
                CREATE TABLE IF NOT EXISTS evaluation_metrics (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    target TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 1,
                    metric TEXT NOT NULL,
                    value REAL,
                    passed INTEGER,
                    details_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE (tenant_id, run_id, case_id, target, attempt, metric),
                    FOREIGN KEY (tenant_id, run_id)
                        REFERENCES evaluation_runs (tenant_id, id)
                );
                CREATE TABLE IF NOT EXISTS evaluation_comparisons (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    candidate_target TEXT NOT NULL,
                    baseline_target TEXT NOT NULL,
                    decision TEXT NOT NULL DEFAULT 'abstain',
                    needs_human_review INTEGER NOT NULL DEFAULT 1,
                    human_decision TEXT,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    created_at REAL NOT NULL,
                    UNIQUE (tenant_id, run_id, case_id, candidate_target, baseline_target),
                    FOREIGN KEY (tenant_id, run_id)
                        REFERENCES evaluation_runs (tenant_id, id)
                );
                CREATE TABLE IF NOT EXISTS evaluation_judgments (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    comparison_id TEXT NOT NULL,
                    judge TEXT NOT NULL,
                    ordering TEXT NOT NULL,
                    judgment_encrypted BLOB NOT NULL,
                    content_hash TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE (tenant_id, comparison_id, judge, ordering),
                    FOREIGN KEY (comparison_id)
                        REFERENCES evaluation_comparisons (id)
                );
                CREATE TABLE IF NOT EXISTS evaluation_reviews (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    comparison_id TEXT NOT NULL,
                    reviewer_id TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    rationale_encrypted BLOB NOT NULL,
                    rationale_hash TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE (tenant_id, comparison_id, reviewer_id),
                    FOREIGN KEY (tenant_id, run_id)
                        REFERENCES evaluation_runs (tenant_id, id)
                );
                """
            )
            columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(evaluation_metrics)"
                ).fetchall()
            }
            if "attempt" not in columns:
                connection.executescript(
                    """
                    ALTER TABLE evaluation_metrics RENAME TO evaluation_metrics_legacy;
                    CREATE TABLE evaluation_metrics (
                        id TEXT PRIMARY KEY,
                        tenant_id TEXT NOT NULL,
                        run_id TEXT NOT NULL,
                        case_id TEXT NOT NULL,
                        target TEXT NOT NULL,
                        attempt INTEGER NOT NULL DEFAULT 1,
                        metric TEXT NOT NULL,
                        value REAL,
                        passed INTEGER,
                        details_json TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        UNIQUE (tenant_id, run_id, case_id, target, attempt, metric),
                        FOREIGN KEY (tenant_id, run_id)
                            REFERENCES evaluation_runs (tenant_id, id)
                    );
                    INSERT INTO evaluation_metrics
                        (id, tenant_id, run_id, case_id, target, attempt, metric,
                         value, passed, details_json, created_at)
                    SELECT id, tenant_id, run_id, case_id, target, 1, metric,
                           value, passed, details_json, created_at
                    FROM evaluation_metrics_legacy;
                    DROP TABLE evaluation_metrics_legacy;
                    """
                )

    def upsert_suite(
        self,
        tenant_id: str,
        *,
        suite_id: str,
        name: str,
        version: str,
        source: str,
        license_id: str,
        cases: tuple[EvaluationCase, ...] | list[EvaluationCase],
    ) -> dict[str, Any]:
        if not tenant_id.strip() or not cases:
            raise ValueError("tenant_id and at least one evaluation case are required")
        case_payload = [case.model_dump(mode="json") for case in cases]
        content_hash = hashlib.sha256(
            _canonical(case_payload).encode("utf-8")
        ).hexdigest()
        now = time.time()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT content_hash FROM evaluation_suites WHERE tenant_id = ? AND id = ?",
                (tenant_id, suite_id),
            ).fetchone()
            if existing and existing["content_hash"] != content_hash:
                raise ValueError("evaluation suites are immutable; use a new suite id")
            connection.execute(
                """INSERT OR IGNORE INTO evaluation_suites
                   (tenant_id, id, name, version, source, license_id, content_hash, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    tenant_id,
                    suite_id,
                    name,
                    version,
                    source,
                    license_id,
                    content_hash,
                    now,
                ),
            )
            for ordinal, case in enumerate(case_payload):
                connection.execute(
                    """INSERT OR IGNORE INTO evaluation_cases
                       (tenant_id, suite_id, id, ordinal, case_json)
                       VALUES (?, ?, ?, ?, ?)""",
                    (tenant_id, suite_id, case["id"], ordinal, _canonical(case)),
                )
        return self.get_suite(tenant_id, suite_id) or {}

    def get_suite(self, tenant_id: str, suite_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            suite = connection.execute(
                "SELECT * FROM evaluation_suites WHERE tenant_id = ? AND id = ?",
                (tenant_id, suite_id),
            ).fetchone()
            if suite is None:
                return None
            rows = connection.execute(
                """SELECT case_json FROM evaluation_cases
                   WHERE tenant_id = ? AND suite_id = ? ORDER BY ordinal""",
                (tenant_id, suite_id),
            ).fetchall()
        return {
            "id": suite["id"],
            "name": suite["name"],
            "version": suite["version"],
            "source": suite["source"],
            "licenseId": suite["license_id"],
            "contentHash": suite["content_hash"],
            "createdAt": suite["created_at"],
            "caseCount": len(rows),
            "cases": [json.loads(row["case_json"]) for row in rows],
        }

    def list_suites(self, tenant_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT s.*, COUNT(c.id) AS case_count
                   FROM evaluation_suites s LEFT JOIN evaluation_cases c
                     ON c.tenant_id = s.tenant_id AND c.suite_id = s.id
                   WHERE s.tenant_id = ? GROUP BY s.tenant_id, s.id
                   ORDER BY s.created_at DESC""",
                (tenant_id,),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "version": row["version"],
                "source": row["source"],
                "licenseId": row["license_id"],
                "contentHash": row["content_hash"],
                "caseCount": row["case_count"],
            }
            for row in rows
        ]

    def create_run(self, tenant_id: str, request: EvaluationRunRequest) -> str:
        if self.get_suite(tenant_id, request.suite_id) is None:
            raise ValueError("evaluation suite not found")
        run_id = f"eval_{uuid.uuid4().hex[:20]}"
        now = time.time()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO evaluation_runs
                   (tenant_id, id, suite_id, created_at, updated_at, status, request_json)
                   VALUES (?, ?, ?, ?, ?, 'queued', ?)""",
                (
                    tenant_id,
                    run_id,
                    request.suite_id,
                    now,
                    now,
                    _canonical(request.model_dump(mode="json")),
                ),
            )
        return run_id

    def claim_next_run(
        self, worker_id: str, *, lease_seconds: int = 60
    ) -> dict[str, Any] | None:
        now = time.time()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT tenant_id, id FROM evaluation_runs
                   WHERE cancel_requested = 0
                     AND (status = 'queued' OR (status = 'running' AND lease_until < ?))
                   ORDER BY created_at LIMIT 1""",
                (now,),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """UPDATE evaluation_runs SET status = 'running', worker_id = ?,
                          lease_until = ?, updated_at = ?
                   WHERE tenant_id = ? AND id = ?""",
                (worker_id, now + lease_seconds, now, row["tenant_id"], row["id"]),
            )
        return self.get_run(row["tenant_id"], row["id"], include_content=False)

    def heartbeat(
        self, tenant_id: str, run_id: str, worker_id: str, *, lease_seconds: int = 60
    ) -> bool:
        now = time.time()
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE evaluation_runs SET lease_until = ?, updated_at = ?
                   WHERE tenant_id = ? AND id = ? AND worker_id = ?
                     AND status = 'running' AND cancel_requested = 0""",
                (now + lease_seconds, now, tenant_id, run_id, worker_id),
            )
        return cursor.rowcount == 1

    def cancel_run(self, tenant_id: str, run_id: str) -> bool:
        now = time.time()
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE evaluation_runs
                   SET cancel_requested = 1,
                       status = CASE WHEN status IN ('queued', 'running') THEN 'cancelled' ELSE status END,
                       updated_at = ?
                   WHERE tenant_id = ? AND id = ?
                     AND status IN ('queued', 'running')""",
                (now, tenant_id, run_id),
            )
        return cursor.rowcount == 1

    def is_cancelled(self, tenant_id: str, run_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT status, cancel_requested FROM evaluation_runs
                   WHERE tenant_id = ? AND id = ?""",
                (tenant_id, run_id),
            ).fetchone()
        return bool(
            row is None or row["status"] == "cancelled" or row["cancel_requested"]
        )

    def complete_run(
        self, tenant_id: str, run_id: str, *, summary: dict[str, Any], status: str
    ) -> bool:
        if status not in {"completed", "failed", "incomplete"}:
            raise ValueError("terminal evaluation status is invalid")
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE evaluation_runs SET status = ?, summary_json = ?,
                          updated_at = ?, lease_until = NULL
                   WHERE tenant_id = ? AND id = ? AND status != 'cancelled'""",
                (status, _canonical(summary), time.time(), tenant_id, run_id),
            )
        return cursor.rowcount == 1

    def update_run_summary(
        self, tenant_id: str, run_id: str, summary: dict[str, Any]
    ) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE evaluation_runs SET summary_json = ?, updated_at = ?
                   WHERE tenant_id = ? AND id = ?
                     AND status IN ('completed', 'incomplete', 'failed')""",
                (_canonical(summary), time.time(), tenant_id, run_id),
            )
        return cursor.rowcount == 1

    def comparison_run_id(self, tenant_id: str, comparison_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT run_id FROM evaluation_comparisons
                   WHERE tenant_id = ? AND id = ?""",
                (tenant_id, comparison_id),
            ).fetchone()
        return None if row is None else str(row["run_id"])

    @staticmethod
    def _associated_data(
        tenant_id: str, run_id: str, case_id: str, target: str, attempt: int
    ) -> bytes:
        return f"{tenant_id}/{run_id}/{case_id}/{target}/{attempt}".encode("utf-8")

    def record_output(
        self,
        tenant_id: str,
        run_id: str,
        *,
        case_id: str,
        target: str,
        attempt: int,
        output_text: str,
        usage: dict[str, Any],
        latency: dict[str, Any],
        cost: dict[str, Any],
        status: str,
    ) -> str:
        if self.artifact_cipher is None:
            raise ValueError(
                "artifact encryption is required before retaining output text"
            )
        key = f"{tenant_id}\0{run_id}\0{case_id}\0{target}\0{attempt}"
        output_id = "out_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        plaintext = output_text.encode("utf-8")
        encrypted = self.artifact_cipher.encrypt(
            plaintext,
            associated_data=self._associated_data(
                tenant_id, run_id, case_id, target, attempt
            ),
        )
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO evaluation_outputs
                   (id, tenant_id, run_id, case_id, target, attempt,
                    content_encrypted, content_hash, usage_json, latency_json,
                    cost_json, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    output_id,
                    tenant_id,
                    run_id,
                    case_id,
                    target,
                    attempt,
                    encrypted,
                    hashlib.sha256(plaintext).hexdigest(),
                    _canonical(usage),
                    _canonical(latency),
                    _canonical(cost),
                    status,
                    time.time(),
                ),
            )
        return output_id

    def record_metric(
        self,
        tenant_id: str,
        run_id: str,
        *,
        case_id: str,
        target: str,
        attempt: int = 1,
        metric: str,
        value: float | None,
        passed: bool | None,
        details: dict[str, Any],
    ) -> str:
        if attempt < 1:
            raise ValueError("metric attempt must be positive")
        key = f"{tenant_id}\0{run_id}\0{case_id}\0{target}\0{attempt}\0{metric}"
        metric_id = "metric_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO evaluation_metrics
                   (id, tenant_id, run_id, case_id, target, attempt, metric, value,
                    passed, details_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (tenant_id, run_id, case_id, target, attempt, metric) DO UPDATE SET
                     value = excluded.value, passed = excluded.passed,
                     details_json = excluded.details_json, created_at = excluded.created_at""",
                (
                    metric_id,
                    tenant_id,
                    run_id,
                    case_id,
                    target,
                    attempt,
                    metric,
                    value,
                    None if passed is None else int(passed),
                    _canonical(details),
                    time.time(),
                ),
            )
        return metric_id

    def create_comparison(
        self,
        tenant_id: str,
        run_id: str,
        *,
        case_id: str,
        candidate_target: str,
        baseline_target: str,
    ) -> str:
        key = f"{tenant_id}\0{run_id}\0{case_id}\0{candidate_target}\0{baseline_target}"
        comparison_id = "cmp_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO evaluation_comparisons
                   (id, tenant_id, run_id, case_id, candidate_target,
                    baseline_target, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    comparison_id,
                    tenant_id,
                    run_id,
                    case_id,
                    candidate_target,
                    baseline_target,
                    time.time(),
                ),
            )
        return comparison_id

    @staticmethod
    def _judgment_data(
        tenant_id: str, comparison_id: str, judge: str, ordering: str
    ) -> bytes:
        return f"judgment/{tenant_id}/{comparison_id}/{judge}/{ordering}".encode(
            "utf-8"
        )

    def record_judgment(
        self,
        tenant_id: str,
        comparison_id: str,
        *,
        judge: str,
        ordering: str,
        judgment: PairwiseJudgment,
    ) -> str:
        if ordering not in {"normal", "swapped"}:
            raise ValueError("judgment ordering must be normal or swapped")
        if self.artifact_cipher is None:
            raise ValueError(
                "artifact encryption is required before retaining judgments"
            )
        key = f"{tenant_id}\0{comparison_id}\0{judge}\0{ordering}"
        judgment_id = "judge_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        plaintext = _canonical(judgment.model_dump(mode="json")).encode("utf-8")
        encrypted = self.artifact_cipher.encrypt(
            plaintext,
            associated_data=self._judgment_data(
                tenant_id, comparison_id, judge, ordering
            ),
        )
        with self._connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO evaluation_judgments
                   (id, tenant_id, comparison_id, judge, ordering,
                    judgment_encrypted, content_hash, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    judgment_id,
                    tenant_id,
                    comparison_id,
                    judge,
                    ordering,
                    encrypted,
                    hashlib.sha256(plaintext).hexdigest(),
                    time.time(),
                ),
            )
        return judgment_id

    def finalize_comparison(
        self,
        tenant_id: str,
        comparison_id: str,
        *,
        decision: PairwiseDecision | str,
        needs_human_review: bool,
        details: dict[str, Any] | None = None,
    ) -> bool:
        normalized = PairwiseDecision(decision).value
        with self._connect() as connection:
            cursor = connection.execute(
                """UPDATE evaluation_comparisons
                   SET decision = ?, needs_human_review = ?, details_json = ?
                   WHERE tenant_id = ? AND id = ?""",
                (
                    normalized,
                    int(needs_human_review),
                    _canonical(details or {}),
                    tenant_id,
                    comparison_id,
                ),
            )
        return cursor.rowcount == 1

    def _judgments(self, tenant_id: str, comparison_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM evaluation_judgments
                   WHERE tenant_id = ? AND comparison_id = ?
                   ORDER BY judge, ordering""",
                (tenant_id, comparison_id),
            ).fetchall()
        if rows and self.artifact_cipher is None:
            raise ValueError("artifact encryption key is required to read judgments")
        result: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(
                self.artifact_cipher.decrypt(
                    row["judgment_encrypted"],
                    associated_data=self._judgment_data(
                        tenant_id, comparison_id, row["judge"], row["ordering"]
                    ),
                ).decode("utf-8")
            )
            result.append(
                {
                    "id": row["id"],
                    "judge": row["judge"],
                    "ordering": row["ordering"],
                    **payload,
                }
            )
        return result

    def list_comparisons(self, tenant_id: str, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM evaluation_comparisons
                   WHERE tenant_id = ? AND run_id = ?
                   ORDER BY case_id, candidate_target, baseline_target""",
                (tenant_id, run_id),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "caseId": row["case_id"],
                "candidateTarget": row["candidate_target"],
                "baselineTarget": row["baseline_target"],
                "decision": row["decision"],
                "needsHumanReview": bool(row["needs_human_review"]),
                "humanDecision": row["human_decision"],
                "details": json.loads(row["details_json"]),
                "judgments": self._judgments(tenant_id, row["id"]),
            }
            for row in rows
        ]

    def review_queue(
        self, tenant_id: str, *, run_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        where = "WHERE tenant_id = ? AND needs_human_review = 1"
        values: list[Any] = [tenant_id]
        if run_id:
            where += " AND run_id = ?"
            values.append(run_id)
        values.append(min(max(limit, 1), 500))
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id, run_id, case_id, candidate_target, baseline_target
                   FROM evaluation_comparisons """
                + where
                + " ORDER BY created_at LIMIT ?",
                values,
            ).fetchall()
        queue: list[dict[str, Any]] = []
        output_cache: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
        for row in rows:
            outputs = output_cache.setdefault(
                row["run_id"],
                {
                    (item["caseId"], item["target"]): item
                    for item in self._outputs(
                        tenant_id, row["run_id"], include_content=True
                    )
                    if item["attempt"] == 1
                },
            )
            candidate = outputs.get((row["case_id"], row["candidate_target"]))
            baseline = outputs.get((row["case_id"], row["baseline_target"]))
            if not candidate or not baseline:
                continue
            swapped = self._review_is_swapped(row["id"])
            response_a, response_b = (
                (baseline, candidate) if swapped else (candidate, baseline)
            )
            queue.append(
                {
                    "id": row["id"],
                    "runId": row["run_id"],
                    "caseId": row["case_id"],
                    "responseA": response_a["outputText"],
                    "responseB": response_b["outputText"],
                }
            )
        return queue

    @staticmethod
    def _review_is_swapped(comparison_id: str) -> bool:
        digest = hashlib.sha256(
            f"blind-review:{comparison_id}".encode("utf-8")
        ).digest()
        return bool(digest[0] & 1)

    @classmethod
    def resolve_blind_review_decision(
        cls, comparison_id: str, decision: str
    ) -> PairwiseDecision:
        normalized = decision.strip().lower()
        if normalized == "tie":
            return PairwiseDecision.TIE
        if normalized not in {"response_a", "response_b"}:
            raise ValueError("decision must be response_a, response_b, or tie")
        response_a_is_candidate = not cls._review_is_swapped(comparison_id)
        chose_a = normalized == "response_a"
        return (
            PairwiseDecision.CANDIDATE
            if chose_a == response_a_is_candidate
            else PairwiseDecision.BASELINE
        )

    @staticmethod
    def _review_data(tenant_id: str, comparison_id: str, reviewer_id: str) -> bytes:
        return f"review/{tenant_id}/{comparison_id}/{reviewer_id}".encode("utf-8")

    def add_review(
        self,
        tenant_id: str,
        *,
        comparison_id: str,
        reviewer_id: str,
        decision: PairwiseDecision | str,
        rationale: str,
    ) -> bool:
        normalized = PairwiseDecision(decision).value
        if normalized == PairwiseDecision.ABSTAIN.value:
            raise ValueError("human review must choose candidate, baseline, or tie")
        if not reviewer_id.strip() or not rationale.strip():
            raise ValueError("reviewer_id and rationale are required")
        if self.artifact_cipher is None:
            raise ValueError("artifact encryption is required before retaining reviews")
        with self._connect() as connection:
            comparison = connection.execute(
                "SELECT run_id FROM evaluation_comparisons WHERE tenant_id = ? AND id = ?",
                (tenant_id, comparison_id),
            ).fetchone()
            if comparison is None:
                return False
            review_id = f"review_{uuid.uuid4().hex[:20]}"
            plaintext = rationale.encode("utf-8")
            encrypted = self.artifact_cipher.encrypt(
                plaintext,
                associated_data=self._review_data(
                    tenant_id, comparison_id, reviewer_id
                ),
            )
            connection.execute(
                """INSERT OR IGNORE INTO evaluation_reviews
                   (id, tenant_id, run_id, comparison_id, reviewer_id, decision,
                    rationale_encrypted, rationale_hash, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    review_id,
                    tenant_id,
                    comparison["run_id"],
                    comparison_id,
                    reviewer_id,
                    normalized,
                    encrypted,
                    hashlib.sha256(plaintext).hexdigest(),
                    time.time(),
                ),
            )
            connection.execute(
                """UPDATE evaluation_comparisons
                   SET human_decision = ?, needs_human_review = 0
                   WHERE tenant_id = ? AND id = ?""",
                (normalized, tenant_id, comparison_id),
            )
        return True

    def _reviews(self, tenant_id: str, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM evaluation_reviews
                   WHERE tenant_id = ? AND run_id = ?
                   ORDER BY created_at, id""",
                (tenant_id, run_id),
            ).fetchall()
        if rows and self.artifact_cipher is None:
            raise ValueError("artifact encryption key is required to read reviews")
        reviews: list[dict[str, Any]] = []
        for row in rows:
            rationale = self.artifact_cipher.decrypt(
                row["rationale_encrypted"],
                associated_data=self._review_data(
                    tenant_id, row["comparison_id"], row["reviewer_id"]
                ),
            ).decode("utf-8")
            reviews.append(
                {
                    "id": row["id"],
                    "comparisonId": row["comparison_id"],
                    "reviewerId": row["reviewer_id"],
                    "decision": row["decision"],
                    "rationale": rationale,
                    "rationaleHash": row["rationale_hash"],
                    "createdAt": row["created_at"],
                }
            )
        return reviews

    def _outputs(
        self, tenant_id: str, run_id: str, *, include_content: bool
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM evaluation_outputs WHERE tenant_id = ? AND run_id = ?
                   ORDER BY case_id, target, attempt""",
                (tenant_id, run_id),
            ).fetchall()
        outputs: list[dict[str, Any]] = []
        for row in rows:
            content: str | None = None
            if include_content:
                if self.artifact_cipher is None:
                    raise ValueError(
                        "artifact encryption key is required to read output text"
                    )
                content = self.artifact_cipher.decrypt(
                    row["content_encrypted"],
                    associated_data=self._associated_data(
                        tenant_id, run_id, row["case_id"], row["target"], row["attempt"]
                    ),
                ).decode("utf-8")
            outputs.append(
                {
                    "id": row["id"],
                    "caseId": row["case_id"],
                    "target": row["target"],
                    "attempt": row["attempt"],
                    "outputText": content,
                    "contentHash": row["content_hash"],
                    "usage": json.loads(row["usage_json"]),
                    "latency": json.loads(row["latency_json"]),
                    "cost": json.loads(row["cost_json"]),
                    "status": row["status"],
                }
            )
        return outputs

    def _metrics(self, tenant_id: str, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM evaluation_metrics WHERE tenant_id = ? AND run_id = ?
                   ORDER BY case_id, target, metric""",
                (tenant_id, run_id),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "caseId": row["case_id"],
                "target": row["target"],
                "attempt": row["attempt"],
                "metric": row["metric"],
                "value": row["value"],
                "passed": None if row["passed"] is None else bool(row["passed"]),
                "details": json.loads(row["details_json"]),
            }
            for row in rows
        ]

    def get_run(
        self, tenant_id: str, run_id: str, *, include_content: bool = True
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM evaluation_runs WHERE tenant_id = ? AND id = ?",
                (tenant_id, run_id),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "tenantId": tenant_id,
            "suiteId": row["suite_id"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "status": row["status"],
            "request": json.loads(row["request_json"]),
            "summary": json.loads(row["summary_json"]),
            "workerId": row["worker_id"],
            "leaseUntil": row["lease_until"],
            "cancelRequested": bool(row["cancel_requested"]),
            "outputs": self._outputs(
                tenant_id, run_id, include_content=include_content
            ),
            "metrics": self._metrics(tenant_id, run_id),
        }

    def list_runs(
        self, tenant_id: str, *, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id FROM evaluation_runs WHERE tenant_id = ?
                   ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (tenant_id, min(max(limit, 1), 200), max(offset, 0)),
            ).fetchall()
        return [
            run
            for row in rows
            if (run := self.get_run(tenant_id, row["id"], include_content=False))
            is not None
        ]

    def export_run(self, tenant_id: str, run_id: str) -> dict[str, Any]:
        run = self.get_run(tenant_id, run_id)
        if run is None:
            raise KeyError("evaluation run not found")
        suite = self.get_suite(tenant_id, run["suiteId"])
        payload = {
            "schemaVersion": 2,
            "run": run,
            "suite": suite,
            "comparisons": self.list_comparisons(tenant_id, run_id),
            "reviews": self._reviews(tenant_id, run_id),
        }
        digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
        return {
            "manifest": {
                "schemaVersion": 2,
                "runId": run_id,
                "suiteId": run["suiteId"],
                "sha256": digest,
            },
            **payload,
        }

    def export_run_csv(self, tenant_id: str, run_id: str) -> str:
        run = self.get_run(tenant_id, run_id)
        if run is None:
            raise KeyError("evaluation run not found")
        fields = [
            "recordType",
            "runId",
            "suiteId",
            "caseId",
            "target",
            "attempt",
            "status",
            "outputText",
            "contentHash",
            "inputTokens",
            "outputTokens",
            "stageUsage",
            "totalLatencyMs",
            "ttftMs",
            "actualCostUSD",
            "normalizedCostUSD",
            "comparisonId",
            "candidateTarget",
            "baselineTarget",
            "decision",
            "needsHumanReview",
            "humanDecision",
            "judge",
            "ordering",
            "correctness",
            "completeness",
            "depth",
            "coherence",
            "confidence",
            "rationale",
            "safetyFlags",
            "reviewerId",
        ]
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for item in run["outputs"]:
            row = {
                "recordType": "output",
                "runId": run_id,
                "suiteId": run["suiteId"],
                "caseId": item["caseId"],
                "target": item["target"],
                "attempt": item["attempt"],
                "status": item["status"],
                "outputText": item["outputText"],
                "contentHash": item["contentHash"],
                "inputTokens": item["usage"].get("input_tokens", 0),
                "outputTokens": item["usage"].get("output_tokens", 0),
                "stageUsage": _canonical(item["usage"].get("stages") or []),
                "totalLatencyMs": item["latency"].get("total_ms"),
                "ttftMs": item["latency"].get("ttft_ms"),
                "actualCostUSD": item["cost"].get("actual_usd"),
                "normalizedCostUSD": item["cost"].get("normalized_usd"),
            }
            writer.writerow({key: _csv_safe(value) for key, value in row.items()})
        for comparison in self.list_comparisons(tenant_id, run_id):
            comparison_row = {
                "recordType": "comparison",
                "runId": run_id,
                "suiteId": run["suiteId"],
                "caseId": comparison["caseId"],
                "comparisonId": comparison["id"],
                "candidateTarget": comparison["candidateTarget"],
                "baselineTarget": comparison["baselineTarget"],
                "decision": comparison["decision"],
                "needsHumanReview": comparison["needsHumanReview"],
                "humanDecision": comparison["humanDecision"],
            }
            writer.writerow(
                {key: _csv_safe(comparison_row.get(key, "")) for key in fields}
            )
            for judgment in comparison["judgments"]:
                judgment_row = {
                    "recordType": "judgment",
                    "runId": run_id,
                    "suiteId": run["suiteId"],
                    "caseId": comparison["caseId"],
                    "comparisonId": comparison["id"],
                    "judge": judgment["judge"],
                    "ordering": judgment["ordering"],
                    "decision": judgment["decision"],
                    "correctness": judgment["correctness"],
                    "completeness": judgment["completeness"],
                    "depth": judgment["depth"],
                    "coherence": judgment["coherence"],
                    "confidence": judgment["confidence"],
                    "rationale": judgment["rationale"],
                    "safetyFlags": ";".join(judgment.get("safety_flags") or ()),
                }
                writer.writerow(
                    {key: _csv_safe(judgment_row.get(key, "")) for key in fields}
                )
        for review in self._reviews(tenant_id, run_id):
            review_row = {
                "recordType": "review",
                "runId": run_id,
                "suiteId": run["suiteId"],
                "comparisonId": review["comparisonId"],
                "reviewerId": review["reviewerId"],
                "decision": review["decision"],
                "rationale": review["rationale"],
            }
            writer.writerow({key: _csv_safe(review_row.get(key, "")) for key in fields})
        return output.getvalue()
