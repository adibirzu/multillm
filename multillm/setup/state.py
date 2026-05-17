# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Setup state machine for the first-run wizard (Plan 01-07 Task 1).

State diagram::

    PENDING
       │ advance('admin', ...)
       ▼
    ADMIN_CREATED ─── advance('backends'|'local_probe'|'observability', ...) ──┐
       │                                                                       │
       └───────────────────── complete() ──────────────────────────────────────┘
                                       │
                                       ▼
                                    COMPLETE  ──── reset_setup() ──► PENDING

All SQL uses parameter substitution (``?``) — no f-string SQL — to
pre-empt Phase 2b's CI grep gate.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from enum import Enum
from typing import Any

__all__ = [
    "SetupState",
    "advance",
    "complete",
    "get_state",
    "is_complete",
    "reset_setup",
]


class SetupState(str, Enum):
    """Coarse-grained wizard state derived from DB rows."""

    PENDING = "pending"
    ADMIN_CREATED = "admin_created"
    BACKENDS_CONFIGURED = "backends_configured"
    LOCAL_PROBED = "local_probed"
    OBSERVABILITY_SET = "observability_set"
    COMPLETE = "complete"


_VALID_PANES = ("admin", "backends", "local_probe", "observability")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def is_complete(conn: sqlite3.Connection) -> bool:
    """Return ``True`` iff ``system.setup_complete == '1'``."""
    row = conn.execute(
        "SELECT value FROM system WHERE key=?", ("setup_complete",)
    ).fetchone()
    if row is None:
        return False
    value = row[0] if not isinstance(row, sqlite3.Row) else row["value"]
    return value == "1"


def get_state(conn: sqlite3.Connection) -> SetupState:
    """Derive the current ``SetupState`` from the DB.

    Order of precedence:

    1. ``system.setup_complete == '1'`` → :attr:`SetupState.COMPLETE`
    2. observability pane completed → :attr:`SetupState.OBSERVABILITY_SET`
    3. local_probe pane completed   → :attr:`SetupState.LOCAL_PROBED`
    4. backends pane completed      → :attr:`SetupState.BACKENDS_CONFIGURED`
    5. admin pane completed         → :attr:`SetupState.ADMIN_CREATED`
    6. otherwise                    → :attr:`SetupState.PENDING`
    """
    if is_complete(conn):
        return SetupState.COMPLETE

    panes = {
        r[0] if not isinstance(r, sqlite3.Row) else r["pane"]
        for r in conn.execute("SELECT pane FROM setup_state").fetchall()
    }

    if "observability" in panes:
        return SetupState.OBSERVABILITY_SET
    if "local_probe" in panes:
        return SetupState.LOCAL_PROBED
    if "backends" in panes:
        return SetupState.BACKENDS_CONFIGURED
    if "admin" in panes:
        return SetupState.ADMIN_CREATED
    return SetupState.PENDING


def _upsert_pane(
    conn: sqlite3.Connection, pane: str, payload: dict[str, Any]
) -> None:
    """Insert or update a row in ``setup_state``."""
    conn.execute(
        """
        INSERT INTO setup_state (pane, payload_json, completed_at)
        VALUES (?, ?, ?)
        ON CONFLICT(pane) DO UPDATE SET
            payload_json = excluded.payload_json,
            completed_at = excluded.completed_at
        """,
        (pane, json.dumps(payload, sort_keys=True), _now()),
    )


def _upsert_admin_user(
    conn: sqlite3.Connection, email: str, password_hash: str
) -> None:
    """Upsert the single admin user (id=1 in Phase 1; multi-user in 2b)."""
    conn.execute(
        """
        INSERT INTO admin_users (id, email, password_hash, created_at)
        VALUES (1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            email = excluded.email,
            password_hash = excluded.password_hash
        """,
        (email, password_hash, _now()),
    )


def advance(
    conn: sqlite3.Connection, pane: str, payload: dict[str, Any]
) -> None:
    """Persist a wizard pane's payload and (for ``admin``) the admin user.

    Args:
        conn:    Open SQLite connection (caller owns the lifecycle).
        pane:    One of ``admin``, ``backends``, ``local_probe``,
                 ``observability``.
        payload: JSON-serialisable dict.

    Raises:
        ValueError: if ``pane`` is not recognised.
    """
    if pane not in _VALID_PANES:
        raise ValueError(
            f"unknown pane {pane!r}; expected one of {_VALID_PANES}"
        )

    _upsert_pane(conn, pane, payload)

    if pane == "admin":
        email = payload.get("email")
        password_hash = payload.get("password_hash")
        if email and password_hash:
            _upsert_admin_user(conn, email, password_hash)

    conn.commit()


def _set_system(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO system (key, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = excluded.updated_at
        """,
        (key, value, _now()),
    )


def complete(conn: sqlite3.Connection) -> None:
    """Mark setup complete and wipe ephemeral ``setup_state`` rows.

    The pane payloads (which may contain backend API keys, per pane 2)
    are deleted; long-term storage of those keys is the caller's
    responsibility (write them to the gateway config before calling
    complete()). This mitigates T-01-07-03.
    """
    _set_system(conn, "setup_complete", "1")
    conn.execute("DELETE FROM setup_state")
    conn.commit()


def reset_setup(conn: sqlite3.Connection) -> None:
    """Flip setup back to PENDING and clear admin state.

    Used by ``multillm reset --confirm`` to re-enable the wizard.
    """
    _set_system(conn, "setup_complete", "0")
    conn.execute("DELETE FROM setup_state")
    conn.execute("DELETE FROM admin_users")
    conn.commit()
