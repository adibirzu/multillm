"""
Shared memory for multi-LLM conversations and gateway settings.

Uses SQLite with FTS5 for fast full-text search and local RAG capabilities.
Memory is stored in the MultiLLM data directory and can be shared across machines.

Two storage areas:
- **Project memory**: per-project knowledge shared between LLMs (FTS5-indexed)
- **Settings store**: key-value settings for gateway configuration
"""

import json
import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Optional

from .config import DATA_DIR

log = logging.getLogger("multillm.memory")

MEMORY_DB = DATA_DIR / "memory.db"


def _init_memory_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        -- Core memory entries
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            project TEXT NOT NULL DEFAULT 'global',
            source_llm TEXT,
            category TEXT DEFAULT 'general',
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata TEXT DEFAULT '{}'
        );

        -- FTS5 index for fast full-text search (local RAG)
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
            title, content, project, category,
            content=memories,
            content_rowid=rowid
        );

        -- Triggers to keep FTS in sync
        CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, title, content, project, category)
            VALUES (new.rowid, new.title, new.content, new.project, new.category);
        END;

        CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, title, content, project, category)
            VALUES ('delete', old.rowid, old.title, old.content, old.project, old.category);
        END;

        CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, title, content, project, category)
            VALUES ('delete', old.rowid, old.title, old.content, old.project, old.category);
            INSERT INTO memories_fts(rowid, title, content, project, category)
            VALUES (new.rowid, new.title, new.content, new.project, new.category);
        END;

        -- Conversation context sharing between LLMs
        CREATE TABLE IF NOT EXISTS shared_context (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            created_at REAL NOT NULL,
            source_llm TEXT NOT NULL,
            target_llm TEXT DEFAULT '*',
            context_type TEXT DEFAULT 'info',
            content TEXT NOT NULL,
            expires_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_ctx_session ON shared_context(session_id);

        -- Settings store (key-value with JSON values)
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at REAL NOT NULL
        );
    """)


@contextmanager
def _get_memory_db():
    conn = sqlite3.connect(str(MEMORY_DB), timeout=5)
    conn.row_factory = sqlite3.Row
    _init_memory_db(conn)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ── Memory CRUD ──────────────────────────────────────────────────────────────

def store_memory(
    title: str,
    content: str,
    project: str = "global",
    source_llm: str = "unknown",
    category: str = "general",
    metadata: Optional[dict] = None,
) -> str:
    """Store a memory entry. Returns the memory ID."""
    mem_id = f"mem_{uuid.uuid4().hex[:16]}"
    now = time.time()
    with _get_memory_db() as conn:
        conn.execute(
            """INSERT INTO memories
               (id, created_at, updated_at, project, source_llm, category, title, content, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (mem_id, now, now, project, source_llm, category, title, content,
             json.dumps(metadata or {})),
        )
    return mem_id


