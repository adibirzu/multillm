from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time

import pytest

from multillm.evaluation.artifacts import ArtifactCipher
from multillm.evaluation.contracts import EvaluationCase, EvaluationRunRequest
from multillm.evaluation.runner import EvaluationResponse, EvaluationRunner
from multillm.evaluation.store import EvaluationStore
from multillm import langfuse_integration


@dataclass
class _Observation:
    payload: dict
    ended: bool = False

    def end(self) -> None:
        self.ended = True


@dataclass
class _Client:
    observations: list[_Observation] = field(default_factory=list)

    def start_observation(self, **payload):
        observation = _Observation(payload)
        self.observations.append(observation)
        return observation


def test_evaluation_trace_only_emits_allowlisted_metadata(monkeypatch):
    client = _Client()
    monkeypatch.setattr(langfuse_integration, "_client", client)

    langfuse_integration.trace_evaluation_run(
        run_id="eval_0123456789abcdef0123",
        suite_id="finops-v1",
        tenant_id="customer-secret-name",
        status="completed",
        profile="release",
        execution_mode="live_host",
        candidates=("codex/gpt-5-5",),
        moa_variants=("moa/quality",),
        judge_pool=("claude-cli/sonnet", "gemini-cli/flash"),
        summary={
            "outputs": 4,
            "failures": [{"caseId": "private-case", "error": "RuntimeError"}],
            "deterministicPassRate": 0.75,
            "releaseGate": "not_demonstrated",
            "pairwise": [
                {
                    "candidate": "moa/quality",
                    "baseline": "codex/gpt-5-5",
                    "winRate": 0.75,
                    "lower95": 0.5,
                    "upper95": 1.0,
                    "sampleCount": 4,
                    "rationale": "must never be sent",
                }
            ],
            "rawPrompt": "private prompt",
            "rawOutput": "private output",
        },
    )

    assert len(client.observations) == 1
    observation = client.observations[0]
    assert observation.ended is True
    assert "input" not in observation.payload
    assert "output" not in observation.payload
    serialized = repr(observation.payload)
    assert "customer-secret-name" not in serialized
    assert "private prompt" not in serialized
    assert "private output" not in serialized
    assert "must never be sent" not in serialized
    assert "private-case" not in serialized
    assert observation.payload["metadata"]["failureCount"] == 1
    assert observation.payload["metadata"]["targets"] == [
        "codex/gpt-5-5",
        "moa/quality",
    ]


def test_generation_trace_can_capture_bounded_visible_content(monkeypatch):
    client = _Client()
    monkeypatch.setattr(langfuse_integration, "_client", client)
    monkeypatch.setattr(langfuse_integration, "LANGFUSE_CAPTURE_CONTENT", True)
    monkeypatch.setattr(langfuse_integration, "LANGFUSE_CONTENT_MAX_CHARS", 12)

    langfuse_integration.trace_llm_generation(
        model_alias="claude-cli/fable",
        backend="claude_cli",
        real_model="claude-fable-5",
        project="llm-project",
        prompt_text="visible prompt content",
        response_text="visible response content",
        input_tokens=10,
        output_tokens=20,
        reasoning_tokens=7,
    )

    payload = client.observations[0].payload
    assert payload["input"] == "visible prom"
    assert payload["output"] == "visible resp"
    assert payload["usage_details"]["reasoning"] == 7
    assert payload["metadata"]["project"] == "llm-project"


def test_langfuse_shutdown_is_bounded_when_delivery_is_stuck(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    class _BlockedClient:
        def shutdown(self):
            started.set()
            release.wait(timeout=5)

    monkeypatch.setattr(langfuse_integration, "_client", _BlockedClient())
    before = time.monotonic()

    langfuse_integration.shutdown_langfuse(timeout_seconds=0.01)

    assert started.wait(timeout=0.2)
    assert time.monotonic() - before < 0.2
    assert langfuse_integration._client is None
    release.set()


@pytest.mark.asyncio
async def test_runner_calls_completion_hook_with_content_redacted(tmp_path):
    store = EvaluationStore(
        tmp_path / "eval.db", artifact_cipher=ArtifactCipher(b"z" * 32)
    )
    store.upsert_suite(
        "tenant-a",
        suite_id="suite-1",
        name="Suite",
        version="1",
        source="owned",
        license_id="Apache-2.0",
        cases=(
            EvaluationCase(id="case-1", prompt="secret prompt", category="finops"),
        ),
    )
    store.create_run(
        "tenant-a",
        EvaluationRunRequest(
            suite_id="suite-1",
            candidates=("model/a",),
            moa_variants=(),
        ),
    )
    completed: list[dict] = []

    async def execute(_target, _case, _request):
        return EvaluationResponse(text="secret response")

    runner = EvaluationRunner(
        store=store,
        execute=execute,
        worker_id="worker-1",
        on_complete=completed.append,
    )

    assert await runner.run_once()
    assert len(completed) == 1
    assert completed[0]["status"] == "completed"
    assert completed[0]["outputs"][0]["outputText"] is None
    assert "secret prompt" not in repr(completed[0])
    assert "secret response" not in repr(completed[0])
