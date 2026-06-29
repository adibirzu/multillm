# SPDX-License-Identifier: Apache-2.0

from multillm.orchestration_store import OrchestrationStore


def test_trace_storage_is_tenant_scoped_and_does_not_persist_raw_prompt(tmp_path):
    store = OrchestrationStore(tmp_path / "orchestration.db")
    run_id = store.create_run(
        tenant_id="tenant-a",
        prompt="private prompt text",
        policy={"preset": "balanced"},
        task_features={"task_type": "coding"},
    )
    store.record_call(
        tenant_id="tenant-a",
        run_id=run_id,
        stage="draft",
        model="ollama/small",
        effort="low",
        usage={"input_tokens": 10, "output_tokens": 5},
        cost_usd=0.0,
        latency_ms=5,
        status="ok",
    )

    assert store.get_trace("tenant-b", run_id) is None
    trace = store.get_trace("tenant-a", run_id)
    assert trace is not None
    assert trace["promptHash"]
    assert "private prompt text" not in str(trace)
    assert trace["calls"][0]["stage"] == "draft"


def test_feedback_and_scorecards_never_cross_tenants(tmp_path):
    store = OrchestrationStore(tmp_path / "orchestration.db")
    run_a = store.create_run("tenant-a", "a", {}, {"task_type": "coding"})
    run_b = store.create_run("tenant-b", "b", {}, {"task_type": "coding"})

    store.add_feedback("tenant-a", run_a, rating=5, issue_categories=("correct",))
    assert store.add_feedback("tenant-a", run_b, rating=1) is False

    feedback_a = store.list_feedback("tenant-a")
    feedback_b = store.list_feedback("tenant-b")
    assert len(feedback_a) == 1
    assert feedback_b == []


def test_feedback_validation_rejects_out_of_range_ratings(tmp_path):
    store = OrchestrationStore(tmp_path / "orchestration.db")
    run_id = store.create_run("default", "a", {}, {})

    try:
        store.add_feedback("default", run_id, rating=6)
    except ValueError as exc:
        assert "rating" in str(exc)
    else:
        raise AssertionError("invalid feedback rating was accepted")


def test_scorecards_require_minimum_samples_before_routing(tmp_path):
    store = OrchestrationStore(tmp_path / "orchestration.db")
    for _ in range(19):
        store.record_scorecard_observation(
            "tenant-a",
            model="openai/model",
            task_type="coding",
            quality=1.0,
            reliable=True,
            cost_usd=0.01,
        )

    assert store.get_scorecards("tenant-a") == []
    store.record_scorecard_observation(
        "tenant-a",
        model="openai/model",
        task_type="coding",
        quality=1.0,
        reliable=True,
        cost_usd=0.01,
    )

    scorecards = store.get_scorecards("tenant-a")
    assert len(scorecards) == 1
    assert scorecards[0]["sample_count"] == 20
    assert scorecards[0]["confidence_lower"] > 0.7
    assert store.get_scorecards("tenant-b") == []
