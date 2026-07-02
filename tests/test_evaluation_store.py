from __future__ import annotations

import csv
import io
import json

from multillm.evaluation.artifacts import ArtifactCipher
from multillm.evaluation.contracts import EvaluationCase, EvaluationRunRequest
from multillm.evaluation.store import EvaluationStore


def _cases() -> tuple[EvaluationCase, ...]:
    return (
        EvaluationCase(
            id="anomaly-spike",
            prompt="Explain a 38% spend spike.",
            category="anomaly_detection",
            expected_tools=("get_cost_data",),
            required_terms=("baseline", "owner"),
            tags=("finops", "live"),
        ),
        EvaluationCase(
            id="focus-nlp",
            prompt="Map this question to FOCUS fields.",
            category="focus_nlp",
            required_terms=("ServiceName", "EffectiveCost"),
            tags=("finops", "nlp"),
        ),
    )


def _store(path):
    return EvaluationStore(path, artifact_cipher=ArtifactCipher(bytes(range(32))))


def test_suite_and_run_lifecycle_are_tenant_scoped_and_resumable(tmp_path):
    store = _store(tmp_path / "eval.db")
    suite = store.upsert_suite(
        "tenant-a",
        suite_id="finops-v1",
        name="FinOps v1",
        version="1.0.0",
        source="owned",
        license_id="Apache-2.0",
        cases=_cases(),
    )
    assert suite["caseCount"] == 2
    assert len(suite["contentHash"]) == 64

    run_id = store.create_run(
        "tenant-a",
        EvaluationRunRequest(
            suite_id="finops-v1",
            candidates=("codex/gpt-5-5", "claude-cli/sonnet"),
            moa_variants=("moa/quality",),
        ),
    )
    assert store.get_run("tenant-b", run_id) is None

    claimed = store.claim_next_run("worker-1", lease_seconds=30)
    assert claimed is not None
    assert claimed["id"] == run_id
    assert claimed["status"] == "running"
    assert store.heartbeat("tenant-a", run_id, "worker-1", lease_seconds=30)

    store.record_output(
        "tenant-a",
        run_id,
        case_id="anomaly-spike",
        target="codex/gpt-5-5",
        attempt=1,
        output_text="Compare the baseline and assign an owner.",
        usage={"input_tokens": 10, "output_tokens": 9},
        latency={"total_ms": 1200, "ttft_ms": None, "ttft_unavailable_reason": "non_streaming_cli"},
        cost={"actual_usd": None, "normalized_usd": 0.01, "pricing_version": "2026-07"},
        status="succeeded",
    )
    store.record_metric(
        "tenant-a",
        run_id,
        case_id="anomaly-spike",
        target="codex/gpt-5-5",
        metric="required_terms",
        value=1.0,
        passed=True,
        details={"matched": ["baseline", "owner"]},
    )
    assert store.complete_run("tenant-a", run_id, summary={"passed": True}, status="completed")

    detail = store.get_run("tenant-a", run_id)
    assert detail is not None
    assert detail["status"] == "completed"
    assert detail["summary"] == {"passed": True}
    assert detail["outputs"][0]["outputText"] == "Compare the baseline and assign an owner."
    assert detail["metrics"][0]["passed"] is True


def test_output_idempotency_and_cancel_are_safe(tmp_path):
    store = _store(tmp_path / "eval.db")
    store.upsert_suite(
        "tenant-a",
        suite_id="finops-v1",
        name="FinOps v1",
        version="1",
        source="owned",
        license_id="Apache-2.0",
        cases=_cases(),
    )
    run_id = store.create_run("tenant-a", EvaluationRunRequest(suite_id="finops-v1"))

    kwargs = {
        "case_id": "focus-nlp",
        "target": "moa/quality",
        "attempt": 1,
        "output_text": "first",
        "usage": {},
        "latency": {"total_ms": 1},
        "cost": {},
        "status": "succeeded",
    }
    first = store.record_output("tenant-a", run_id, **kwargs)
    second = store.record_output("tenant-a", run_id, **{**kwargs, "output_text": "replacement"})
    assert first == second
    assert store.get_run("tenant-a", run_id)["outputs"][0]["outputText"] == "first"

    other = store.create_run("tenant-a", EvaluationRunRequest(suite_id="finops-v1"))
    assert store.cancel_run("tenant-b", other) is False
    assert store.cancel_run("tenant-a", other) is True
    assert store.get_run("tenant-a", other)["status"] == "cancelled"


def test_export_rows_are_flat_reproducible_and_csv_safe(tmp_path):
    store = _store(tmp_path / "eval.db")
    store.upsert_suite(
        "tenant-a",
        suite_id="finops-v1",
        name="FinOps v1",
        version="1",
        source="owned",
        license_id="Apache-2.0",
        cases=_cases(),
    )
    run_id = store.create_run("tenant-a", EvaluationRunRequest(suite_id="finops-v1"))
    store.record_output(
        "tenant-a",
        run_id,
        case_id="focus-nlp",
        target="codex/gpt-5-5",
        attempt=1,
        output_text="=HYPERLINK(\"bad\")",
        usage={"input_tokens": 2, "output_tokens": 3},
        latency={"total_ms": 5},
        cost={"normalized_usd": 0.1},
        status="succeeded",
    )

    bundle = store.export_run("tenant-a", run_id)
    assert bundle["manifest"]["runId"] == run_id
    assert len(bundle["manifest"]["sha256"]) == 64
    assert json.dumps(bundle, sort_keys=True)

    rows = list(csv.DictReader(io.StringIO(store.export_run_csv("tenant-a", run_id))))
    assert rows[0]["outputText"].startswith("'=")