def search_memory(
    query: str,
    project: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """Search memories using FTS5 full-text search (local RAG)."""
    with _get_memory_db() as conn:
        if project:
            rows = conn.execute(
                """SELECT m.id, m.title, m.content, m.project, m.source_llm,
                          m.category, m.created_at, m.metadata, rank
                   FROM memories_fts fts
                   JOIN memories m ON m.rowid = fts.rowid
                   WHERE memories_fts MATCH ? AND m.project = ?
                   ORDER BY rank LIMIT ?""",
                (query, project, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT m.id, m.title, m.content, m.project, m.source_llm,
                          m.category, m.created_at, m.metadata, rank
                   FROM memories_fts fts
                   JOIN memories m ON m.rowid = fts.rowid
                   WHERE memories_fts MATCH ?
                   ORDER BY rank LIMIT ?""",
                (query, limit),
            ).fetchall()
    return [dict(r) for r in rows]


def list_memories(
    project: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """List recent memories, optionally filtered by project/category."""
    with _get_memory_db() as conn:
        query = "SELECT id, title, project, source_llm, category, created_at FROM memories WHERE 1=1"
        params: list = []
        if project:
            query += " AND project = ?"
            params.append(project)
        if category:
            query += " AND category = ?"
            params.append(category)
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_memory(memory_id: str) -> Optional[dict]:
    """Get a single memory by ID."""
    with _get_memory_db() as conn:
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    return dict(row) if row else None


def delete_memory(memory_id: str) -> bool:
    """Delete a memory entry."""
    with _get_memory_db() as conn:
        cursor = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
    return cursor.rowcount > 0


# ── Shared Context (cross-LLM communication) ────────────────────────────────

def share_context(
    session_id: str,
    source_llm: str,
    content: str,
    context_type: str = "info",
    target_llm: str = "*",
    ttl_seconds: int = 3600,
) -> str:
    """Share context from one LLM to another within a session."""
    ctx_id = f"ctx_{uuid.uuid4().hex[:16]}"
    now = time.time()
    with _get_memory_db() as conn:
        conn.execute(
            """INSERT INTO shared_context
               (id, session_id, created_at, source_llm, target_llm, context_type, content, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (ctx_id, session_id, now, source_llm, target_llm, context_type, content, now + ttl_seconds),
        )
    return ctx_id


def get_shared_context(
    session_id: str,
    target_llm: Optional[str] = None,
) -> list[dict]:
    """Get shared context entries for a session, filtering expired ones."""
    now = time.time()
    with _get_memory_db() as conn:
        query = """SELECT * FROM shared_context
                   WHERE session_id = ? AND (expires_at IS NULL OR expires_at > ?)"""
        params: list = [session_id, now]
        if target_llm:
            query += " AND (target_llm = '*' OR target_llm = ?)"
            params.append(target_llm)
        query += " ORDER BY created_at ASC"
        rows = conn.execute(query, params).fetchall()
        conn.execute("DELETE FROM shared_context WHERE expires_at < ?", (now,))
    return [dict(r) for r in rows]


# ── Settings Store ───────────────────────────────────────────────────────────

_DEFAULT_SETTINGS = {
    "default_model": "ollama/llama3",
    "default_temperature": 0.7,
    "max_tokens_default": 4096,
    "streaming_enabled": True,
    "otel_enabled": False,
    "fallback_chain": ["ollama/qwen3-30b", "ollama/llama3", "ollama/mistral"],
    "auto_orchestration_enabled": True,
    "auto_second_opinion_model": "oca/gpt5",
    "auto_council_models": ["ollama/qwen3-30b", "oca/gpt5", "gemini/flash"],
    "auto_share_context": True,
    "usage_limits": {
        "claude_opus": 35_000_000,
        "claude_sonnet": 70_000_000,
        "claude_haiku": 140_000_000,
        "claude_other": 70_000_000,
        "gemini_cli": 14_000_000,
        "codex_cli_external": 70_000_000,
    },
}


def get_settings() -> dict:
    """Get all settings, merging defaults with stored values."""
    settings = dict(_DEFAULT_SETTINGS)
    with _get_memory_db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    for row in rows:
        try:
            settings[row["key"]] = json.loads(row["value"])
        except (json.JSONDecodeError, KeyError):
            pass
    return settings


def get_setting(key: str, default=None):
    """Get a single setting value."""
    with _get_memory_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row:
        try:
            return json.loads(row["value"])
        except json.JSONDecodeError:
            return row["value"]
    return _DEFAULT_SETTINGS.get(key, default)


def set_setting(key: str, value) -> None:
    """Set a single setting value."""
    now = time.time()
    with _get_memory_db() as conn:
        conn.execute(
            """INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (key, json.dumps(value), now),
        )


def update_settings(data: dict) -> None:
    """Update multiple settings at once."""
    for key, value in data.items():
        set_setting(key, value)


def delete_setting(key: str) -> bool:
    """Delete a setting, reverting to default."""
    with _get_memory_db() as conn:
        cursor = conn.execute("DELETE FROM settings WHERE key = ?", (key,))
    return cursor.rowcount > 0
