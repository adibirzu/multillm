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
from pathlib import Path
from typing import Optional

from .config import (
    DATA_DIR, OTEL_ENABLED, OTEL_SERVICE_NAME,
    OCI_APM_DOMAIN_ID, OCI_APM_DATA_KEY, OCI_APM_ENDPOINT,
)

log = logging.getLogger("multillm.tracking")

# ── SQLite Usage Store ───────────────────────────────────────────────────────

DB_PATH = DATA_DIR / "usage.db"

# Approximate cost per 1M tokens (USD) — for estimation only
COST_TABLE = {
    "ollama":       {"input": 0.0,    "output": 0.0},
    "lmstudio":     {"input": 0.0,    "output": 0.0},
    "codex_cli":    {"input": 0.0,    "output": 0.0},
    "gemini_cli":   {"input": 0.0,    "output": 0.0},
    "openrouter":   {"input": 2.50,   "output": 10.0},
    "openai":       {"input": 2.50,   "output": 10.0},
    "anthropic":    {"input": 3.0,    "output": 15.0},
    "oca":          {"input": 0.0,    "output": 0.0},    # Internal Oracle
    "gemini":       {"input": 0.075,  "output": 0.30},   # Flash pricing
    "groq":         {"input": 0.05,   "output": 0.08},   # Llama 70B pricing
    "deepseek":     {"input": 0.27,   "output": 1.10},   # DeepSeek-V3
    "mistral":      {"input": 2.0,    "output": 6.0},    # Mistral Large
    "together":     {"input": 0.88,   "output": 0.88},   # Llama 70B Turbo
    "xai":          {"input": 3.0,    "output": 15.0},   # Grok-3
    "fireworks":    {"input": 0.90,   "output": 0.90},   # Llama 70B
    "azure_openai": {"input": 2.50,   "output": 10.0},   # Same as OpenAI
    "bedrock":      {"input": 3.0,    "output": 15.0},   # Claude Sonnet pricing
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_usage_session ON usage(session_id)")


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
_sessions: dict[str, tuple[str, float]] = {}  # project -> (session_id, last_request_time)


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
        """INSERT INTO sessions (id, started_at, last_active_at, project)
           VALUES (?, ?, ?, ?)""",
        (session_id, now, now, project),
    )
    _sessions[project] = (session_id, now)
    log.info("New session started: %s (project=%s)", session_id, project)
    return session_id


def _update_session(conn: sqlite3.Connection, session_id: str, model_alias: str,
                    input_tokens: int, output_tokens: int, cost: float, now: float) -> None:
    """Update session aggregates."""
    # Get current models_used
    row = conn.execute("SELECT models_used FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if row:
        try:
            models = json.loads(row["models_used"] if isinstance(row["models_used"], str) else row[0])
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
                total_cost_usd = total_cost_usd + ?,
                models_used = ?
            WHERE id = ?""",
            (now, input_tokens, output_tokens, cost, json.dumps(models), session_id),
        )


def record_usage(
    project: str,
    model_alias: str,
    backend: str,
    real_model: str,
    input_tokens: int,
    output_tokens: int,
    latency_ms: float,
    status: str = "ok",
    error_message: Optional[str] = None,
) -> str:
    """Record a single LLM request to the usage database. Returns the usage ID."""
    costs = COST_TABLE.get(backend, {"input": 0, "output": 0})
    cost = (input_tokens * costs["input"] + output_tokens * costs["output"]) / 1_000_000
    now = time.time()
    usage_id = f"req_{uuid.uuid4().hex[:16]}"

    with _get_db() as conn:
        session_id = _get_or_create_session(conn, project, now)
        conn.execute(
            """INSERT INTO usage
               (id, timestamp, project, model_alias, backend, real_model,
                input_tokens, output_tokens, latency_ms, cost_estimate_usd,
                status, error_message, session_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                usage_id,
                now,
                project,
                model_alias,
                backend,
                real_model,
                input_tokens,
                output_tokens,
                latency_ms,
                cost,
                status,
                error_message,
                session_id,
            ),
        )
        _update_session(conn, session_id, model_alias, input_tokens, output_tokens, cost, now)
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
) -> None:
    """Update a streaming usage record with actual token counts after stream completes."""
    if not usage_id:
        return
    costs_backend = None
    with _get_db() as conn:
        row = conn.execute("SELECT backend, session_id FROM usage WHERE id = ?", (usage_id,)).fetchone()
        if not row:
            return
        backend = row["backend"]
        session_id = row["session_id"]
        costs = COST_TABLE.get(backend, {"input": 0, "output": 0})
        cost = (input_tokens * costs["input"] + output_tokens * costs["output"]) / 1_000_000
        conn.execute(
            """UPDATE usage SET input_tokens = ?, output_tokens = ?, cost_estimate_usd = ?
               WHERE id = ?""",
            (input_tokens, output_tokens, cost, usage_id),
        )
        if session_id:
            conn.execute(
                """UPDATE sessions SET
                    total_input_tokens = total_input_tokens + ?,
                    total_output_tokens = total_output_tokens + ?,
                    total_cost_usd = total_cost_usd + ?
                WHERE id = ?""",
                (input_tokens, output_tokens, cost, session_id),
            )
    log.debug("Updated streaming usage %s: in=%d out=%d", usage_id, input_tokens, output_tokens)


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


def get_sessions(hours: int = 168, project: Optional[str] = None, limit: int = 50) -> list[dict]:
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
        sess = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
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


