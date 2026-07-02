# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""
Token usage tracking (SQLite) and OpenTelemetry instrumentation.

Tracks every LLM request: model, tokens, latency, project, cost estimate.
Exports traces/metrics to OCI APM via OTLP when enabled.
"""

import json
import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager
from typing import Optional

from .config import (
    DATA_DIR,
    OTEL_ENABLED,
    OTEL_SERVICE_NAME,
    OCI_APM_DOMAIN_ID,
    OCI_APM_DATA_KEY,
    OCI_APM_ENDPOINT,
    OCI_APM_DATA_KEY_TYPE,
    OCI_APM_METRICS_ENABLED,
)
from .model_registry import pricing_for


def _oci_apm_signal_endpoint(signal: str) -> str:
    """Build the correct OCI APM OTLP endpoint for a signal.

    OCI APM signal paths (authoritative, per OCI docs):
      - traces:  /20200101/opentelemetry/{private|public}/v1/traces
      - metrics: /20200101/opentelemetry/v1/metrics

    ``OCI_APM_ENDPOINT`` ends with ``/opentelemetry/``. The previous code posted
    traces to the bare base and rewrote metrics to a non-existent ``/metrics/``
    path, which returned 404 on every export.
    """
    base = (
        OCI_APM_ENDPOINT if OCI_APM_ENDPOINT.endswith("/") else OCI_APM_ENDPOINT + "/"
    )
    if signal == "traces":
        key_type = (
            OCI_APM_DATA_KEY_TYPE
            if OCI_APM_DATA_KEY_TYPE in ("private", "public")
            else "private"
        )
        return f"{base}{key_type}/v1/traces"
    return f"{base}v1/metrics"


log = logging.getLogger("multillm.tracking")

# ── SQLite Usage Store ───────────────────────────────────────────────────────

DB_PATH = DATA_DIR / "usage.db"

# Approximate cost per 1M tokens (USD) — for estimation only
COST_TABLE = {
    "ollama": {"input": 0.0, "output": 0.0},
    "lmstudio": {"input": 0.0, "output": 0.0},
    "claude_cli": {"input": 0.0, "output": 0.0},
    "codex_cli": {"input": 0.0, "output": 0.0},
    "gemini_cli": {"input": 0.0, "output": 0.0},
    "antigravity": {"input": 0.0, "output": 0.0},
    "openrouter": {"input": 2.50, "output": 10.0},
    "openai": {"input": 2.50, "output": 10.0},
    "anthropic": {"input": 3.0, "output": 15.0},
    "gemini": {"input": 0.075, "output": 0.30},  # Flash pricing
    "groq": {"input": 0.05, "output": 0.08},  # Llama 70B pricing
    "deepseek": {"input": 0.27, "output": 1.10},  # DeepSeek-V3
    "mistral": {"input": 2.0, "output": 6.0},  # Mistral Large
    "together": {"input": 0.88, "output": 0.88},  # Llama 70B Turbo
    "xai": {"input": 3.0, "output": 15.0},  # Grok-3
    "fireworks": {"input": 0.90, "output": 0.90},  # Llama 70B
    "azure_openai": {"input": 2.50, "output": 10.0},  # Same as OpenAI
    "bedrock": {"input": 3.0, "output": 15.0},  # Claude Sonnet pricing
    "oci_genai": {"input": 0.10, "output": 0.10},  # OCI GenAI (approx; Llama-class)
}


def _init_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS usage (
            id TEXT PRIMARY KEY,
            timestamp REAL NOT NULL,
            project TEXT NOT NULL DEFAULT 'unknown',
            model_alias TEXT NOT NULL,
            backend TEXT NOT NULL,
            real_model TEXT,
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cache_read_input_tokens INTEGER DEFAULT 0,
            cache_creation_input_tokens INTEGER DEFAULT 0,
            reasoning_tokens INTEGER DEFAULT 0,
            service_tier TEXT,
            latency_ms REAL DEFAULT 0,
            cost_estimate_usd REAL DEFAULT 0,
            status TEXT DEFAULT 'ok',
            error_message TEXT,
            session_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_usage_project ON usage(project);
        CREATE INDEX IF NOT EXISTS idx_usage_timestamp ON usage(timestamp);
        CREATE INDEX IF NOT EXISTS idx_usage_model ON usage(model_alias);
        CREATE INDEX IF NOT EXISTS idx_usage_session ON usage(session_id);

        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            started_at REAL NOT NULL,
            last_active_at REAL NOT NULL,
            project TEXT NOT NULL DEFAULT 'unknown',
            caller TEXT DEFAULT 'claude-code',
            total_requests INTEGER DEFAULT 0,
            total_input_tokens INTEGER DEFAULT 0,
            total_output_tokens INTEGER DEFAULT 0,
            total_cache_read_input_tokens INTEGER DEFAULT 0,
            total_cache_creation_input_tokens INTEGER DEFAULT 0,
            total_reasoning_tokens INTEGER DEFAULT 0,
            total_cost_usd REAL DEFAULT 0,
            models_used TEXT DEFAULT '[]'
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);
        CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project);
    """)
    # Add session_id column to existing usage table if missing
    try:
        conn.execute("SELECT session_id FROM usage LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE usage ADD COLUMN session_id TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_session ON usage(session_id)"
        )
    # AUTH-17: identifier-level ALTER TABLE statements written as literals
    # (no string interpolation into the SQL passed to .execute) so the
    # rg "execute\(.*f['\"]" gate stays clean.
    try:
        conn.execute("SELECT cache_read_input_tokens FROM usage LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE usage ADD COLUMN cache_read_input_tokens INTEGER DEFAULT 0"
        )
    try:
        conn.execute("SELECT cache_creation_input_tokens FROM usage LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE usage ADD COLUMN cache_creation_input_tokens INTEGER DEFAULT 0"
        )
    try:
        conn.execute("SELECT reasoning_tokens FROM usage LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE usage ADD COLUMN reasoning_tokens INTEGER DEFAULT 0")
    try:
        conn.execute("SELECT service_tier FROM usage LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute("ALTER TABLE usage ADD COLUMN service_tier TEXT")
    try:
        conn.execute("SELECT total_cache_read_input_tokens FROM sessions LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE sessions ADD COLUMN total_cache_read_input_tokens INTEGER DEFAULT 0"
        )
    try:
        conn.execute("SELECT total_cache_creation_input_tokens FROM sessions LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE sessions ADD COLUMN total_cache_creation_input_tokens INTEGER DEFAULT 0"
        )
    try:
        conn.execute("SELECT total_reasoning_tokens FROM sessions LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE sessions ADD COLUMN total_reasoning_tokens INTEGER DEFAULT 0"
        )
    # Plan 02b-01 Task 2: backfill tenant_id onto pre-existing rows.
    # tracking.py owns its own usage.db (separate from multillm.db), so the
    # 0003_auth_tenancy alembic migration's backfill cannot reach this table.
    # Per AUTH-17, all DDL is written as explicit literals (no f-string
    # interpolation into .execute()).
    try:
        conn.execute("SELECT tenant_id FROM usage LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE usage ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
        )
        conn.execute(
            "UPDATE usage SET tenant_id = 'default' WHERE tenant_id IS NULL OR tenant_id = ''"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_tenant ON usage(tenant_id)")
    try:
        conn.execute("SELECT tenant_id FROM sessions LIMIT 1")
    except sqlite3.OperationalError:
        conn.execute(
            "ALTER TABLE sessions ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'default'"
        )
        conn.execute(
            "UPDATE sessions SET tenant_id = 'default' WHERE tenant_id IS NULL OR tenant_id = ''"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_tenant ON sessions(tenant_id)"
        )


@contextmanager
def _get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_db(conn)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


SESSION_GAP_SECONDS = 300  # 5 minutes gap = new session

# Per-project session tracking to prevent cross-project contamination
_sessions: dict[
    str, tuple[str, float]
] = {}  # project -> (session_id, last_request_time)


def _get_or_create_session(conn: sqlite3.Connection, project: str, now: float) -> str:
    """Get current session or create a new one if the gap is too large."""
    entry = _sessions.get(project)
    if entry:
        session_id, last_time = entry
        if (now - last_time) < SESSION_GAP_SECONDS:
            _sessions[project] = (session_id, now)
            return session_id

    # Create new session
    session_id = f"sess_{uuid.uuid4().hex[:12]}"
    conn.execute(
        """INSERT INTO sessions (id, started_at, last_active_at, project, tenant_id)
           VALUES (?, ?, ?, ?, ?)""",
        (session_id, now, now, project, "default"),  # Plan 02b-01 Task 2: D-2b-03
    )
    _sessions[project] = (session_id, now)
    log.info("New session started: %s (project=%s)", session_id, project)
    return session_id


def _update_session(
    conn: sqlite3.Connection,
    session_id: str,
    model_alias: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_input_tokens: int,
    cache_creation_input_tokens: int,
    reasoning_tokens: int,
    cost: float,
    now: float,
) -> None:
    """Update session aggregates."""
    # Get current models_used
    row = conn.execute(
        "SELECT models_used FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if row:
        try:
            models = json.loads(
                row["models_used"] if isinstance(row["models_used"], str) else row[0]
            )
        except (json.JSONDecodeError, TypeError):
            models = []
        if model_alias not in models:
            models.append(model_alias)
        conn.execute(
            """UPDATE sessions SET
                last_active_at = ?,
                total_requests = total_requests + 1,
                total_input_tokens = total_input_tokens + ?,
                total_output_tokens = total_output_tokens + ?,
                total_cache_read_input_tokens = total_cache_read_input_tokens + ?,
                total_cache_creation_input_tokens = total_cache_creation_input_tokens + ?,
                total_reasoning_tokens = total_reasoning_tokens + ?,
                total_cost_usd = total_cost_usd + ?,
                models_used = ?
            WHERE id = ?""",
            (
                now,
                input_tokens,
                output_tokens,
                cache_read_input_tokens,
                cache_creation_input_tokens,
                reasoning_tokens,
                cost,
                json.dumps(models),
                session_id,
            ),
        )


def _estimate_cost(
    backend: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    real_model: str = "",
    reasoning_tokens: int = 0,
) -> float:
    pricing = pricing_for(backend, real_model)
    # OpenAI reports cached tokens inside input_tokens; Anthropic reports cache
    # reads/writes separately. Avoid charging OpenAI cached tokens twice.
    billable_input = input_tokens
    if backend in {"openai", "azure_openai"}:
        billable_input = max(
            0, input_tokens - cache_read_input_tokens - cache_creation_input_tokens
        )
    return pricing.estimate(
        input_tokens=billable_input,
        output_tokens=output_tokens,
        cached_read_tokens=cache_read_input_tokens,
        cache_write_tokens=cache_creation_input_tokens,
        reasoning_tokens=reasoning_tokens,
    )


def record_usage(
    project: str,
    model_alias: str,
    backend: str,
    real_model: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: float,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
    reasoning_tokens: int = 0,
    service_tier: str | None = None,
    status: str = "ok",
    error_message: Optional[str] = None,
) -> str:
    """Record a single LLM request to the usage database. Returns the usage ID."""
    cost = _estimate_cost(
        backend,
        input_tokens,
        output_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
        real_model=real_model,
        reasoning_tokens=reasoning_tokens,
    )
    now = time.time()
    usage_id = f"req_{uuid.uuid4().hex[:16]}"

    with _get_db() as conn:
        session_id = _get_or_create_session(conn, project, now)
        conn.execute(
            """INSERT INTO usage
               (id, timestamp, project, model_alias, backend, real_model,
                input_tokens, output_tokens, cache_read_input_tokens, cache_creation_input_tokens,
                reasoning_tokens, service_tier,
                latency_ms, cost_estimate_usd,
                status, error_message, session_id, tenant_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                usage_id,
                now,
                project,
                model_alias,
                backend,
                real_model,
                input_tokens,
                output_tokens,
                cache_read_input_tokens,
                cache_creation_input_tokens,
                reasoning_tokens,
                service_tier,
                latency_ms,
                cost,
                status,
                error_message,
                session_id,
                "default",  # Plan 02b-01 Task 2: single-tenant world; D-2b-03
            ),
        )
        _update_session(
            conn,
            session_id,
            model_alias,
            input_tokens,
            output_tokens,
            cache_read_input_tokens,
            cache_creation_input_tokens,
            reasoning_tokens,
            cost,
            now,
        )
    return usage_id


def get_usage_summary(
    project: Optional[str] = None,
    hours: int = 24,
) -> list[dict]:
    """Get usage summary grouped by model for the last N hours."""
    since = time.time() - (hours * 3600)
    with _get_db() as conn:
        query = """
            SELECT
                model_alias,
                backend,
                COUNT(*) as request_count,
                SUM(input_tokens) as total_input,
                SUM(output_tokens) as total_output,
                SUM(cache_read_input_tokens) as total_cache_read_input,
                SUM(cache_creation_input_tokens) as total_cache_creation_input,
                AVG(latency_ms) as avg_latency_ms,
                SUM(cost_estimate_usd) as total_cost_usd,
                SUM(CASE WHEN status != 'ok' THEN 1 ELSE 0 END) as error_count
            FROM usage
            WHERE timestamp > ?
        """
        params: list = [since]
        if project:
            query += " AND project = ?"
            params.append(project)
        query += " GROUP BY model_alias, backend ORDER BY total_cost_usd DESC"

        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_project_summary(hours: int = 24) -> list[dict]:
    """Get usage summary grouped by project."""
    since = time.time() - (hours * 3600)
    with _get_db() as conn:
        rows = conn.execute(
            """SELECT project,
                      COUNT(*) as requests,
                      SUM(input_tokens) as input_tokens,
                      SUM(output_tokens) as output_tokens,
                      SUM(cache_read_input_tokens) as cache_read_input_tokens,
                      SUM(cache_creation_input_tokens) as cache_creation_input_tokens,
                      SUM(cost_estimate_usd) as cost_usd
               FROM usage WHERE timestamp > ?
               GROUP BY project ORDER BY cost_usd DESC""",
            (since,),
        ).fetchall()
    return [dict(r) for r in rows]


def update_streaming_usage(
    usage_id: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_input_tokens: int = 0,
    cache_creation_input_tokens: int = 0,
) -> None:
    """Update a streaming usage record with actual token counts after stream completes."""
    if not usage_id:
        return
    with _get_db() as conn:
        row = conn.execute(
            "SELECT backend, session_id FROM usage WHERE id = ?", (usage_id,)
        ).fetchone()
        if not row:
            return
        backend = row["backend"]
        session_id = row["session_id"]
        cost = _estimate_cost(
            backend,
            input_tokens,
            output_tokens,
            cache_read_input_tokens=cache_read_input_tokens,
            cache_creation_input_tokens=cache_creation_input_tokens,
        )
        conn.execute(
            """UPDATE usage SET input_tokens = ?, output_tokens = ?,
                      cache_read_input_tokens = ?, cache_creation_input_tokens = ?, cost_estimate_usd = ?
               WHERE id = ?""",
            (
                input_tokens,
                output_tokens,
                cache_read_input_tokens,
                cache_creation_input_tokens,
                cost,
                usage_id,
            ),
        )
        if session_id:
            conn.execute(
                """UPDATE sessions SET
                    total_input_tokens = total_input_tokens + ?,
                    total_output_tokens = total_output_tokens + ?,
                    total_cache_read_input_tokens = total_cache_read_input_tokens + ?,
                    total_cache_creation_input_tokens = total_cache_creation_input_tokens + ?,
                    total_cost_usd = total_cost_usd + ?
                WHERE id = ?""",
                (
                    input_tokens,
                    output_tokens,
                    cache_read_input_tokens,
                    cache_creation_input_tokens,
                    cost,
                    session_id,
                ),
            )
    log.debug(
        "Updated streaming usage %s: in=%d out=%d",
        usage_id,
        input_tokens,
        output_tokens,
    )


def get_recent_backend_latency(
    backend: str,
    minutes: int = 30,
    limit: int = 20,
) -> Optional[float]:
    """Return average recent latency for a backend, or None if no samples exist.

    Uses the most recent successful-ish requests so adaptive routing can favor
    backends that are actually responding faster right now.
    """
    since = time.time() - (minutes * 60)
    with _get_db() as conn:
        rows = conn.execute(
            """SELECT latency_ms
               FROM usage
               WHERE backend = ?
                 AND timestamp > ?
                 AND latency_ms > 0
                 AND status NOT IN ('error', 'cache_hit')
               ORDER BY timestamp DESC
               LIMIT ?""",
            (backend, since, limit),
        ).fetchall()

    if not rows:
        return None

    samples = [float(row["latency_ms"]) for row in rows]
    return sum(samples) / len(samples)


# ── Session Queries ──────────────────────────────────────────────────────────


def get_active_sessions() -> list[dict]:
    """Get sessions active within the last SESSION_GAP_SECONDS (5 min)."""
    cutoff = time.time() - SESSION_GAP_SECONDS
    with _get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM sessions WHERE last_active_at > ? ORDER BY last_active_at DESC",
            (cutoff,),
        ).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        try:
            d["models_used"] = json.loads(d.get("models_used", "[]"))
        except (json.JSONDecodeError, TypeError):
            d["models_used"] = []
        d["active_seconds"] = int(time.time() - d["started_at"])
        results.append(d)
    return results


def get_sessions(
    hours: int = 168, project: Optional[str] = None, limit: int = 50
) -> list[dict]:
    """Get recent sessions (default: last 7 days)."""
    since = time.time() - (hours * 3600)
    with _get_db() as conn:
        query = "SELECT * FROM sessions WHERE started_at > ?"
        params: list = [since]
        if project:
            query += " AND project = ?"
            params.append(project)
        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
    results = []
    for r in rows:
        d = dict(r)
        try:
            d["models_used"] = json.loads(d.get("models_used", "[]"))
        except (json.JSONDecodeError, TypeError):
            d["models_used"] = []
        results.append(d)
    return results


def get_session_detail(session_id: str) -> dict:
    """Get a session with all its requests."""
    with _get_db() as conn:
        sess = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not sess:
            return {}
        requests = conn.execute(
            "SELECT * FROM usage WHERE session_id = ? ORDER BY timestamp ASC",
            (session_id,),
        ).fetchall()
    result = dict(sess)
    try:
        result["models_used"] = json.loads(result.get("models_used", "[]"))
    except (json.JSONDecodeError, TypeError):
        result["models_used"] = []
    result["requests"] = [dict(r) for r in requests]
    return result


def get_model_routing_stats(hours: int = 168, project: Optional[str] = None) -> dict:
    """Per-model performance from the usage log, for the query router.

    Returns ``{model_alias: {backend, requests, avgLatencyMs, avgCostUSD,
    errorRate}}`` over the window — the historical signal the router uses to
    learn which model performs well (FusionFactory query-level fusion idea).
    """
    since = time.time() - (hours * 3600)
    with _get_db() as conn:
        where_clause = "timestamp > ?"
        params: list = [since]
        if project:
            where_clause += " AND project = ?"
            params.append(project)
        rows = conn.execute(
            """SELECT model_alias, backend,
                      COUNT(*) as requests,
                      AVG(latency_ms) as avg_latency_ms,
                      AVG(cost_estimate_usd) as avg_cost_usd,
                      SUM(CASE WHEN status LIKE '%error%' THEN 1 ELSE 0 END) * 1.0 / COUNT(*) as error_rate
               FROM usage WHERE """
            + where_clause
            + """
               GROUP BY model_alias, backend""",
            params,
        ).fetchall()
    out: dict = {}
    for r in rows:
        out[r["model_alias"]] = {
            "backend": r["backend"],
            "requests": r["requests"],
            "avgLatencyMs": round(r["avg_latency_ms"] or 0, 1),
            "avgCostUSD": round(r["avg_cost_usd"] or 0, 6),
            "errorRate": round(r["error_rate"] or 0, 3),
        }
    return out


def get_dashboard_stats(hours: int = 720, project: Optional[str] = None) -> dict:
    """Get aggregated stats for the dashboard (default: last 30 days)."""
    since = time.time() - (hours * 3600)
    with _get_db() as conn:
        where_clause = "timestamp > ?"
        params: list = [since]
        if project:
            where_clause += " AND project = ?"
            params.append(project)

        # Overall totals
        totals = conn.execute(
            """SELECT COUNT(*) as total_requests,
                      COALESCE(SUM(input_tokens), 0) as total_input,
                      COALESCE(SUM(output_tokens), 0) as total_output,
                      COALESCE(SUM(cache_read_input_tokens), 0) as total_cache_read_input,
                      COALESCE(SUM(cache_creation_input_tokens), 0) as total_cache_creation_input,
                      COALESCE(SUM(cost_estimate_usd), 0) as total_cost
               FROM usage WHERE """
            + where_clause,
            params,
        ).fetchone()

        # By backend
        by_backend = conn.execute(
            """SELECT backend,
                      COUNT(*) as requests,
                      COALESCE(SUM(input_tokens), 0) as input_tokens,
                      COALESCE(SUM(output_tokens), 0) as output_tokens,
                      COALESCE(SUM(cache_read_input_tokens), 0) as cache_read_input_tokens,
                      COALESCE(SUM(cache_creation_input_tokens), 0) as cache_creation_input_tokens,
                      COALESCE(SUM(cost_estimate_usd), 0) as cost_usd
               FROM usage WHERE """
            + where_clause
            + """
               GROUP BY backend ORDER BY cost_usd DESC""",
            params,
        ).fetchall()

        # By model
        by_model = conn.execute(
            """SELECT model_alias, backend,
                      COUNT(*) as requests,
                      COALESCE(SUM(input_tokens), 0) as input_tokens,
                      COALESCE(SUM(output_tokens), 0) as output_tokens,
                      COALESCE(SUM(cache_read_input_tokens), 0) as cache_read_input_tokens,
                      COALESCE(SUM(cache_creation_input_tokens), 0) as cache_creation_input_tokens,
                      COALESCE(SUM(cost_estimate_usd), 0) as cost_usd,
                      AVG(latency_ms) as avg_latency_ms
               FROM usage WHERE """
            + where_clause
            + """
               GROUP BY model_alias, backend ORDER BY requests DESC""",
            params,
        ).fetchall()

        # Daily breakdown (last 30 days)
        daily = conn.execute(
            """SELECT date(timestamp, 'unixepoch', 'localtime') as day,
                      COUNT(*) as requests,
                      COALESCE(SUM(input_tokens), 0) as input_tokens,
                      COALESCE(SUM(output_tokens), 0) as output_tokens,
                      COALESCE(SUM(cache_read_input_tokens), 0) as cache_read_input_tokens,
                      COALESCE(SUM(cache_creation_input_tokens), 0) as cache_creation_input_tokens,
                      COALESCE(SUM(cost_estimate_usd), 0) as cost_usd
               FROM usage WHERE """
            + where_clause
            + """
               GROUP BY day ORDER BY day ASC""",
            params,
        ).fetchall()

        # Session count
        session_where = "started_at > ?"
        session_params: list = [since]
        if project:
            session_where += " AND project = ?"
            session_params.append(project)
        session_count = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE " + session_where,
            session_params,
        ).fetchone()

        # Status breakdown — surfaces fallback/cache_hit/error/streaming mix that
        # is collected per-request but was never aggregated for the dashboard.
        by_status = conn.execute(
            """SELECT COALESCE(status, 'unknown') as status,
                      COUNT(*) as requests
               FROM usage WHERE """
            + where_clause
            + """
               GROUP BY status ORDER BY requests DESC""",
            params,
        ).fetchall()

        # Recent errors — most recent failed requests with their messages.
        recent_errors = conn.execute(
            """SELECT timestamp, model_alias, backend, status, error_message
               FROM usage
               WHERE """
            + where_clause
            + """
                 AND (status = 'error' OR error_message IS NOT NULL)
               ORDER BY timestamp DESC LIMIT 20""",
            params,
        ).fetchall()

        # Hourly breakdown (last 168 hours max)
        hourly_since = max(since, time.time() - 168 * 3600)
        hourly_where = "timestamp > ?"
        hourly_params: list = [hourly_since]
        if project:
            hourly_where += " AND project = ?"
            hourly_params.append(project)
        hourly = conn.execute(
            """SELECT strftime('%Y-%m-%d %H:00', timestamp, 'unixepoch', 'localtime') as hour,
                      backend,
                      COUNT(*) as requests,
                      COALESCE(SUM(input_tokens), 0) as input_tokens,
                      COALESCE(SUM(output_tokens), 0) as output_tokens,
                      COALESCE(SUM(cache_read_input_tokens), 0) as cache_read_input_tokens,
                      COALESCE(SUM(cache_creation_input_tokens), 0) as cache_creation_input_tokens,
                      COALESCE(SUM(cost_estimate_usd), 0) as cost_usd
               FROM usage WHERE """
            + hourly_where
            + """
               GROUP BY hour, backend ORDER BY hour ASC""",
            hourly_params,
        ).fetchall()

    status_rows = [dict(r) for r in by_status]
    status_counts = {r["status"]: r["requests"] for r in status_rows}
    error_count = status_counts.get("error", 0)
    fallback_count = sum(
        c for s, c in status_counts.items() if s.startswith("fallback")
    )

    totals_dict = dict(totals) if totals else {}
    total_requests = totals_dict.get("total_requests", 0) or 0
    total_input = totals_dict.get("total_input", 0) or 0
    total_output = totals_dict.get("total_output", 0) or 0
    total_cache_read = totals_dict.get("total_cache_read_input", 0) or 0
    total_cache_creation = totals_dict.get("total_cache_creation_input", 0) or 0
    total_tokens = total_input + total_output + total_cache_read + total_cache_creation
    billable_input_tokens = total_input + total_cache_read + total_cache_creation
    total_cost = totals_dict.get("total_cost", 0) or 0
    session_total = session_count[0] if session_count else 0

    return {
        "hours": hours,
        "project": project,
        "totals": totals_dict,
        "session_count": session_total,
        "derived": {
            "total_tokens": total_tokens,
            "billable_input_tokens": billable_input_tokens,
            "cache_read_input_tokens": total_cache_read,
            "cache_creation_input_tokens": total_cache_creation,
            "avg_requests_per_session": (total_requests / session_total)
            if session_total
            else 0,
            "avg_tokens_per_request": (total_tokens / total_requests)
            if total_requests
            else 0,
            "avg_cost_per_request": (total_cost / total_requests)
            if total_requests
            else 0,
            "avg_cost_per_1k_tokens": ((total_cost / total_tokens) * 1000)
            if total_tokens
            else 0,
            "requests_per_hour": (total_requests / hours) if hours else 0,
            "tokens_per_hour": (total_tokens / hours) if hours else 0,
            "cost_per_hour": (total_cost / hours) if hours else 0,
        },
        "by_backend": [dict(r) for r in by_backend],
        "by_model": [dict(r) for r in by_model],
        "daily": [dict(r) for r in daily],
        "hourly": [dict(r) for r in hourly],
        "by_status": status_rows,
        "reliability": {
            "error_count": error_count,
            "fallback_count": fallback_count,
            "error_rate": (error_count / total_requests) if total_requests else 0,
            "fallback_rate": (fallback_count / total_requests) if total_requests else 0,
        },
        "recent_errors": [dict(r) for r in recent_errors],
    }


# ── OpenTelemetry ────────────────────────────────────────────────────────────

_tracer = None
_meter = None
_token_counter = None
_request_counter = None
_latency_histogram = None


def _build_otel_exporter_kwargs() -> dict:
    """Build OTLP exporter kwargs, using OCI APM if configured."""
    kwargs: dict = {}

    if OCI_APM_ENDPOINT and OCI_APM_DATA_KEY:
        # OCI APM requires the data key in the Authorization header. The
        # per-signal endpoint (traces vs metrics) is set by the caller via
        # _oci_apm_signal_endpoint(); we only supply the auth header here.
        kwargs["headers"] = {
            "Authorization": f"dataKey {OCI_APM_DATA_KEY}",
        }
        log.info(
            "OCI APM configured: base=%s domain=%s key_type=%s",
            OCI_APM_ENDPOINT,
            OCI_APM_DOMAIN_ID[:30] + "..." if OCI_APM_DOMAIN_ID else "?",
            OCI_APM_DATA_KEY_TYPE,
        )
    # Otherwise, fall back to standard OTEL_EXPORTER_OTLP_ENDPOINT env var
    return kwargs


def init_otel(app=None):
    """Initialize OpenTelemetry tracing and metrics.

    Supports two destinations:
    - OCI APM: Set OCI_APM_DOMAIN_ID + OCI_APM_DATA_KEY + OCI_APM_REGION
    - Standard OTLP: Set OTEL_EXPORTER_OTLP_ENDPOINT (default)
    """
    global _tracer, _meter, _token_counter, _request_counter, _latency_histogram

    if not OTEL_ENABLED:
        log.info("OpenTelemetry disabled (set OTEL_ENABLED=true to enable)")
        return

    try:
        from opentelemetry import trace, metrics
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        exporter_kwargs = _build_otel_exporter_kwargs()

        resource = Resource.create(
            {
                "service.name": OTEL_SERVICE_NAME,
                "service.version": "0.5.0",
                # OCI APM uses these resource attributes for grouping
                "deployment.environment": "production",
                "service.namespace": "llm-coding",
            }
        )

        # Tracing — OCI APM needs the explicit /…/v1/traces path; a standard
        # OTLP collector derives it from the base endpoint env var itself.
        trace_kwargs = dict(exporter_kwargs)
        if OCI_APM_ENDPOINT:
            trace_kwargs["endpoint"] = _oci_apm_signal_endpoint("traces")
        trace_exporter = OTLPSpanExporter(**trace_kwargs)
        tp = TracerProvider(resource=resource)
        tp.add_span_processor(BatchSpanProcessor(trace_exporter))
        trace.set_tracer_provider(tp)
        _tracer = trace.get_tracer("multillm")

        # Metrics — OCI APM uses /…/v1/metrics. Skippable for domains that don't
        # ingest metrics (avoids the 404 export-loop) while traces still flow.
        export_metrics = (not OCI_APM_ENDPOINT) or OCI_APM_METRICS_ENABLED
        if export_metrics:
            metrics_kwargs = dict(exporter_kwargs)
            if OCI_APM_ENDPOINT:
                metrics_kwargs["endpoint"] = _oci_apm_signal_endpoint("metrics")
            metric_exporter = OTLPMetricExporter(**metrics_kwargs)
            reader = PeriodicExportingMetricReader(
                metric_exporter, export_interval_millis=30000
            )
            mp = MeterProvider(resource=resource, metric_readers=[reader])
            metrics.set_meter_provider(mp)
            _meter = metrics.get_meter("multillm")
        else:
            # In-process meter with no exporter so /api/otel still reports metrics
            # are wired without shipping them to an endpoint that rejects them.
            mp = MeterProvider(resource=resource)
            metrics.set_meter_provider(mp)
            _meter = metrics.get_meter("multillm")
            log.info("OCI APM metrics export disabled (OCI_APM_METRICS_ENABLED=false)")

        _token_counter = _meter.create_counter(
            "llm.tokens",
            description="Total tokens consumed",
            unit="tokens",
        )
        _request_counter = _meter.create_counter(
            "llm.requests",
            description="Total LLM requests",
        )
        _latency_histogram = _meter.create_histogram(
            "llm.latency",
            description="LLM request latency",
            unit="ms",
        )

        # Auto-instrument FastAPI if app provided
        if app:
            try:
                from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

                FastAPIInstrumentor.instrument_app(app)
            except ImportError:
                log.debug("FastAPI instrumentation not available")

        dest = "OCI APM (LLM-CODING)" if OCI_APM_ENDPOINT else "standard OTLP"
        log.info(
            "OpenTelemetry initialized (service=%s, destination=%s)",
            OTEL_SERVICE_NAME,
            dest,
        )

    except ImportError as e:
        log.warning("OpenTelemetry packages not available: %s", e)
    except Exception as e:
        log.error("OpenTelemetry init failed: %s", e)


@contextmanager
def trace_llm_call(model_alias: str, backend: str, project: str):
    """Context manager that creates an OTel span for an LLM call.

    Uses GenAI semantic conventions (gen_ai.*) so downstream collectors
    (e.g., OTel Collector routing connector) can detect LLM traces and
    route them to Langfuse or other LLM-specific backends.
    """
    if _tracer:
        # Map backend to gen_ai.system value
        system_map = {
            "anthropic": "anthropic",
            "openai": "openai",
            "gemini": "google",
            "ollama": "ollama",
            "openrouter": "openrouter",
            "groq": "groq",
            "deepseek": "deepseek",
            "mistral": "mistral",
            "together": "together",
            "xai": "xai",
            "fireworks": "fireworks",
            "azure_openai": "azure",
            "bedrock": "aws",
            "lmstudio": "lmstudio",
            "codex_cli": "openai",
            "gemini_cli": "google",
            "claude_cli": "anthropic",
            "antigravity": "google",
        }
        with _tracer.start_as_current_span(
            "gen_ai.chat",
            attributes={
                # GenAI semantic conventions (for collector routing)
                "gen_ai.system": system_map.get(backend, backend),
                "gen_ai.request.model": model_alias,
                "gen_ai.operation.name": "chat",
                # MultiLLM-specific attributes
                "llm.model": model_alias,
                "llm.backend": backend,
                "llm.project": project,
                "llm.provider": backend,
            },
        ) as span:
            yield span
    else:
        yield None


def finalize_llm_span(
    span,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_create_tokens: int = 0,
    model_alias: str = "",
    status: str = "ok",
):
    """Set final GenAI attributes on a span after the LLM call completes."""
    if span is None:
        return
    try:
        span.set_attribute("gen_ai.response.model", model_alias)
        span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
        span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
        span.set_attribute("gen_ai.usage.total_tokens", input_tokens + output_tokens)
        if cache_read_tokens:
            span.set_attribute("gen_ai.usage.cache_read_tokens", cache_read_tokens)
        if cache_create_tokens:
            span.set_attribute("gen_ai.usage.cache_create_tokens", cache_create_tokens)
        if status != "ok":
            span.set_attribute("gen_ai.error", True)
    except Exception:
        pass


def record_otel_metrics(
    model_alias: str,
    backend: str,
    project: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: float,
    status: str = "ok",
):
    """Record OTel metrics for an LLM call."""
    attrs = {
        "llm.model": model_alias,
        "llm.backend": backend,
        "llm.project": project,
        "llm.status": status,
    }
    if _token_counter:
        _token_counter.add(input_tokens, {**attrs, "llm.token_type": "input"})
        _token_counter.add(output_tokens, {**attrs, "llm.token_type": "output"})
    if _request_counter:
        _request_counter.add(1, attrs)
    if _latency_histogram:
        _latency_histogram.record(latency_ms, attrs)
