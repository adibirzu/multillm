"""Tests for session tracking in the tracking module."""
import json
import time
from unittest.mock import patch

import pytest

from multillm import tracking
from multillm.tracking import (
    record_usage,
    get_sessions,
    get_session_detail,
    get_dashboard_stats,
    update_streaming_usage,
    SESSION_GAP_SECONDS,
)


@pytest.fixture(autouse=True)
def reset_session_state():
    """Reset the per-project session state between tests."""
    tracking._sessions.clear()
    yield
    tracking._sessions.clear()


def _record(project="test-sess", model="ollama/llama3", **kwargs):
    """Helper to record usage with defaults."""
    record_usage(
        project=project,
        model_alias=model,
        backend=model.split("/")[0],
        real_model=model.split("/")[1],
        input_tokens=kwargs.get("input_tokens", 100),
        output_tokens=kwargs.get("output_tokens", 50),
        latency_ms=kwargs.get("latency_ms", 200.0),
        status=kwargs.get("status", "ok"),
    )


class TestSessionCreation:

    def test_first_request_creates_session(self):
        _record(project="sess-create")
        sessions = get_sessions(hours=1, project="sess-create")
        assert len(sessions) >= 1
        s = sessions[0]
        assert s["id"].startswith("sess_")
        assert s["project"] == "sess-create"
        assert s["total_requests"] == 1

    def test_rapid_requests_same_session(self):
        _record(project="sess-rapid")
        _record(project="sess-rapid")
        _record(project="sess-rapid")
        sessions = get_sessions(hours=1, project="sess-rapid")
        assert len(sessions) == 1
        assert sessions[0]["total_requests"] == 3

    def test_gap_creates_new_session(self):
        _record(project="sess-gap")
        # Simulate a gap larger than SESSION_GAP_SECONDS
        sess_id, _ = tracking._sessions["sess-gap"]
        tracking._sessions["sess-gap"] = (sess_id, time.time() - SESSION_GAP_SECONDS - 10)
        _record(project="sess-gap")
        sessions = get_sessions(hours=1, project="sess-gap")
        assert len(sessions) == 2

    def test_session_aggregates_tokens(self):
        _record(project="sess-agg", input_tokens=100, output_tokens=50)
        _record(project="sess-agg", input_tokens=200, output_tokens=100)
        sessions = get_sessions(hours=1, project="sess-agg")
        s = sessions[0]
        assert s["total_input_tokens"] == 300
        assert s["total_output_tokens"] == 150
        assert s["total_requests"] == 2

    def test_session_aggregates_cache_tokens(self):
        record_usage(
            project="sess-cache",
            model_alias="anthropic/claude",
            backend="anthropic",
            real_model="claude-sonnet-4-6",
            input_tokens=100,
            output_tokens=50,
            cache_read_input_tokens=25,
            cache_creation_input_tokens=10,
            latency_ms=100.0,
        )
        sessions = get_sessions(hours=1, project="sess-cache")
        s = sessions[0]
        assert s["total_cache_read_input_tokens"] == 25
        assert s["total_cache_creation_input_tokens"] == 10

    def test_session_tracks_models_used(self):
        _record(project="sess-models", model="ollama/llama3")
        _record(project="sess-models", model="openai/gpt-4o")
        _record(project="sess-models", model="ollama/llama3")  # duplicate
        sessions = get_sessions(hours=1, project="sess-models")
        s = sessions[0]
        models = s["models_used"]
        assert "ollama/llama3" in models
        assert "openai/gpt-4o" in models
        assert len(models) == 2  # no duplicates


