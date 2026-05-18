# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Repository Protocols for tenant-aware data access.

Phase 2a introduces the SHAPE only — `tenant_id: str` is required positional
as the first non-self argument on every method, with no default. Phase 2b
will wire concrete implementations in tracking.py / memory.py / sessions.py
and replace the literal `"default"` arguments with real tenant context.
"""

from .memory import MemoryRepoSqlite
from .repo import MemoryRepo, SessionRepo, TrackingRepo
from .sessions import SessionRepoSqlite
from .tracking import TrackingRepoSqlite

__all__ = [
    "MemoryRepo",
    "MemoryRepoSqlite",
    "SessionRepo",
    "SessionRepoSqlite",
    "TrackingRepo",
    "TrackingRepoSqlite",
]
