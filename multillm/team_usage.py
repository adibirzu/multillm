# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""
Multi-user, multi-account LLM usage aggregation.

Captures per-UNIX-user, per-LLM-account usage on shared developer
workstations (e.g. the OCI Remote Dev VM). A lightweight per-user collector
runs *as each developer*, reads that developer's local CLI stats
(``~/.claude``, ``~/.codex``, ``~/.gemini``) via the existing stats readers,
and POSTs a daily snapshot to the gateway's ``/api/usage/ingest`` endpoint.

The gateway stores snapshots in a ``team_usage`` table keyed by
``(tenant_id, backend, account, model, day)`` and serves an aggregated team
view at ``/api/team-usage`` (rendered by ``/team``).

Snapshot semantics
------------------
Each collector run reports the *cumulative daily total* for the user. Ingest
therefore UPSERTs on the natural key (REPLACE the row) instead of summing, so
re-running the collector is idempotent and never double-counts. A delta/append
model would double-count on every overlapping window.

Security / multi-tenancy
------------------------
``tenant_id`` is the developer's UNIX username (the workstation identity).
``account`` is the LLM provider account label (e.g. an email or org) the
developer authenticated that CLI with — this is what distinguishes "Adi's
Claude Max" from "Royce's Claude API" usage. The collector never transmits
tokens or credentials; it only forwards aggregate counts and a best-effort
account label.

All SQL is written as string literals (no f-string interpolation into
``.execute()``) to satisfy the AUTH-17 CI grep gate.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from .config import DATA_DIR

log = logging.getLogger("multillm.team_usage")

# Reuse the tracking database file so the team view can live alongside the
# per-request ``usage`` table; ``team_usage`` is an independent table.
DB_PATH = DATA_DIR / "usage.db"

# Canonical backend identifiers for locally-installed AI CLIs.
BACKEND_CLAUDE = "claude"
BACKEND_CODEX = "codex"
BACKEND_GEMINI = "gemini"
KNOWN_BACKENDS = (BACKEND_CLAUDE, BACKEND_CODEX, BACKEND_GEMINI)


# ── Record model ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TeamUsageRecord:
    """A single per-user/per-account/per-model daily usage snapshot."""

    tenant_id: str  # UNIX user / workstation identity
    backend: str  # claude | codex | gemini
    account: str  # provider account label (email/org), '' if unknown
    model: str
    day: str  # YYYY-MM-DD (collector-local date)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_tokens: int = 0
    requests: int = 0
    cost_usd: float = 0.0
    source_host: str = ""  # hostname of the workstation

    def key(self) -> tuple[str, str, str, str, str]:
        return (self.tenant_id, self.backend, self.account, self.model, self.day)

    def to_payload(self) -> dict:
        return asdict(self)


def record_from_dict(d: dict) -> TeamUsageRecord:
    """Build a validated TeamUsageRecord from an ingest payload dict."""
    tenant_id = str(d.get("tenant_id") or "").strip()
    backend = str(d.get("backend") or "").strip().lower()
    model = str(d.get("model") or "unknown").strip() or "unknown"
    day = str(d.get("day") or "").strip()
    if not tenant_id:
        raise ValueError("tenant_id is required")
    if backend not in KNOWN_BACKENDS:
        raise ValueError("backend must be one of %s" % (KNOWN_BACKENDS,))
    if not _valid_day(day):
        raise ValueError("day must be YYYY-MM-DD")
    return TeamUsageRecord(
        tenant_id=tenant_id,
        backend=backend,
        account=str(d.get("account") or "").strip(),
        model=model,
        day=day,
        input_tokens=_int(d.get("input_tokens")),
        output_tokens=_int(d.get("output_tokens")),
        cache_tokens=_int(d.get("cache_tokens")),
        requests=_int(d.get("requests")),
        cost_usd=_float(d.get("cost_usd")),
        source_host=str(d.get("source_host") or "").strip(),
    )


def _int(v: object) -> int:
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


