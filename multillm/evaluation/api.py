"""Tenant-scoped HTTP API for evaluation suites, runs, results, and exports."""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Header, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import ValidationError

from multillm.config import DATA_DIR

from .artifacts import ArtifactCipher
from .benchmarks import BENCHMARKS
from .contracts import EvaluationRunRequest
from .reports import render_management_html
from .runner import EvaluationRunner, evaluate_release_gate
from .store import EvaluationStore
from .suites import load_finops_suite


router = APIRouter(prefix="/api/evaluations", tags=["evaluations"])


def _ok(data: Any, *, meta: dict[str, Any] | None = None, status_code: int = 200):
    payload = {"success": True, "data": data, "error": None, "meta": meta or {}}
    return JSONResponse(payload, status_code=status_code)


def _error(message: str, *, status_code: int):
    return JSONResponse(
        {"success": False, "data": None, "error": {"message": message}, "meta": {}},
        status_code=status_code,
    )


def _tenant(value: str | None) -> str:
    tenant = (value or "default").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}", tenant):
        raise ValueError("invalid tenant identifier")
    return tenant


def _tenant_or_response(value: str | None) -> tuple[str | None, JSONResponse | None]:
    try:
        return _tenant(value), None
    except ValueError as exc:
        return None, _error(str(exc), status_code=422)


@lru_cache(maxsize=1)
def get_evaluation_store() -> EvaluationStore:
    encoded_key = os.getenv("MULTILLM_EVAL_ARTIFACT_KEY", "").strip()
    if not encoded_key:
        raise RuntimeError(
            "MULTILLM_EVAL_ARTIFACT_KEY is required to retain encrypted evaluation artifacts"
        )
    cipher = ArtifactCipher.from_base64(encoded_key)
    data_dir = Path(os.getenv("MULTILLM_DATA_DIR", str(DATA_DIR)))
    return EvaluationStore(data_dir / "multillm.db", artifact_cipher=cipher)


def _ensure_owned_suite(store: EvaluationStore, tenant_id: str) -> None:
    if store.get_suite(tenant_id, "finops-v1") is None:
        store.upsert_suite(
            tenant_id,
            suite_id="finops-v1",
            name="FinOps NLP and anomaly evaluation",
            version="1.0.0",
            source="MultiLLM owned suite",
            license_id="Apache-2.0",
            cases=load_finops_suite(),
        )


@router.get("/suites")
def list_suites_api(
    x_multillm_tenant: str | None = Header(None),
    store: EvaluationStore = Depends(get_evaluation_store),
):
    tenant_id, error = _tenant_or_response(x_multillm_tenant)
    if error:
        return error
    _ensure_owned_suite(store, tenant_id)
    return _ok(store.list_suites(tenant_id))


@router.get("/benchmarks")
def benchmark_manifest_api():
    """Return protocol/license metadata without fetching restricted datasets."""
    return _ok([definition.public_dict() for definition in BENCHMARKS.values()])


@router.get("/runs")
def list_runs_api(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    x_multillm_tenant: str | None = Header(None),
    store: EvaluationStore = Depends(get_evaluation_store),
):
    tenant_id, error = _tenant_or_response(x_multillm_tenant)
    if error:
        return error
    runs = store.list_runs(tenant_id, limit=limit, offset=offset)
    return _ok(runs, meta={"limit": limit, "offset": offset, "count": len(runs)})


@router.post("/runs")
def create_run_api(
    payload: dict[str, Any],
    x_multillm_tenant: str | None = Header(None),
    store: EvaluationStore = Depends(get_evaluation_store),
):
    tenant_id, error = _tenant_or_response(x_multillm_tenant)
    if error:
        return error
    try:
        request = EvaluationRunRequest.model_validate(payload)
    except ValidationError as exc:
        return _error(str(exc), status_code=422)
    _ensure_owned_suite(store, tenant_id)
    try:
        run_id = store.create_run(tenant_id, request)
    except ValueError as exc:
        return _error(str(exc), status_code=422)
    return _ok({"id": run_id, "status": "queued"}, status_code=202)


@router.get("/runs/{run_id}")
def get_run_api(
    run_id: str,
    x_multillm_tenant: str | None = Header(None),
    store: EvaluationStore = Depends(get_evaluation_store),
):
    tenant_id, error = _tenant_or_response(x_multillm_tenant)
    if error:
        return error
    run = store.get_run(tenant_id, run_id, include_content=False)
    if run is None:
        return _error("evaluation run not found", status_code=404)
    return _ok(run)


@router.get("/runs/{run_id}/results")
def get_results_api(
    run_id: str,
    include_content: bool = Query(False),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1_000),
    x_multillm_tenant: str | None = Header(None),
    store: EvaluationStore = Depends(get_evaluation_store),
):
    tenant_id, error = _tenant_or_response(x_multillm_tenant)
    if error:
        return error
    run = store.get_run(tenant_id, run_id, include_content=include_content)
    if run is None:
        return _error("evaluation run not found", status_code=404)
    outputs = run["outputs"][offset : offset + limit]
    return _ok(
        outputs,
        meta={"offset": offset, "limit": limit, "count": len(outputs), "total": len(run["outputs"])},
    )


