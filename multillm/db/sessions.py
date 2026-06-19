# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Concrete SessionRepo implementation against the sessions table in usage.db.

Plan 02b-01 Task 3. Implements the Phase 2a `SessionRepo` Protocol with
parameterized SQL that filters every query by `tenant_id` — the AUTH-15
cross-tenant isolation contract.

Note: the project does not have a separate `multillm/sessions.py` module
(sessions live inside `multillm/tracking.py:_get_or_create_session`). This
repo reads/writes the same `sessions` table; the tracking.py session helper
populates rows with `tenant_id="default"` (Task 2).

D-2b-06: parameterized queries only.
"""

from __future__ import annotations

import json
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


class SessionRepoSqlite:
    """SQLite-backed implementation of the SessionRepo Protocol."""

    def __init__(self, db_path: Path | str):
        self._db_path = Path(db_path)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = _connect(self._db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ── Protocol methods ───────────────────────────────────────────

    def list_sessions(self, tenant_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        """Return up to ``limit`` recent sessions for this tenant."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, started_at, last_active_at, project, caller, "
                "       total_requests, total_input_tokens, total_output_tokens, "
                "       total_cost_usd, models_used "
                "FROM sessions "
                "WHERE tenant_id = ? "
                "ORDER BY started_at DESC "
                "LIMIT ?",
                (tenant_id, int(limit)),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_session(self, tenant_id: str, session_id: str) -> Optional[dict[str, Any]]:
        """Fetch a single session, scoped to the tenant."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, started_at, last_active_at, project, caller, "
                "       total_requests, total_input_tokens, total_output_tokens, "
                "       total_cost_usd, models_used "
                "FROM sessions "
                "WHERE tenant_id = ? AND id = ?",
                (tenant_id, session_id),
            ).fetchone()
        return dict(row) if row else None

    def create_session(self, tenant_id: str, session: dict[str, Any]) -> dict[str, Any]:
        """Insert a new session row for this tenant."""
        session_id = session.get("id") or f"sess_{uuid.uuid4().hex[:12]}"
        now = session.get("started_at", time.time())
        project = session.get("project", "unknown")
        caller = session.get("caller", "claude-code")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO sessions "
                "(id, started_at, last_active_at, project, caller, tenant_id, "
                " total_requests, total_input_tokens, total_output_tokens, "
                " total_cost_usd, models_used) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0, 0.0, '[]')",
                (session_id, now, now, project, caller, tenant_id),
            )
        return {
            "id": session_id,
            "started_at": now,
            "last_active_at": now,
            "project": project,
            "caller": caller,
            "tenant_id": tenant_id,
        }

    def append_request(
        self, tenant_id: str, session_id: str, request: dict[str, Any]
    ) -> None:
        """Update aggregates on the session row for one request.

        Required ``request`` keys are tolerant: missing keys default to 0.
        """
        now = float(request.get("timestamp", time.time()))
        input_tokens = int(request.get("input_tokens", 0) or 0)
        output_tokens = int(request.get("output_tokens", 0) or 0)
        cost = float(request.get("cost_usd", 0.0) or 0.0)
        model_alias = request.get("model_alias")
        with self._conn() as conn:
            # Read-then-write inside one connection scope is safe because
            # _conn yields a single Connection and commits at exit.
            row = conn.execute(
                "SELECT models_used FROM sessions WHERE tenant_id = ? AND id = ?",
                (tenant_id, session_id),
            ).fetchone()
            if row is None:
                return  # No-op when session doesn't exist for this tenant
            models = []
            try:
                models = json.loads(row["models_used"] or "[]")
            except (json.JSONDecodeError, TypeError):
                models = []
            if model_alias and model_alias not in models:
                models.append(model_alias)
            conn.execute(
                "UPDATE sessions SET "
                "  last_active_at = ?, "
                "  total_requests = total_requests + 1, "
                "  total_input_tokens = total_input_tokens + ?, "
                "  total_output_tokens = total_output_tokens + ?, "
                "  total_cost_usd = total_cost_usd + ?, "
                "  models_used = ? "
                "WHERE tenant_id = ? AND id = ?",
                (
                    now,
                    input_tokens,
                    output_tokens,
                    cost,
                    json.dumps(models),
                    tenant_id,
                    session_id,
                ),
            )
