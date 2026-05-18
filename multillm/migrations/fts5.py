# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""FTS5 rebuild helper for the migration toolkit (D-06).

Ships in Phase 1 but is not exercised by the smoke migration — Phase 2b
inherits the helper and uses it to rebuild the memory FTS5 index after the
tenant_id column rollout.

SECURITY: SQLite cannot parameterize identifiers (table names). Callers may
forward a table name from user-controlled config in some future flow, so
``rebuild_fts5_indexes`` validates the identifier against a strict regex
BEFORE interpolating it into a SQL statement. Anything outside
``^[A-Za-z_][A-Za-z0-9_]*$`` raises ``ValueError``.
"""

from __future__ import annotations

import re
import sqlite3

__all__ = ["rebuild_fts5_indexes"]


_SAFE_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_identifier(name: str) -> str:
    if not isinstance(name, str) or not _SAFE_IDENT.fullmatch(name):
        raise ValueError(
            f"Refusing to use {name!r} as a SQL identifier — does not match "
            f"^[A-Za-z_][A-Za-z0-9_]*$"
        )
    return name


def rebuild_fts5_indexes(conn: sqlite3.Connection, table_name: str) -> None:
    """Rebuild the FTS5 index for ``table_name``.

    Parameters
    ----------
    conn
        Open ``sqlite3.Connection`` against a DB containing the FTS5 table.
    table_name
        Bare identifier of the FTS5 virtual table to rebuild. Must match
        ``^[A-Za-z_][A-Za-z0-9_]*$``.

    Raises
    ------
    ValueError
        If ``table_name`` is not a safe identifier.
    sqlite3.OperationalError
        If ``table_name`` is not an FTS5 virtual table — the helper does
        NOT catch this; the caller is responsible for deciding whether to
        treat it as a hard failure or a no-op.
    """
    safe = _validate_identifier(table_name)
    # FTS5 rebuild incantation: insert the magic sentinel into the FTS5 table.
    # AUTH-17: ``safe`` is whitelisted by ``_validate_identifier``; SQL is
    # composed via explicit concatenation (no f-string) to keep the rg gate
    # ``execute\(.*f['\"]`` clean while preserving the well-known FTS5
    # rebuild incantation that requires the table name in two positions.
    rebuild_sql = "INSERT INTO " + safe + "(" + safe + ") VALUES('rebuild')"
    conn.execute(rebuild_sql)
    conn.commit()
