"""Tests for the resilience module — retry logic and circuit breakers."""

import asyncio
import pytest
import httpx
from unittest.mock import AsyncMock, patch

from multillm.resilience import (
    CircuitBreaker,
    with_retry,
    BackendUnavailableError,
    get_breaker,
    all_breaker_status,
    _is_retryable,
    _breakers,
)


# ── Circuit Breaker Tests ────────────────────────────────────────────────────


class TestCircuitBreaker:
    def setup_method(self):
        self.cb = CircuitBreaker(failure_threshold=3, recovery_timeout=1.0)

    def test_starts_closed(self):
        assert self.cb.state == "closed"
        assert self.cb.is_available

    def test_stays_closed_below_threshold(self):
        self.cb.record_failure()
        self.cb.record_failure()
        assert self.cb.state == "closed"
        assert self.cb.is_available

    def test_opens_at_threshold(self):
        for _ in range(3):
            self.cb.record_failure()
        assert self.cb.state == "open"
        assert not self.cb.is_available

    def test_success_resets_failures(self):
        self.cb.record_failure()
        self.cb.record_failure()
        self.cb.record_success()
        assert self.cb._failures == 0
        assert self.cb.state == "closed"

    def test_half_open_after_recovery_timeout(self):
        for _ in range(3):
            self.cb.record_failure()
        assert self.cb.state == "open"
        # Simulate time passing
        self.cb._last_failure_time -= 2.0  # past recovery timeout
        assert self.cb.state == "half-open"
        assert self.cb.is_available

    def test_half_open_limits_concurrent(self):
        for _ in range(3):
            self.cb.record_failure()
        self.cb._last_failure_time -= 2.0
        assert self.cb.state == "half-open"
        self.cb.record_half_open_attempt()
        assert not self.cb.is_available  # max 1 in half-open

    def test_half_open_success_closes(self):
        for _ in range(3):
            self.cb.record_failure()
        self.cb._last_failure_time -= 2.0
        self.cb.record_success()
        assert self.cb.state == "closed"

    def test_half_open_failure_reopens(self):
        for _ in range(3):
            self.cb.record_failure()
        self.cb._last_failure_time -= 2.0
        assert self.cb.state == "half-open"
        self.cb.record_failure()
        assert self.cb.state == "open"
        assert self.cb._total_trips == 2

    def test_status_dict(self):
        status = self.cb.status()
        assert "state" in status
        assert "failures" in status
        assert "threshold" in status
        assert status["threshold"] == 3

    def test_total_trips_counter(self):
        for _ in range(3):
            self.cb.record_failure()
        assert self.cb._total_trips == 1


# ── Retryable Detection ─────────────────────────────────────────────────────


class TestRetryableDetection:
    def test_connect_error_is_retryable(self):
        assert _is_retryable(httpx.ConnectError("fail"))

    def test_timeout_is_retryable(self):
        assert _is_retryable(httpx.ConnectTimeout("timeout"))
        assert _is_retryable(httpx.ReadTimeout("timeout"))

    def test_429_is_retryable(self):
        response = httpx.Response(429, request=httpx.Request("POST", "http://test"))
        assert _is_retryable(httpx.HTTPStatusError("rate limit", request=response.request, response=response))

    def test_502_is_retryable(self):
        response = httpx.Response(502, request=httpx.Request("POST", "http://test"))
        assert _is_retryable(httpx.HTTPStatusError("bad gw", request=response.request, response=response))

    def test_400_not_retryable(self):
        response = httpx.Response(400, request=httpx.Request("POST", "http://test"))
        assert not _is_retryable(httpx.HTTPStatusError("bad req", request=response.request, response=response))

    def test_value_error_not_retryable(self):
        assert not _is_retryable(ValueError("nope"))


# ── with_retry Tests ─────────────────────────────────────────────────────────


class TestWithRetry:
    def setup_method(self):
        _breakers.clear()

    @pytest.mark.asyncio
    async def test_success_no_retry(self):
        call_count = 0
        async def factory():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await with_retry(factory, "test_backend", max_retries=2)
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_transient_error(self):
        call_count = 0
        async def factory():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise httpx.ConnectError("fail")
            return "recovered"

        result = await with_retry(factory, "test_backend", max_retries=2, base_delay=0.01)
        assert result == "recovered"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_no_retry_on_non_retryable(self):
        async def factory():
            raise ValueError("bad input")

        with pytest.raises(ValueError):
            await with_retry(factory, "test_backend", max_retries=2, base_delay=0.01)

    @pytest.mark.asyncio
    async def test_exhausts_retries(self):
        call_count = 0
        async def factory():
            nonlocal call_count
            call_count += 1
            raise httpx.ConnectError("always fails")

        with pytest.raises(httpx.ConnectError):
            await with_retry(factory, "test_backend", max_retries=2, base_delay=0.01)
        assert call_count == 3  # 1 initial + 2 retries

    @pytest.mark.asyncio
    async def test_circuit_breaker_blocks(self):
        breaker = get_breaker("blocked_backend")
        for _ in range(5):
            breaker.record_failure()
        assert breaker.state == "open"

        async def factory():
            return "should not run"

        with pytest.raises(BackendUnavailableError):
            await with_retry(factory, "blocked_backend")

    @pytest.mark.asyncio
    async def test_success_resets_breaker(self):
        breaker = get_breaker("recovering")
        breaker.record_failure()
        breaker.record_failure()

        async def factory():
            return "ok"

        await with_retry(factory, "recovering")
        assert breaker._failures == 0
        assert breaker.state == "closed"

    @pytest.mark.asyncio
    async def test_breaker_status_registry(self):
        _breakers.clear()
        get_breaker("a")
        get_breaker("b")
        status = all_breaker_status()
        assert "a" in status
        assert "b" in status
        assert status["a"]["state"] == "closed"
