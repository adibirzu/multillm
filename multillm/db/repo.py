# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

r"""Repository Protocols for tenant-aware data access.

Phase 2a introduces the SHAPE only — `tenant_id: str` is required positional
as the first non-self argument on every method (D-2a-03), with no default.
Phase 2b will wire concrete implementations in tracking.py / memory.py /
sessions.py and replace the literal "default" arguments at the call sites
with real tenant context.

Grep invariant for Phase 2b setup:

    git grep -nE 'def \w+\(self, tenant_id:' multillm/db/

Must report one match per Protocol method (12 total in this revision).
The dict[str, Any] placeholders are intentional — Phase 2b replaces them
with TypedDicts or dataclasses when concrete implementations land. For
Phase 2a we only need the shape to exist and be grep-friendly.
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SessionRepo(Protocol):
    """Session lifecycle, scoped per tenant."""

    def list_sessions(self, tenant_id: str, *, limit: int = 50) -> list[dict[str, Any]]: ...

    def get_session(self, tenant_id: str, session_id: str) -> dict[str, Any] | None: ...

    def create_session(self, tenant_id: str, session: dict[str, Any]) -> dict[str, Any]: ...

    def append_request(self, tenant_id: str, session_id: str, request: dict[str, Any]) -> None: ...


@runtime_checkable
class TrackingRepo(Protocol):
    """Usage tracking and dashboard aggregation, scoped per tenant."""

    def record_usage(self, tenant_id: str, usage: dict[str, Any]) -> None: ...

    def get_dashboard(self, tenant_id: str, *, hours: int = 168, project: str | None = None) -> dict[str, Any]: ...

    def get_summary(self, tenant_id: str, *, hours: int = 24) -> dict[str, Any]: ...


@runtime_checkable
class MemoryRepo(Protocol):
    """Cross-LLM memory store, scoped per tenant."""

    def list_memories(self, tenant_id: str, *, limit: int = 20) -> list[dict[str, Any]]: ...

    def search_memories(self, tenant_id: str, query: str, *, limit: int = 10) -> list[dict[str, Any]]: ...

    def get_memory(self, tenant_id: str, memory_id: str) -> dict[str, Any] | None: ...

    def store_memory(self, tenant_id: str, memory: dict[str, Any]) -> dict[str, Any]: ...

    def delete_memory(self, tenant_id: str, memory_id: str) -> bool: ...
