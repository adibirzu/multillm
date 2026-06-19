# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Concrete MemoryRepo implementation against memory.db.

Plan 02b-01 Task 3. Implements the Phase 2a `MemoryRepo` Protocol with
parameterized SQL that filters every query by `tenant_id` — the AUTH-15
cross-tenant isolation contract.

The FTS5 search path uses parameterized `MATCH ? AND tenant_id = ?` — no
f-string interpolation of the search query into SQL. This is the AUTH-16
SQL-injection regression line: a malicious bearer like
``"safe' OR 1=1 --"`` will be matched as a literal FTS string, not
interpolated into the query.

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
    return conn


class MemoryRepoSqlite:
    """SQLite-backed implementation of the MemoryRepo Protocol."""

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

    def list_memories(self, tenant_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        """Recent memories for this tenant, newest first."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT id, created_at, updated_at, project, source_llm, "
                "       category, title, content, metadata "
                "FROM memories "
                "WHERE tenant_id = ? "
                "ORDER BY updated_at DESC "
                "LIMIT ?",
                (tenant_id, int(limit)),
            ).fetchall()
        return [dict(r) for r in rows]

    def search_memories(
        self, tenant_id: str, query: str, *, limit: int = 10
    ) -> list[dict[str, Any]]:
        """FTS5 search over memories, scoped to one tenant.

        Joins the FTS5 virtual table to the base ``memories`` table so we can
        apply both the FTS MATCH and the ``tenant_id`` filter without any
        f-string interpolation of the user-supplied query.
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT m.id, m.created_at, m.updated_at, m.project, m.source_llm, "
                "       m.category, m.title, m.content, m.metadata "
                "FROM memories_fts AS f "
                "JOIN memories AS m ON m.rowid = f.rowid "
                "WHERE f.memories_fts MATCH ? "
                "  AND m.tenant_id = ? "
                "ORDER BY rank "
                "LIMIT ?",
                (query, tenant_id, int(limit)),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_memory(self, tenant_id: str, memory_id: str) -> Optional[dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, created_at, updated_at, project, source_llm, "
                "       category, title, content, metadata "
                "FROM memories "
                "WHERE tenant_id = ? AND id = ?",
                (tenant_id, memory_id),
            ).fetchone()
        return dict(row) if row else None

    def store_memory(self, tenant_id: str, memory: dict[str, Any]) -> dict[str, Any]:
        """Insert one memory row tagged with this tenant.

        Required key: ``title``, ``content``. Optional: ``project``,
        ``source_llm``, ``category``, ``metadata``. Returns the stored row.
        """
        memory_id = memory.get("id") or f"mem_{uuid.uuid4().hex[:16]}"
        now = float(memory.get("created_at", time.time()))
        project = memory.get("project", "global")
        source_llm = memory.get("source_llm", "unknown")
        category = memory.get("category", "general")
        title = memory["title"]
        content = memory["content"]
        meta = json.dumps(memory.get("metadata", {}) or {})
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO memories "
                "(id, created_at, updated_at, project, source_llm, category, "
                " title, content, metadata, tenant_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    memory_id,
                    now,
                    now,
                    project,
                    source_llm,
                    category,
                    title,
                    content,
                    meta,
                    tenant_id,
                ),
            )
        return {
            "id": memory_id,
            "created_at": now,
            "updated_at": now,
            "project": project,
            "source_llm": source_llm,
            "category": category,
            "title": title,
            "content": content,
            "metadata": meta,
            "tenant_id": tenant_id,
        }

    def delete_memory(self, tenant_id: str, memory_id: str) -> bool:
        """Delete one memory row by id, scoped to one tenant. Returns True if a row was deleted."""
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM memories WHERE tenant_id = ? AND id = ?",
                (tenant_id, memory_id),
            )
            return cur.rowcount > 0