@router.get("/runs/{run_id}/comparisons")
def get_comparisons_api(
    run_id: str,
    x_multillm_tenant: str | None = Header(None),
    store: EvaluationStore = Depends(get_evaluation_store),
):
    tenant_id, error = _tenant_or_response(x_multillm_tenant)
    if error:
        return error
    if store.get_run(tenant_id, run_id, include_content=False) is None:
        return _error("evaluation run not found", status_code=404)
    comparisons = store.list_comparisons(tenant_id, run_id)
    return _ok(comparisons, meta={"count": len(comparisons)})


@router.get("/reviews/queue")
def review_queue_api(
    run_id: str | None = Query(None, pattern=r"^eval_[a-f0-9]{20}$"),
    limit: int = Query(100, ge=1, le=500),
    x_multillm_tenant: str | None = Header(None),
    store: EvaluationStore = Depends(get_evaluation_store),
):
    tenant_id, error = _tenant_or_response(x_multillm_tenant)
    if error:
        return error
    queue = store.review_queue(tenant_id, run_id=run_id, limit=limit)
    return _ok(queue, meta={"count": len(queue), "blinded": True})


@router.post("/reviews/{comparison_id}")
def submit_review_api(
    comparison_id: str,
    payload: dict[str, Any],
    x_multillm_tenant: str | None = Header(None),
    x_multillm_reviewer: str | None = Header(None),
    store: EvaluationStore = Depends(get_evaluation_store),
):
    tenant_id, error = _tenant_or_response(x_multillm_tenant)
    if error:
        return error
    reviewer = (x_multillm_reviewer or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:@-]{0,119}", reviewer):
        return _error("valid X-MultiLLM-Reviewer header is required", status_code=422)
    decision = str(payload.get("decision") or "").strip().lower()
    rationale = str(payload.get("rationale") or "").strip()
    if decision not in {"response_a", "response_b", "tie"}:
        return _error("decision must be response_a, response_b, or tie", status_code=422)
    if not rationale or len(rationale) > 8_000:
        return _error("rationale is required and limited to 8000 characters", status_code=422)
    try:
        resolved_decision = store.resolve_blind_review_decision(
            comparison_id, decision
        )
        created = store.add_review(
            tenant_id,
            comparison_id=comparison_id,
            reviewer_id=reviewer,
            decision=resolved_decision,
            rationale=rationale,
        )
    except ValueError as exc:
        return _error(str(exc), status_code=422)
    if not created:
        return _error("comparison not found", status_code=404)
    run_id = store.comparison_run_id(tenant_id, comparison_id)
    run = (
        store.get_run(tenant_id, run_id, include_content=False)
        if run_id is not None
        else None
    )
    if run is not None and run["status"] in {"completed", "incomplete", "failed"}:
        comparisons = store.list_comparisons(tenant_id, run_id)
        pairwise = EvaluationRunner._pairwise_summary(comparisons)
        profile = EvaluationRunRequest.model_validate(run["request"]).profile
        summary = {
            **run["summary"],
            "pairwise": pairwise,
            "releaseGate": evaluate_release_gate(
                profile,
                pairwise,
                pending_reviews=any(
                    comparison["needsHumanReview"] for comparison in comparisons
                ),
            ),
        }
        store.update_run_summary(tenant_id, run_id, summary)
    return _ok({"comparisonId": comparison_id, "blindDecision": decision})


@router.post("/runs/{run_id}/cancel")
def cancel_run_api(
    run_id: str,
    x_multillm_tenant: str | None = Header(None),
    store: EvaluationStore = Depends(get_evaluation_store),
):
    tenant_id, error = _tenant_or_response(x_multillm_tenant)
    if error:
        return error
    if not store.cancel_run(tenant_id, run_id):
        return _error("evaluation run not found or already terminal", status_code=404)
    return _ok({"id": run_id, "status": "cancelled"})


@router.get("/runs/{run_id}/export")
def export_run_api(
    run_id: str,
    format: str = Query("json", pattern="^(json|csv|html)$"),
    x_multillm_tenant: str | None = Header(None),
    store: EvaluationStore = Depends(get_evaluation_store),
):
    tenant_id, error = _tenant_or_response(x_multillm_tenant)
    if error:
        return error
    try:
        if format == "csv":
            return Response(
                store.export_run_csv(tenant_id, run_id),
                media_type="text/csv",
                headers={"Content-Disposition": f'attachment; filename="{run_id}.csv"'},
            )
        bundle = store.export_run(tenant_id, run_id)
    except KeyError:
        return _error("evaluation run not found", status_code=404)
    if format == "html":
        return HTMLResponse(
            render_management_html(bundle),
            headers={"Content-Disposition": f'attachment; filename="{run_id}.html"'},
        )
    return _ok(bundle)
