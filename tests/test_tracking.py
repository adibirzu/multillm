# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

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

    def test_cache_token_tracking(self):
        record_usage(
            project="test-cache",
            model_alias="anthropic/claude",
            backend="anthropic",
            real_model="claude-sonnet-4-6",
            input_tokens=1000,
            output_tokens=100,
            cache_read_input_tokens=500,
            cache_creation_input_tokens=200,
            latency_ms=200.0,
        )

        summary = get_usage_summary(project="test-cache", hours=1)
        entry = next((s for s in summary if s["model_alias"] == "anthropic/claude"), None)
        assert entry is not None
        assert entry["total_cache_read_input"] >= 500
        assert entry["total_cache_creation_input"] >= 200
        assert entry["total_cost_usd"] > 0

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


class TestOCIAPMEndpoints:
    """OCI APM OTLP signal paths must match the documented format (KB: 404 fix)."""

    def test_traces_endpoint_uses_private_v1_traces(self, monkeypatch):
        from multillm import tracking, config
        monkeypatch.setattr(config, "OCI_APM_ENDPOINT",
                            "https://apm-trace.eu-frankfurt-1.oci.oraclecloud.com/20200101/opentelemetry/")
        monkeypatch.setattr(tracking, "OCI_APM_ENDPOINT", config.OCI_APM_ENDPOINT)
        monkeypatch.setattr(tracking, "OCI_APM_DATA_KEY_TYPE", "private")
        url = tracking._oci_apm_signal_endpoint("traces")
        assert url.endswith("/20200101/opentelemetry/private/v1/traces")

    def test_traces_endpoint_supports_public_key(self, monkeypatch):
        from multillm import tracking, config
        monkeypatch.setattr(tracking, "OCI_APM_ENDPOINT",
                            "https://apm-trace.eu-frankfurt-1.oci.oraclecloud.com/20200101/opentelemetry/")
        monkeypatch.setattr(tracking, "OCI_APM_DATA_KEY_TYPE", "public")
        assert tracking._oci_apm_signal_endpoint("traces").endswith("/opentelemetry/public/v1/traces")

    def test_metrics_endpoint_uses_v1_metrics(self, monkeypatch):
        from multillm import tracking
        monkeypatch.setattr(tracking, "OCI_APM_ENDPOINT",
                            "https://apm-trace.eu-frankfurt-1.oci.oraclecloud.com/20200101/opentelemetry/")
        url = tracking._oci_apm_signal_endpoint("metrics")
        assert url.endswith("/20200101/opentelemetry/v1/metrics")
        assert "/metrics/" not in url  # the old bogus path that 404'd

    def test_unknown_key_type_falls_back_to_private(self, monkeypatch):
        from multillm import tracking
        monkeypatch.setattr(tracking, "OCI_APM_ENDPOINT",
                            "https://x.oci.oraclecloud.com/20200101/opentelemetry/")
        monkeypatch.setattr(tracking, "OCI_APM_DATA_KEY_TYPE", "garbage")
        assert "/private/v1/traces" in tracking._oci_apm_signal_endpoint("traces")
