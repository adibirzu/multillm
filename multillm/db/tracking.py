# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Concrete TrackingRepo implementation against the existing usage.db.

Plan 02b-01 Task 2. Implements the Phase 2a `TrackingRepo` Protocol with
parameterized SQL that includes `WHERE tenant_id = ?` on every query —
this is the AUTH-15 cross-tenant isolation contract.

Coexists with `multillm/tracking.py`: this repo is the new entrypoint for
tenant-aware data access; `multillm/tracking.py` keeps its existing
public API (delegating writes through `tenant_id="default"` per D-2b-03)
so the Phase 1 baseline tests continue to pass without churn.

D-2b-03: single tenant always "default" — but the repo accepts arbitrary
tenant_id so AUTH-15's two-tenant isolation test is meaningful.
D-2b-06: parameterized queries only. NO f-string interpolation of any
variable into a SQL string.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional


def _connect(db_path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


class TrackingRepoSqlite:
    """SQLite-backed implementation of the TrackingRepo Protocol.

    The repo owns its connection lifecycle for individual method calls so
    callers don't need to manage transactions manually. The connection is
    closed at the end of each public method.
    """

    def __init__(self, db_path: Path | str):
        self._db_path = Path(db_path)

    # ── Connection helper ──────────────────────────────────────────

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = _connect(self._db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ── Protocol methods ───────────────────────────────────────────

    def record_usage(self, tenant_id: str, usage: dict[str, Any]) -> None:
        """Insert one usage row.

        Required keys in ``usage``: ``model_alias``, ``backend``. Optional:
        ``project``, ``real_model``, ``input_tokens``, ``output_tokens``,
        ``cache_read_input_tokens``, ``cache_creation_input_tokens``,
        ``latency_ms``, ``cost_estimate_usd``, ``status``, ``error_message``,
        ``session_id``. Missing keys default to zero / empty / None.
        """
        usage_id = usage.get("id") or f"req_{uuid.uuid4().hex[:16]}"
        now = usage.get("timestamp", time.time())
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO usage
                   (id, timestamp, project, model_alias, backend, real_model,
                    input_tokens, output_tokens,
                    cache_read_input_tokens, cache_creation_input_tokens,
                    latency_ms, cost_estimate_usd,
                    status, error_message, session_id, tenant_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    usage_id,
                    now,
                    usage.get("project", "unknown"),
                    usage["model_alias"],
                    usage["backend"],
                    usage.get("real_model"),
                    int(usage.get("input_tokens", 0) or 0),
                    int(usage.get("output_tokens", 0) or 0),
                    int(usage.get("cache_read_input_tokens", 0) or 0),
                    int(usage.get("cache_creation_input_tokens", 0) or 0),
                    float(usage.get("latency_ms", 0.0) or 0.0),
                    float(usage.get("cost_estimate_usd", 0.0) or 0.0),
                    usage.get("status", "ok"),
                    usage.get("error_message"),
                    usage.get("session_id"),
                    tenant_id,
                ),
            )

    def get_dashboard(
        self,
        tenant_id: str,
        *,
        hours: int = 168,
        project: Optional[str] = None,
    ) -> dict[str, Any]:
        """Aggregated dashboard stats for one tenant.

        Returns a dict with keys: total_requests, total_input_tokens,
        total_output_tokens, total_cost_usd, error_count, per_backend
        (list of {backend, requests, tokens, cost}).
        """
        since = time.time() - (hours * 3600)
        with self._conn() as conn:
            # All scalars in one round-trip. WHERE tenant_id = ? on every
            # query is the AUTH-15 invariant.
            base_filter_sql = " WHERE tenant_id = ? AND timestamp > ?"
            base_params: list[Any] = [tenant_id, since]
            if project is not None:
                base_filter_sql += " AND project = ?"
                base_params.append(project)

            totals = conn.execute(
                "SELECT "
                "  COUNT(*)                       AS total_requests, "
                "  COALESCE(SUM(input_tokens), 0)  AS total_input_tokens, "
                "  COALESCE(SUM(output_tokens), 0) AS total_output_tokens, "
                "  COALESCE(SUM(cost_estimate_usd), 0.0) AS total_cost_usd, "
                "  COALESCE(SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END), 0) AS error_count "
                "FROM usage"
                + base_filter_sql,
                base_params,
            ).fetchone()

            per_backend_rows = conn.execute(
                "SELECT backend, "
                "  COUNT(*) AS requests, "
                "  COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens, "
                "  COALESCE(SUM(cost_estimate_usd), 0.0) AS cost "
                "FROM usage"
                + base_filter_sql
                + " GROUP BY backend ORDER BY cost DESC",
                base_params,
            ).fetchall()

        return {
            "tenant_id": tenant_id,
            "hours": hours,
            "project": project,
            "total_requests": int(totals["total_requests"]),
            "total_input_tokens": int(totals["total_input_tokens"]),
            "total_output_tokens": int(totals["total_output_tokens"]),
            "total_cost_usd": float(totals["total_cost_usd"]),
            "error_count": int(totals["error_count"]),
            "per_backend": [dict(r) for r in per_backend_rows],
        }

    def get_summary(self, tenant_id: str, *, hours: int = 24) -> dict[str, Any]:
        """Lightweight summary: per-model totals for the last `hours`."""
        since = time.time() - (hours * 3600)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT "
                "  model_alias, backend, "
                "  COUNT(*) AS request_count, "
                "  COALESCE(SUM(input_tokens), 0) AS total_input, "
                "  COALESCE(SUM(output_tokens), 0) AS total_output, "
                "  COALESCE(SUM(cost_estimate_usd), 0.0) AS total_cost_usd "
                "FROM usage "
                "WHERE tenant_id = ? AND timestamp > ? "
                "GROUP BY model_alias, backend "
                "ORDER BY total_cost_usd DESC",
                (tenant_id, since),
            ).fetchall()
        return {
            "tenant_id": tenant_id,
            "hours": hours,
            "models": [dict(r) for r in rows],
        }
