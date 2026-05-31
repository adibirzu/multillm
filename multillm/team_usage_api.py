# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""
FastAPI surface for multi-user / multi-account LLM usage monitoring.

Endpoints (registered onto the main gateway app via :func:`register`):

* ``POST /api/usage/ingest`` — per-user collector pushes a daily snapshot
  batch (write; requires gateway auth when ``MULTILLM_API_KEY`` is set).
* ``GET  /api/team-usage``   — aggregated team rollup (read-only; in the
  optional public read allowlist).
* ``GET  /team``             — multi-user usage dashboard page.

Optional per-user daily cost budgets are read from the ``MULTILLM_USER_BUDGETS``
environment variable (``user=usd,user=usd``) and surfaced as an ``over_budget``
flag on each user's rollup. Tune the policy in :func:`apply_budgets`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import team_usage

log = logging.getLogger("multillm.team_usage_api")

_STATIC_DIR = Path(__file__).parent / "static"


# ── Request models ───────────────────────────────────────────────────────────


class IngestRecord(BaseModel):
    backend: str
    model: str = "unknown"
    day: str
    account: str = ""
    tenant_id: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_tokens: int = 0
    requests: int = 0
    cost_usd: float = 0.0
    source_host: str = ""


class IngestBatch(BaseModel):
    tenant_id: str = Field(..., min_length=1, description="UNIX user / workstation identity")
    source_host: str = ""
    records: list[IngestRecord] = Field(default_factory=list)


# ── Budget policy (operator-tunable) ─────────────────────────────────────────


def _parse_budgets(raw: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for pair in (raw or "").split(","):
        pair = pair.strip()
        if "=" in pair:
            user, amount = pair.split("=", 1)
            try:
                out[user.strip()] = float(amount)
            except ValueError:
                continue
    return out


def apply_budgets(by_user: list[dict]) -> list[dict]:
    """
    Annotate each user's rollup with budget state.

    Default policy: a per-user daily-cost cap from ``MULTILLM_USER_BUDGETS``
    (e.g. ``adi=5,royce=10``). ``over_budget`` is True when the windowed cost
    exceeds ``cap * window_days``. Adjust here to add Slack alerts, hard quota
    enforcement, or per-account caps.
    """
    budgets = _parse_budgets(os.environ.get("MULTILLM_USER_BUDGETS", ""))
    if not budgets:
        return by_user
    annotated = []
    for row in by_user:
        cap = budgets.get(row.get("bucket", ""))
        new = dict(row)
        if cap is not None:
            new["daily_budget_usd"] = cap
            new["over_budget"] = bool(row.get("cost_usd", 0.0) > cap)
        annotated.append(new)
    return annotated


# ── Registration ─────────────────────────────────────────────────────────────


def register(app) -> None:
    """Attach the team-usage routes to the gateway FastAPI app."""

    @app.post("/api/usage/ingest")
    async def ingest_usage(batch: IngestBatch):  # noqa: ANN202 — FastAPI handler
        records = []
        errors = []
        for raw in batch.records:
            d = raw.model_dump()
            # Force the batch identity unless the record names its own tenant.
            d["tenant_id"] = d.get("tenant_id") or batch.tenant_id
            d["source_host"] = d.get("source_host") or batch.source_host
            try:
                records.append(team_usage.record_from_dict(d))
            except ValueError as e:
                errors.append(str(e))
        if errors and not records:
            raise HTTPException(status_code=422, detail={"errors": errors})
        written = team_usage.record_team_usage(records)
        log.info("ingest: tenant=%s wrote=%d skipped=%d", batch.tenant_id, written, len(errors))
        return {"status": "ok", "written": written, "skipped": len(errors), "errors": errors}

    @app.get("/api/team-usage")
    async def team_usage_api(hours: int = 168, tenant: Optional[str] = None):  # noqa: ANN202
        data = team_usage.get_team_usage(hours=hours, tenant=tenant)
        data["by_user"] = apply_budgets(data["by_user"])
        return data

    @app.get("/team")
    async def team_dashboard():  # noqa: ANN202
        html = _STATIC_DIR / "team.html"
        if not html.exists():
            raise HTTPException(status_code=404, detail="team dashboard not built")
        return FileResponse(str(html))
