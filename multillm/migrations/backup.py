# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Pre-migration SQLite backup helper.

``create_backup`` snapshots the configured SQLite database to
``$MULTILLM_HOME/backups/pre-<rev>-<unix-ms>.db`` before any DDL runs. The
caller (``migrate_up``) is required to invoke this BEFORE Alembic mutates
the live DB — see threat ``T-01-03-01``.

The backup directory is created on demand with mode 0o700; individual files
inherit the process umask. This is sufficient for the single-user threat
model in Phase 1 (T-01-03-02). Phase 2b will revisit when tenant data lives
in the backed-up DB.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

__all__ = ["BACKUP_DIR", "create_backup", "resolve_home"]

_DEFAULT_HOME_NAME = ".multillm"


def resolve_home() -> Path:
    """Return the active ``MULTILLM_HOME`` directory.

    Resolution order:
    1. ``$MULTILLM_HOME`` env var (matches ``multillm.config`` semantics)
    2. ``~/.multillm``

    Resolved on every call so test monkeypatching of the env var is honored
    without module-reload gymnastics in callers.
    """
    override = os.getenv("MULTILLM_HOME", "").strip()
    if override:
        return Path(override)
    return Path.home() / _DEFAULT_HOME_NAME


def _backup_dir() -> Path:
    return resolve_home() / "backups"


class _BackupDirProxy:
    """Late-bound proxy so ``BACKUP_DIR`` reflects env-var changes at test time.

    A bare module-level ``BACKUP_DIR = resolve_home() / 'backups'`` would freeze
    the path at import time and break monkeypatched MULTILLM_HOME tests unless
    the consumer reloads the module. The proxy resolves on every attribute read,
    so ``BACKUP_DIR.exists()``, ``BACKUP_DIR == path``, ``path.parent == BACKUP_DIR``,
    etc. all work transparently.
    """

    def _current(self) -> Path:
        return _backup_dir()

    def __getattr__(self, name: str):
        return getattr(self._current(), name)

    def __fspath__(self) -> str:
        return os.fspath(self._current())

    def __eq__(self, other: object) -> bool:
        return self._current() == other

    def __hash__(self) -> int:
        return hash(self._current())

    def __repr__(self) -> str:
        return f"<BACKUP_DIR -> {self._current()!s}>"

    def __truediv__(self, other: str) -> Path:
        return self._current() / other


BACKUP_DIR: Path = _BackupDirProxy()  # type: ignore[assignment]


def create_backup(db_path: Path, target_rev: str) -> Path:
    """Snapshot ``db_path`` into ``BACKUP_DIR`` before a migration runs.

    The returned path follows ``pre-<target_rev>-<unix-ms>.db``. Consecutive
    calls produce distinct filenames (millisecond resolution).

    Raises
    ------
    FileNotFoundError
        When ``db_path`` does not exist. Migrations against a missing DB
        must abort — never silently skip the safety backup.
    """
    src = Path(db_path)
    if not src.exists():
        raise FileNotFoundError(
            f"Cannot back up {src}: source database does not exist."
        )

    backup_dir = _backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    # mkdir's mode is only applied on creation; if the dir pre-existed with
    # a looser mode we tighten it here for the single-user threat model.
    try:
        os.chmod(backup_dir, 0o700)
    except OSError:
        # Filesystem may not support chmod (e.g. Windows over SMB). The
        # mkdir mode already applies in the common case.
        pass

    timestamp_ms = int(time.time() * 1000)
    dest = backup_dir / f"pre-{target_rev}-{timestamp_ms}.db"
    # Guard against the (rare) clock-jitter case where two calls land on the
    # same millisecond. shutil.copy2 would overwrite — we want distinct files.
    while dest.exists():
        timestamp_ms += 1
        dest = backup_dir / f"pre-{target_rev}-{timestamp_ms}.db"

    shutil.copy2(src, dest)
    return dest
