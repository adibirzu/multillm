"""Tests for the tracking module."""
import pytest
from multillm.tracking import record_usage, get_usage_summary, get_project_summary


class TestUsageTracking:

    def test_record_and_query(self):
        record_usage(
            project="test-track",
            model_alias="ollama/llama3",
            backend="ollama",
            real_model="llama3",
            input_tokens=100,
            output_tokens=50,
            latency_ms=250.0,
        )

        summary = get_usage_summary(project="test-track", hours=1)
        assert len(summary) >= 1
        entry = next((s for s in summary if s["model_alias"] == "ollama/llama3"), None)
        assert entry is not None
        assert entry["total_input"] >= 100
        assert entry["total_output"] >= 50

    def test_record_error(self):
        record_usage(
            project="test-error",
            model_alias="openai/gpt-4o",
            backend="openai",
            real_model="gpt-4o",
            input_tokens=0,
            output_tokens=0,
            latency_ms=100.0,
            status="error",
            error_message="Connection refused",
        )

        summary = get_usage_summary(project="test-error", hours=1)
        entry = next((s for s in summary if s["model_alias"] == "openai/gpt-4o"), None)
        assert entry is not None
        assert entry["error_count"] >= 1

    def test_cost_estimation(self):
        record_usage(
            project="test-cost",
            model_alias="anthropic/claude",
            backend="anthropic",
            real_model="claude-sonnet-4-6",
            input_tokens=1_000_000,
            output_tokens=100_000,
            latency_ms=5000.0,
        )

        summary = get_usage_summary(project="test-cost", hours=1)
        entry = next((s for s in summary if s["model_alias"] == "anthropic/claude"), None)
        assert entry is not None
        assert entry["total_cost_usd"] > 0  # Should have non-zero cost for Anthropic

    def test_free_backend_zero_cost(self):
        record_usage(
            project="test-free",
            model_alias="ollama/mistral",
            backend="ollama",
            real_model="mistral",
            input_tokens=10000,
            output_tokens=5000,
            latency_ms=300.0,
        )

        summary = get_usage_summary(project="test-free", hours=1)
        entry = next((s for s in summary if s["model_alias"] == "ollama/mistral"), None)
        assert entry is not None
        assert entry["total_cost_usd"] == 0

    def test_project_summary(self):
        record_usage(
            project="proj-summary-test",
            model_alias="ollama/llama3",
            backend="ollama",
            real_model="llama3",
            input_tokens=500,
            output_tokens=200,
            latency_ms=150.0,
        )

        projects = get_project_summary(hours=1)
        proj = next((p for p in projects if p["project"] == "proj-summary-test"), None)
        assert proj is not None
        assert proj["requests"] >= 1
        assert proj["input_tokens"] >= 500

    def test_multiple_models_tracking(self):
        for model in ["ollama/llama3", "gemini/flash", "openai/gpt-4o"]:
            record_usage(
                project="multi-model-test",
                model_alias=model,
                backend=model.split("/")[0],
                real_model=model.split("/")[1],
                input_tokens=100,
                output_tokens=50,
                latency_ms=200.0,
            )

        summary = get_usage_summary(project="multi-model-test", hours=1)
        models = {s["model_alias"] for s in summary}
        assert "ollama/llama3" in models
        assert "gemini/flash" in models
        assert "openai/gpt-4o" in models