class TestSessionQueries:

    def test_get_sessions_project_filter(self):
        _record(project="filter-a")
        _record(project="filter-b")  # Different project = different session automatically

        a = get_sessions(hours=1, project="filter-a")
        b = get_sessions(hours=1, project="filter-b")
        assert all(s["project"] == "filter-a" for s in a)
        assert all(s["project"] == "filter-b" for s in b)

    def test_get_sessions_limit(self):
        for i in range(5):
            # Force new session each iteration by expiring the last request time
            if "limit-test" in tracking._sessions:
                sid, _ = tracking._sessions["limit-test"]
                tracking._sessions["limit-test"] = (sid, 0.0)
            _record(project="limit-test")
        sessions = get_sessions(hours=1, project="limit-test", limit=3)
        assert len(sessions) <= 3

    def test_get_session_detail(self):
        _record(project="detail-test", model="gemini/flash")
        _record(project="detail-test", model="ollama/llama3")
        sessions = get_sessions(hours=1, project="detail-test")
        detail = get_session_detail(sessions[0]["id"])
        assert detail["id"] == sessions[0]["id"]
        assert len(detail["requests"]) == 2
        assert detail["requests"][0]["model_alias"] == "gemini/flash"

    def test_get_session_detail_nonexistent(self):
        detail = get_session_detail("sess_nonexistent")
        assert detail == {}


class TestCrossProjectIsolation:

    def test_different_projects_get_different_sessions(self):
        _record(project="proj-alpha")
        _record(project="proj-beta")
        _record(project="proj-alpha")

        alpha = get_sessions(hours=1, project="proj-alpha")
        beta = get_sessions(hours=1, project="proj-beta")
        assert len(alpha) == 1
        assert len(beta) == 1
        assert alpha[0]["id"] != beta[0]["id"]
        assert alpha[0]["total_requests"] == 2
        assert beta[0]["total_requests"] == 1


class TestUpdateStreamingUsage:

    def test_update_streaming_tokens(self):
        usage_id = record_usage(
            project="stream-test", model_alias="ollama/llama3",
            backend="ollama", real_model="llama3",
            input_tokens=0, output_tokens=0, latency_ms=100.0,
            status="streaming",
        )
        update_streaming_usage(usage_id, input_tokens=500, output_tokens=200)
        # Verify via session detail
        sessions = get_sessions(hours=1, project="stream-test")
        assert sessions[0]["total_input_tokens"] == 500
        assert sessions[0]["total_output_tokens"] == 200

    def test_update_nonexistent_noop(self):
        # Should not raise
        update_streaming_usage("req_nonexistent", 100, 50)


class TestDashboardStats:

    def test_dashboard_stats_structure(self):
        _record(project="dash-test")
        stats = get_dashboard_stats(hours=1)
        assert "totals" in stats
        assert "hours" in stats
        assert "session_count" in stats
        assert "derived" in stats
        assert "by_backend" in stats
        assert "by_model" in stats
        assert "daily" in stats
        assert "hourly" in stats

    def test_dashboard_stats_counts(self):
        record_usage(
            project="dash-count",
            model_alias="anthropic/claude",
            backend="anthropic",
            real_model="claude-sonnet-4-6",
            input_tokens=500,
            output_tokens=200,
            cache_read_input_tokens=100,
            cache_creation_input_tokens=50,
            latency_ms=200.0,
        )
        stats = get_dashboard_stats(hours=1)
        assert stats["totals"]["total_requests"] >= 1
        assert stats["totals"]["total_input"] >= 500
        assert stats["totals"]["total_cache_read_input"] >= 100
        assert stats["totals"]["total_cache_creation_input"] >= 50
        assert stats["session_count"] >= 1
        assert stats["derived"]["total_tokens"] >= 850
        assert stats["derived"]["avg_tokens_per_request"] > 0

    def test_dashboard_daily_breakdown(self):
        _record(project="dash-daily")
        stats = get_dashboard_stats(hours=1)
        assert len(stats["daily"]) >= 1
        day = stats["daily"][-1]
        assert "day" in day
        assert day["requests"] >= 1

    def test_dashboard_stats_project_filter(self):
        _record(project="dash-alpha", input_tokens=300, output_tokens=100)
        _record(project="dash-beta", input_tokens=50, output_tokens=25)
        stats = get_dashboard_stats(hours=1, project="dash-alpha")
        assert stats["project"] == "dash-alpha"
        assert stats["totals"]["total_input"] >= 300
        assert stats["totals"]["total_output"] >= 100
        assert stats["totals"]["total_input"] < 350
        assert stats["session_count"] == 1
