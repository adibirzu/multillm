# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""Quota-aware failover for the MultiLLM gateway.

When a backend runs out of quota / credits (HTTP 429 or 402, or an
``insufficient_quota`` style payload) the gateway should not surface the error —
it should transparently continue on the next provider in the fallback chain so
the user can keep working. This is distinct from the existing connection-error
fallback (which targets a local model); quota failover walks the whole chain,
cloud or local, trying each provider that hasn't already failed.

Pure helpers here so the policy is unit-tested without a live backend.
"""

from __future__ import annotations

from typing import Optional

try:  # httpx is always present in the gateway, but keep the import defensive
    import httpx
except Exception:  # pragma: no cover
    httpx = None  # type: ignore

# Status codes that mean "this provider can't serve you right now for billing /
# capacity reasons" — the canonical signal to move to the next provider.
QUOTA_STATUS_CODES = {429, 402}

# Substrings (lowercased) that signal quota/credit exhaustion in an error body
# even when the status code is generic (some providers return 400/403 with these).
QUOTA_MARKERS = (
    "insufficient_quota",
    "insufficient quota",
    "out of credits",
    "exceeded your current quota",
    "quota exceeded",
    "rate limit",
    "rate_limit",
    "too many requests",
    "billing",
    "payment required",
    "credit balance",
)


def _status_and_text(error: object) -> tuple[Optional[int], str]:
    """Best-effort (status_code, text) extraction from heterogeneous errors."""
    status: Optional[int] = None
    text = ""
    # FastAPI HTTPException
    status_attr = getattr(error, "status_code", None)
    if isinstance(status_attr, int):
        status = status_attr
    detail = getattr(error, "detail", None)
    if detail:
        text += str(detail)
    # httpx.HTTPStatusError carries a response
    response = getattr(error, "response", None)
    if response is not None:
        resp_status = getattr(response, "status_code", None)
        if isinstance(resp_status, int):
            status = resp_status
        try:
            text += " " + (response.text or "")
        except Exception:
            pass
    text += " " + str(error)
    return status, text.lower()


def is_quota_error(error: object) -> bool:
    """True when ``error`` indicates quota/credit/rate-limit exhaustion.

    Matches by status code (429/402) or by quota marker substrings in the
    error text, so it catches providers that signal exhaustion with a body
    rather than a clean status code.
    """
    status, text = _status_and_text(error)
    if status in QUOTA_STATUS_CODES:
        return True
    return any(marker in text for marker in QUOTA_MARKERS)


def build_failover_candidates(
    *,
    routes: dict,
    chain: list[str],
    failed_backend: str,
    exclude_aliases: Optional[set[str]] = None,
) -> list[tuple[str, dict]]:
    """Ordered, de-duplicated failover targets from the configured chain.

    - Skips the backend that just failed and any explicitly excluded aliases.
    - Keeps at most one candidate per backend (no point retrying the same
      provider that just rejected us on quota within a single request).
    - Resolves each alias to its route; unknown aliases are dropped.
    """
    exclude_aliases = exclude_aliases or set()
    out: list[tuple[str, dict]] = []
    seen_backends = {failed_backend}
    for alias in chain:
        if alias in exclude_aliases:
            continue
        route = routes.get(alias)
        if not route:
            continue
        backend = route.get("backend", "")
        if backend in seen_backends:
            continue
        seen_backends.add(backend)
        out.append((alias, route))
    return out
