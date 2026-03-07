"""Tests for the health check module."""

import asyncio
import pytest
import time
from unittest.mock import patch, AsyncMock

from multillm.health import (
    BackendHealth,
    get_health,
    is_backend_healthy,
    all_health_status,
    check_all_backends,
    _health,
    DEGRADED_LATENCY_MS,
)


class TestBackendHealth:
    def test_initial_state(self):
        h = BackendHealth()
        assert h.status == "unknown"
        assert h.consecutive_failures == 0

    def test_mark_healthy(self):
        h = BackendHealth()
        h.mark_healthy(150.0)
        assert h.status == "healthy"
        assert h.last_latency_ms == 150.0
        assert h.last_error is None
        assert h.consecutive_failures == 0

    def test_mark_degraded(self):
        h = BackendHealth()
        h.mark_degraded(6000.0, "slow: 6000ms")
        assert h.status == "degraded"
        assert h.last_latency_ms == 6000.0
        assert "slow" in h.last_error

    def test_mark_unhealthy(self):
        h = BackendHealth()
        h.mark_unhealthy("connection refused")
        assert h.status == "unhealthy"
        assert h.consecutive_failures == 1
        h.mark_unhealthy("still down")
        assert h.consecutive_failures == 2

    def test_healthy_resets_failures(self):
        h = BackendHealth()
        h.mark_unhealthy("err")
        h.mark_unhealthy("err")
        h.mark_healthy(100.0)
        assert h.consecutive_failures == 0

    def test_to_dict(self):
        h = BackendHealth()
        h.mark_healthy(200.0)
        d = h.to_dict()
        assert d["status"] == "healthy"
        assert d["latency_ms"] == 200.0
        assert "last_check_ago" in d


class TestHealthRegistry:
    def setup_method(self):
        _health.clear()

    def test_get_health_creates_entry(self):
        h = get_health("test_backend")
        assert h.status == "unknown"
        assert "test_backend" in _health

    def test_is_backend_healthy_unknown(self):
        assert is_backend_healthy("never_checked") is True

    def test_is_backend_healthy_healthy(self):
        get_health("ok_backend").mark_healthy(100)
        assert is_backend_healthy("ok_backend") is True

    def test_is_backend_healthy_degraded(self):
        get_health("slow_backend").mark_degraded(6000, "slow")
        assert is_backend_healthy("slow_backend") is True  # degraded is still usable

    def test_is_backend_healthy_unhealthy(self):
        get_health("bad_backend").mark_unhealthy("down")
        assert is_backend_healthy("bad_backend") is False

    def test_all_health_status(self):
        get_health("a").mark_healthy(50)
        get_health("b").mark_unhealthy("err")
        status = all_health_status()
        assert "a" in status
        assert status["a"]["status"] == "healthy"
        assert status["b"]["status"] == "unhealthy"
        assert "circuit_breaker" in status["a"]


class TestCheckAllBackends:
    def setup_method(self):
        _health.clear()

    @pytest.mark.asyncio
    async def test_check_marks_local_backends(self):
        """Test that probes run and update health state."""
        # Mock all probes to return quickly
        async def mock_probe_ok():
            return True, 50.0, ""

        async def mock_probe_fail():
            return False, 0.0, "not configured"

        probes = {
            "ollama": lambda: mock_probe_ok(),
            "openai": lambda: mock_probe_fail(),
        }

        with patch("multillm.health.BACKEND_PROBES", probes), \
             patch("multillm.health._probe_anthropic", return_value=(False, 0, "not configured")), \
             patch("multillm.health._probe_oca", return_value=(False, 0, "not configured")), \
             patch("multillm.health._probe_gemini", return_value=(False, 0, "not configured")):
            await check_all_backends()

        assert _health["ollama"].status == "healthy"
        assert _health["openai"].status == "unconfigured"

    @pytest.mark.asyncio
    async def test_slow_backend_marked_degraded(self):
        async def mock_probe_slow():
            return True, DEGRADED_LATENCY_MS + 1000, ""

        probes = {"slow_service": lambda: mock_probe_slow()}

        with patch("multillm.health.BACKEND_PROBES", probes), \
             patch("multillm.health._probe_anthropic", return_value=(False, 0, "not configured")), \
             patch("multillm.health._probe_oca", return_value=(False, 0, "not configured")), \
             patch("multillm.health._probe_gemini", return_value=(False, 0, "not configured")):
            await check_all_backends()

        assert _health["slow_service"].status == "degraded"

    @pytest.mark.asyncio
    async def test_failed_probe_marks_unhealthy(self):
        async def mock_probe_err():
            return False, 200.0, "HTTP 503"

        probes = {"failing": lambda: mock_probe_err()}

        with patch("multillm.health.BACKEND_PROBES", probes), \
             patch("multillm.health._probe_anthropic", return_value=(False, 0, "not configured")), \
             patch("multillm.health._probe_oca", return_value=(False, 0, "not configured")), \
             patch("multillm.health._probe_gemini", return_value=(False, 0, "not configured")):
            await check_all_backends()

        assert _health["failing"].status == "unhealthy"
        assert _health["failing"].last_error == "HTTP 503"
