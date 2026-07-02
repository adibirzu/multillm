from __future__ import annotations

import base64

from fastapi import FastAPI
from fastapi.testclient import TestClient

from multillm.evaluation.api import get_evaluation_store, router
from multillm.evaluation.artifacts import ArtifactCipher
from multillm.evaluation.contracts import EvaluationRunRequest
from multillm.evaluation.contracts import PairwiseDecision
from multillm.evaluation.store import EvaluationStore
from multillm.evaluation.suites import load_finops_suite


def _app(tmp_path):
    store = EvaluationStore(
        tmp_path / "eval.db", artifact_cipher=ArtifactCipher(bytes(range(32)))
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_evaluation_store] = lambda: store
    return TestClient(app), store


def test_suite_and_run_apis_use_consistent_envelope_and_tenant_scope(tmp_path):
    client, _store = _app(tmp_path)

    suites = client.get(
        "/api/evaluations/suites", headers={"X-MultiLLM-Tenant": "tenant-a"}
    )
    assert suites.status_code == 200
    assert suites.json()["success"] is True
    assert suites.json()["data"][0]["id"] == "finops-v1"
    assert suites.json()["data"][0]["caseCount"] == 40

    created = client.post(
        "/api/evaluations/runs",
        headers={"X-MultiLLM-Tenant": "tenant-a"},
        json={
            "suite_id": "finops-v1",
            "candidate_scope": "explicit",
            "candidates": ["codex/gpt-5-5", "claude-cli/sonnet"],
            "moa_variants": ["moa/quality"],
        },
    )
    assert created.status_code == 202
    run_id = created.json()["data"]["id"]

    detail = client.get(
        f"/api/evaluations/runs/{run_id}",
        headers={"X-MultiLLM-Tenant": "tenant-a"},
    )
    assert detail.status_code == 200
    assert detail.json()["data"]["status"] == "queued"
    assert detail.json()["data"]["outputs"] == []

    hidden = client.get(
        f"/api/evaluations/runs/{run_id}",
        headers={"X-MultiLLM-Tenant": "tenant-b"},
    )
    assert hidden.status_code == 404
    assert hidden.json()["success"] is False


def test_run_api_rejects_unapproved_live_execution_and_invalid_tenant(tmp_path):
    client, _store = _app(tmp_path)

    response = client.post(
        "/api/evaluations/runs",
        headers={"X-MultiLLM-Tenant": "tenant-a"},
        json={
            "suite_id": "finops-v1",
            "candidate_scope": "core",
            "execution_mode": "live_host",
            "live_authorized": False,
        },
    )
    assert response.status_code == 422

    invalid = client.get(
        "/api/evaluations/suites", headers={"X-MultiLLM-Tenant": "../tenant"}
    )
    assert invalid.status_code == 422


def test_cancel_list_results_and_audit_exports(tmp_path):
    client, store = _app(tmp_path)
    store.upsert_suite(
        "tenant-a",
        suite_id="finops-v1",
        name="FinOps v1",
        version="1.0.0",
        source="owned",
        license_id="Apache-2.0",
        cases=load_finops_suite(),
    )
    run_id = store.create_run(
        "tenant-a",
        EvaluationRunRequest(
            suite_id="finops-v1",
            candidate_scope="explicit",
            candidates=("codex/gpt-5-5",),
            moa_variants=(),
        ),
    )
    store.record_output(
        "tenant-a",
        run_id,
        case_id="focus-intent-cost-trend",
        target="codex/gpt-5-5",
        attempt=1,
        output_text="<script>alert(1)</script> =unsafe",
        usage={"input_tokens": 1, "output_tokens": 2},
        latency={"total_ms": 3},
        cost={"normalized_usd": 0.001},
        status="succeeded",
    )

    results = client.get(
        f"/api/evaluations/runs/{run_id}/results",
        headers={"X-MultiLLM-Tenant": "tenant-a"},
    )
    assert results.status_code == 200
    assert results.json()["data"][0]["outputText"] is None

    privileged = client.get(
        f"/api/evaluations/runs/{run_id}/results?include_content=true",
        headers={"X-MultiLLM-Tenant": "tenant-a"},
    )
    assert privileged.json()["data"][0]["outputText"].startswith("<script>")

    csv_export = client.get(
        f"/api/evaluations/runs/{run_id}/export?format=csv",
        headers={"X-MultiLLM-Tenant": "tenant-a"},
    )
    assert csv_export.status_code == 200
    assert "attachment" in csv_export.headers["content-disposition"]

    html_export = client.get(
        f"/api/evaluations/runs/{run_id}/export?format=html",
        headers={"X-MultiLLM-Tenant": "tenant-a"},
    )
    assert html_export.status_code == 200
    assert "<script>alert(1)</script>" not in html_export.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_export.text

    listed = client.get(
        "/api/evaluations/runs?limit=10",
        headers={"X-MultiLLM-Tenant": "tenant-a"},
    )
    assert listed.json()["meta"]["count"] == 1

    cancelled = client.post(
        f"/api/evaluations/runs/{run_id}/cancel",
        headers={"X-MultiLLM-Tenant": "tenant-a"},
    )
    assert cancelled.status_code == 200