def get_dashboard_stats(hours: int = 720) -> dict:
    """Get aggregated stats for the dashboard (default: last 30 days)."""
    since = time.time() - (hours * 3600)
    with _get_db() as conn:
        # Overall totals
        totals = conn.execute(
            """SELECT COUNT(*) as total_requests,
                      COALESCE(SUM(input_tokens), 0) as total_input,
                      COALESCE(SUM(output_tokens), 0) as total_output,
                      COALESCE(SUM(cost_estimate_usd), 0) as total_cost
               FROM usage WHERE timestamp > ?""",
            (since,),
        ).fetchone()

        # By backend
        by_backend = conn.execute(
            """SELECT backend,
                      COUNT(*) as requests,
                      COALESCE(SUM(input_tokens), 0) as input_tokens,
                      COALESCE(SUM(output_tokens), 0) as output_tokens,
                      COALESCE(SUM(cost_estimate_usd), 0) as cost_usd
               FROM usage WHERE timestamp > ?
               GROUP BY backend ORDER BY cost_usd DESC""",
            (since,),
        ).fetchall()

        # By model
        by_model = conn.execute(
            """SELECT model_alias, backend,
                      COUNT(*) as requests,
                      COALESCE(SUM(input_tokens), 0) as input_tokens,
                      COALESCE(SUM(output_tokens), 0) as output_tokens,
                      COALESCE(SUM(cost_estimate_usd), 0) as cost_usd,
                      AVG(latency_ms) as avg_latency_ms
               FROM usage WHERE timestamp > ?
               GROUP BY model_alias, backend ORDER BY requests DESC""",
            (since,),
        ).fetchall()

        # Daily breakdown (last 30 days)
        daily = conn.execute(
            """SELECT date(timestamp, 'unixepoch', 'localtime') as day,
                      COUNT(*) as requests,
                      COALESCE(SUM(input_tokens), 0) as input_tokens,
                      COALESCE(SUM(output_tokens), 0) as output_tokens,
                      COALESCE(SUM(cost_estimate_usd), 0) as cost_usd
               FROM usage WHERE timestamp > ?
               GROUP BY day ORDER BY day ASC""",
            (since,),
        ).fetchall()

        # Session count
        session_count = conn.execute(
            "SELECT COUNT(*) FROM sessions WHERE started_at > ?", (since,),
        ).fetchone()

        # Hourly breakdown (last 48 hours max)
        hourly_since = max(since, time.time() - 48 * 3600)
        hourly = conn.execute(
            """SELECT strftime('%Y-%m-%d %H:00', timestamp, 'unixepoch', 'localtime') as hour,
                      backend,
                      COUNT(*) as requests,
                      COALESCE(SUM(input_tokens), 0) as input_tokens,
                      COALESCE(SUM(output_tokens), 0) as output_tokens,
                      COALESCE(SUM(cost_estimate_usd), 0) as cost_usd
               FROM usage WHERE timestamp > ?
               GROUP BY hour, backend ORDER BY hour ASC""",
            (hourly_since,),
        ).fetchall()

    return {
        "totals": dict(totals) if totals else {},
        "session_count": session_count[0] if session_count else 0,
        "by_backend": [dict(r) for r in by_backend],
        "by_model": [dict(r) for r in by_model],
        "daily": [dict(r) for r in daily],
        "hourly": [dict(r) for r in hourly],
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
        # OCI APM requires the data key in the Authorization header
        # and uses a specific OTLP HTTP endpoint per APM domain.
        kwargs["endpoint"] = OCI_APM_ENDPOINT
        kwargs["headers"] = {
            "Authorization": f"dataKey {OCI_APM_DATA_KEY}",
        }
        log.info("OCI APM configured: endpoint=%s domain=%s",
                 OCI_APM_ENDPOINT, OCI_APM_DOMAIN_ID[:30] + "..." if OCI_APM_DOMAIN_ID else "?")
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
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

        exporter_kwargs = _build_otel_exporter_kwargs()

        resource = Resource.create({
            "service.name": OTEL_SERVICE_NAME,
            "service.version": "0.5.0",
            # OCI APM uses these resource attributes for grouping
            "deployment.environment": "production",
            "service.namespace": "llm-coding",
        })

        # Tracing — export to OCI APM or standard OTLP endpoint
        trace_exporter = OTLPSpanExporter(**exporter_kwargs)
        tp = TracerProvider(resource=resource)
        tp.add_span_processor(BatchSpanProcessor(trace_exporter))
        trace.set_tracer_provider(tp)
        _tracer = trace.get_tracer("multillm")

        # Metrics — OCI APM uses a separate metrics endpoint
        metrics_kwargs = dict(exporter_kwargs)
        if OCI_APM_ENDPOINT:
            # OCI APM metrics endpoint pattern
            metrics_kwargs["endpoint"] = OCI_APM_ENDPOINT.replace(
                "/opentelemetry/", "/opentelemetry/metrics/"
            ) if "/opentelemetry/" in OCI_APM_ENDPOINT else OCI_APM_ENDPOINT
        metric_exporter = OTLPMetricExporter(**metrics_kwargs)
        reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=30000)
        mp = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(mp)
        _meter = metrics.get_meter("multillm")

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
        log.info("OpenTelemetry initialized (service=%s, destination=%s)", OTEL_SERVICE_NAME, dest)

    except ImportError as e:
        log.warning("OpenTelemetry packages not available: %s", e)
    except Exception as e:
        log.error("OpenTelemetry init failed: %s", e)


@contextmanager
def trace_llm_call(model_alias: str, backend: str, project: str):
    """Context manager that creates an OTel span for an LLM call."""
    if _tracer:
        with _tracer.start_as_current_span(
            "llm.call",
            attributes={
                "llm.model": model_alias,
                "llm.backend": backend,
                "llm.project": project,
            },
        ) as span:
            yield span
    else:
        yield None


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