def _float(v: object) -> float:
    try:
        return max(0.0, float(v or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _valid_day(day: str) -> bool:
    try:
        datetime.strptime(day, "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False


# ── Storage ──────────────────────────────────────────────────────────────────


def _init_team_db(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS team_usage (
            tenant_id     TEXT NOT NULL,
            backend       TEXT NOT NULL,
            account       TEXT NOT NULL DEFAULT '',
            model         TEXT NOT NULL DEFAULT 'unknown',
            day           TEXT NOT NULL,
            input_tokens  INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            cache_tokens  INTEGER NOT NULL DEFAULT 0,
            requests      INTEGER NOT NULL DEFAULT 0,
            cost_usd      REAL NOT NULL DEFAULT 0,
            source_host   TEXT NOT NULL DEFAULT '',
            updated_at    REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (tenant_id, backend, account, model, day)
        );
        CREATE INDEX IF NOT EXISTS idx_team_usage_day ON team_usage(day);
        CREATE INDEX IF NOT EXISTS idx_team_usage_tenant ON team_usage(tenant_id);
        CREATE INDEX IF NOT EXISTS idx_team_usage_backend ON team_usage(backend);
    """)


@contextmanager
def _get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    _init_team_db(conn)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def record_team_usage(records: Iterable[TeamUsageRecord]) -> int:
    """
    UPSERT a batch of daily usage snapshots. Returns the number of rows written.

    Snapshot-replace semantics: the natural key
    ``(tenant_id, backend, account, model, day)`` is overwritten with the
    incoming counts. The collector always sends the cumulative daily total, so
    replacing (not summing) keeps re-runs idempotent.
    """
    now = datetime.now(timezone.utc).timestamp()
    rows = [
        (
            r.tenant_id,
            r.backend,
            r.account,
            r.model,
            r.day,
            r.input_tokens,
            r.output_tokens,
            r.cache_tokens,
            r.requests,
            r.cost_usd,
            r.source_host,
            now,
        )
        for r in records
    ]
    if not rows:
        return 0
    with _get_db() as conn:
        conn.executemany(
            """INSERT INTO team_usage
               (tenant_id, backend, account, model, day,
                input_tokens, output_tokens, cache_tokens,
                requests, cost_usd, source_host, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(tenant_id, backend, account, model, day)
               DO UPDATE SET
                   input_tokens  = excluded.input_tokens,
                   output_tokens = excluded.output_tokens,
                   cache_tokens  = excluded.cache_tokens,
                   requests      = excluded.requests,
                   cost_usd      = excluded.cost_usd,
                   source_host   = excluded.source_host,
                   updated_at    = excluded.updated_at""",
            rows,
        )
    return len(rows)


def get_team_usage(hours: int = 168, tenant: Optional[str] = None) -> dict:
    """
    Aggregated multi-user usage rollup for the dashboard / API.

    Returns totals plus three breakdowns: by user (tenant), by account, and by
    backend. ``hours`` filters by day window (inclusive of any day touched by
    the window). ``tenant`` optionally restricts to a single developer.
    """
    cutoff_day = _day_cutoff(hours)
    params: list[object] = [cutoff_day]
    where = "WHERE day >= ?"
    if tenant:
        where += " AND tenant_id = ?"
        params.append(tenant)

    with _get_db() as conn:
        by_user = _query_group(conn, "tenant_id", where, params)
        by_account = _query_group(conn, "account", where, params, label_backend=True)
        by_backend = _query_group(conn, "backend", where, params)
        by_user_day = _query_user_day(conn, where, params)
        totals = _query_totals(conn, where, params)

    return {
        "window_hours": hours,
        "since_day": cutoff_day,
        "totals": totals,
        "by_user": by_user,
        "by_account": by_account,
        "by_backend": by_backend,
        "by_user_day": by_user_day,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# The SELECT column whitelist guards against any caller passing an arbitrary
# column name into the grouped queries (defence in depth for the AUTH-17 gate).
_GROUP_COLUMNS = {"tenant_id", "account", "backend", "model"}


def _query_group(
    conn: sqlite3.Connection,
    column: str,
    where: str,
    params: list[object],
    label_backend: bool = False,
) -> list[dict]:
    if column not in _GROUP_COLUMNS:
        raise ValueError("illegal group column")
    # column is whitelisted above, never user input — safe to format the
    # identifier. Values are still bound parameters.
    extra = ", backend" if label_backend else ""
    sql = (
        "SELECT " + column + " AS bucket" + extra + ", "
        "SUM(input_tokens) AS input_tokens, "
        "SUM(output_tokens) AS output_tokens, "
        "SUM(cache_tokens) AS cache_tokens, "
        "SUM(requests) AS requests, "
        "SUM(cost_usd) AS cost_usd "
        "FROM team_usage " + where + " "
        "GROUP BY " + column + extra + " "
        "ORDER BY cost_usd DESC, output_tokens DESC"
    )
    out = []
    for row in conn.execute(sql, params).fetchall():
        d = {
            "bucket": row["bucket"] or "(unknown)",
            "input_tokens": row["input_tokens"] or 0,
            "output_tokens": row["output_tokens"] or 0,
            "cache_tokens": row["cache_tokens"] or 0,
            "requests": row["requests"] or 0,
            "cost_usd": round(row["cost_usd"] or 0.0, 4),
        }
        if label_backend:
            d["backend"] = row["backend"]
        out.append(d)
    return out


def _query_user_day(
    conn: sqlite3.Connection, where: str, params: list[object]
) -> list[dict]:
    sql = (
        "SELECT day, tenant_id, "
        "SUM(input_tokens + output_tokens) AS tokens, "
        "SUM(cost_usd) AS cost_usd "
        "FROM team_usage " + where + " "
        "GROUP BY day, tenant_id ORDER BY day ASC"
    )
    return [
        {
            "day": row["day"],
            "tenant_id": row["tenant_id"],
            "tokens": row["tokens"] or 0,
            "cost_usd": round(row["cost_usd"] or 0.0, 4),
        }
        for row in conn.execute(sql, params).fetchall()
    ]


def _query_totals(conn: sqlite3.Connection, where: str, params: list[object]) -> dict:
    sql = (
        "SELECT "
        "COUNT(DISTINCT tenant_id) AS users, "
        "COUNT(DISTINCT account) AS accounts, "
        "SUM(input_tokens) AS input_tokens, "
        "SUM(output_tokens) AS output_tokens, "
        "SUM(cache_tokens) AS cache_tokens, "
        "SUM(requests) AS requests, "
        "SUM(cost_usd) AS cost_usd "
        "FROM team_usage " + where
    )
    row = conn.execute(sql, params).fetchone()
    return {
        "users": row["users"] or 0,
        "accounts": row["accounts"] or 0,
        "input_tokens": row["input_tokens"] or 0,
        "output_tokens": row["output_tokens"] or 0,
        "cache_tokens": row["cache_tokens"] or 0,
        "requests": row["requests"] or 0,
        "cost_usd": round(row["cost_usd"] or 0.0, 4),
    }


def _day_cutoff(hours: int) -> str:
    cutoff = datetime.now(timezone.utc).timestamp() - max(1, hours) * 3600
    return datetime.fromtimestamp(cutoff, tz=timezone.utc).strftime("%Y-%m-%d")


# ── Collection (runs as each developer on the workstation) ───────────────────


def _g(d: dict, *keys: str) -> int:
    """First present integer-ish value among ``keys`` in dict ``d``."""
    for k in keys:
        if k in d and d[k] is not None:
            return _int(d[k])
    return 0


def _gf(d: dict, *keys: str) -> float:
    for k in keys:
        if k in d and d[k] is not None:
            return _float(d[k])
    return 0.0


def _day_from_entry(entry: dict, fallback: str) -> str:
    for k in ("day", "date"):
        v = entry.get(k)
        if v and _valid_day(str(v)):
            return str(v)
    return fallback


def _records_from_stats(
    stats: dict, *, tenant: str, backend: str, account: str, host: str, today: str
) -> list[TeamUsageRecord]:
    """
    Normalize one stats-reader payload into daily snapshot records.

    Prefers the per-day ``daily`` breakdown so history is preserved; falls back
    to a single ``total`` snapshot stamped with today's date when ``daily`` is
    absent. Token key names differ slightly per CLI, so lookups are tolerant.
    """
    records: list[TeamUsageRecord] = []
    daily = stats.get("daily") or []
    if isinstance(daily, list) and daily:
        for entry in daily:
            if not isinstance(entry, dict):
                continue
            inp = _g(entry, "input_tokens", "input", "prompt_tokens")
            out = _g(entry, "output_tokens", "output", "completion_tokens")
            cache = _g(
                entry,
                "cache_tokens",
                "cached_input",
                "cache_read",
                "cache_read_input_tokens",
                "cached",
            )
            if not (inp or out or cache):
                continue
            records.append(
                TeamUsageRecord(
                    tenant_id=tenant,
                    backend=backend,
                    account=account,
                    model=str(entry.get("model") or "all"),
                    day=_day_from_entry(entry, today),
                    input_tokens=inp,
                    output_tokens=out,
                    cache_tokens=cache,
                    requests=_g(entry, "requests", "messages", "sessions"),
                    cost_usd=_gf(entry, "cost_usd", "cost", "cost_estimate"),
                    source_host=host,
                )
            )
        if records:
            return records

    total = stats.get("total") or {}
    inp = _g(total, "input_tokens", "input", "prompt_tokens")
    out = _g(total, "output_tokens", "output", "completion_tokens")
    cache = _g(
        total,
        "cache_tokens",
        "cached_input",
        "cache_read",
        "cache_read_input_tokens",
        "cached",
    )
    if inp or out or cache:
        records.append(
            TeamUsageRecord(
                tenant_id=tenant,
                backend=backend,
                account=account,
                model="all",
                day=today,
                input_tokens=inp,
                output_tokens=out,
                cache_tokens=cache,
                requests=_g(total, "requests", "messages", "sessions"),
                cost_usd=_gf(stats, "cost_estimate") or _gf(total, "cost_usd", "cost"),
                source_host=host,
            )
        )
    return records


def detect_account(backend: str, home: Optional[Path] = None) -> str:
    """
    Best-effort provider account label for the running user.

    Reads only non-secret identity fields (email / account id) from each CLI's
    local config. Returns '' when nothing can be determined — the dashboard
    then groups by backend alone for that user.
    """
    home = home or Path.home()
    try:
        if backend == BACKEND_CLAUDE:
            cfg = home / ".claude.json"
            if cfg.exists():
                data = json.loads(cfg.read_text())
                acct = data.get("oauthAccount") or {}
                return str(acct.get("emailAddress") or data.get("userID") or "")
        elif backend == BACKEND_CODEX:
            for name in ("auth.json", "config.json"):
                cfg = home / ".codex" / name
                if cfg.exists():
                    data = json.loads(cfg.read_text())
                    return str(
                        data.get("email")
                        or data.get("account_id")
                        or (data.get("tokens") or {}).get("account_id")
                        or ""
                    )
        elif backend == BACKEND_GEMINI:
            cfg = home / ".gemini" / "google_accounts.json"
            if cfg.exists():
                data = json.loads(cfg.read_text())
                active = data.get("active") or ""
                return str(active)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        log.debug("account detect failed for %s: %s", backend, e)
    return ""


def collect_local_usage(
    tenant: str,
    host: str = "",
    hours: int = 168,
    accounts: Optional[dict[str, str]] = None,
) -> list[TeamUsageRecord]:
    """
    Read the running user's local CLI stats and produce snapshot records.

    Must run *as the developer* whose usage is being collected (so the stats
    readers resolve the correct ``$HOME``). ``accounts`` optionally overrides
    the auto-detected provider account label per backend.
    """
    from .claude_stats import get_claude_code_stats
    from .codex_stats import get_codex_stats
    from .gemini_stats import get_gemini_stats

    accounts = accounts or {}
    today = datetime.now().strftime("%Y-%m-%d")
    readers = [
        (BACKEND_CLAUDE, get_claude_code_stats),
        (BACKEND_CODEX, get_codex_stats),
        (BACKEND_GEMINI, get_gemini_stats),
    ]
    out: list[TeamUsageRecord] = []
    for backend, reader in readers:
        try:
            stats = reader(hours=hours)
        except Exception as e:  # noqa: BLE001 — a broken reader must not abort the rest
            log.warning("stats reader %s failed for %s: %s", backend, tenant, e)
            continue
        account = accounts.get(backend) or detect_account(backend)
        out.extend(
            _records_from_stats(
                stats,
                tenant=tenant,
                backend=backend,
                account=account,
                host=host,
                today=today,
            )
        )
    return out