def test_default_store_requires_a_valid_encryption_key(monkeypatch, tmp_path):
    from multillm.evaluation import api

    api.get_evaluation_store.cache_clear()
    monkeypatch.setenv("MULTILLM_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MULTILLM_EVAL_ARTIFACT_KEY", raising=False)
    try:
        response = api.get_evaluation_store()
    except RuntimeError as exc:
        assert "MULTILLM_EVAL_ARTIFACT_KEY" in str(exc)
    else:
        raise AssertionError(response)

    api.get_evaluation_store.cache_clear()
    monkeypatch.setenv(
        "MULTILLM_EVAL_ARTIFACT_KEY",
        base64.urlsafe_b64encode(bytes(range(32))).decode("ascii"),
    )
    assert isinstance(api.get_evaluation_store(), EvaluationStore)
    api.get_evaluation_store.cache_clear()


def test_gateway_mounts_the_evaluation_router(tmp_path):
    from multillm import gateway

    store = EvaluationStore(
        tmp_path / "gateway-eval.db",
        artifact_cipher=ArtifactCipher(bytes(range(32))),
    )
    gateway.app.dependency_overrides[get_evaluation_store] = lambda: store
    try:
        client = TestClient(gateway.app)
        response = client.get("/api/evaluations/suites")
        assert response.status_code == 200
        assert response.json()["data"][0]["id"] == "finops-v1"
    finally:
        gateway.app.dependency_overrides.pop(get_evaluation_store, None)


def test_comparison_and_blinded_human_review_apis(tmp_path):
    client, store = _app(tmp_path)
    store.upsert_suite(
        "tenant-a",
        suite_id="finops-v1",
        name="FinOps v1",
        version="1",
        source="owned",
        license_id="Apache-2.0",
        cases=load_finops_suite(),
    )
    run_id = store.create_run(
        "tenant-a",
        EvaluationRunRequest(suite_id="finops-v1", profile="release"),
    )
    for target, text in (
        ("moa/quality", "candidate answer"),
        ("base/model", "baseline answer"),
    ):
        store.record_output(
            "tenant-a",
            run_id,
            case_id="anomaly-spend-spike",
            target=target,
            attempt=1,
            output_text=text,
            usage={},
            latency={},
            cost={},
            status="succeeded",
        )
    comparison_id = store.create_comparison(
        "tenant-a",
        run_id,
        case_id="anomaly-spend-spike",
        candidate_target="moa/quality",
        baseline_target="base/model",
    )
    store.finalize_comparison(
        "tenant-a",
        comparison_id,
        decision=PairwiseDecision.ABSTAIN,
        needs_human_review=True,
    )
    store.complete_run(
        "tenant-a",
        run_id,
        summary={"releaseGate": "pending_human_review"},
        status="completed",
    )

    comparisons = client.get(
        f"/api/evaluations/runs/{run_id}/comparisons",
        headers={"X-MultiLLM-Tenant": "tenant-a"},
    )
    assert comparisons.status_code == 200
    assert comparisons.json()["data"][0]["needsHumanReview"] is True

    queue = client.get(
        f"/api/evaluations/reviews/queue?run_id={run_id}",
        headers={"X-MultiLLM-Tenant": "tenant-a"},
    )
    assert queue.status_code == 200
    queued = queue.json()["data"][0]
    assert {queued["responseA"], queued["responseB"]} == {
        "candidate answer",
        "baseline answer",
    }
    assert "candidateTarget" not in queue.text

    review = client.post(
        f"/api/evaluations/reviews/{comparison_id}",
        headers={
            "X-MultiLLM-Tenant": "tenant-a",
            "X-MultiLLM-Reviewer": "reviewer-1",
        },
        json={"decision": "response_a", "rationale": "More complete."},
    )
    assert review.status_code == 200
    assert review.json()["data"]["blindDecision"] == "response_a"
    assert "candidate" not in review.text
    resolved = store.list_comparisons("tenant-a", run_id)[0]["humanDecision"]
    assert resolved == (
        "candidate" if queued["responseA"] == "candidate answer" else "baseline"
    )
    refreshed = store.get_run("tenant-a", run_id)
    assert refreshed["summary"]["releaseGate"] == "not_demonstrated"
    assert refreshed["summary"]["pairwise"][0]["sampleCount"] == 1
    assert (
        client.get(
            f"/api/evaluations/reviews/queue?run_id={run_id}",
            headers={"X-MultiLLM-Tenant": "tenant-a"},
        ).json()["data"]
        == []
    )
