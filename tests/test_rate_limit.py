"""Tests for the rate limiting module."""

import pytest
import time

from multillm.rate_limit import (
    TokenBucket,
    check_rate_limit,
    acquire_concurrent,
    release_concurrent,
    rate_limit_status,
    _buckets,
    _concurrent,
)


class TestTokenBucket:
    def test_initial_capacity(self):
        b = TokenBucket(capacity=10)
        assert b.remaining == 10

    def test_acquire_reduces_tokens(self):
        b = TokenBucket(capacity=5)
        assert b.try_acquire()
        assert b.remaining == 4

    def test_exhaustion(self):
        b = TokenBucket(capacity=3)
        assert b.try_acquire()
        assert b.try_acquire()
        assert b.try_acquire()
        assert not b.try_acquire()

    def test_refill_over_time(self):
        b = TokenBucket(capacity=60, window_seconds=60.0)
        # Exhaust all tokens
        for _ in range(60):
            b.try_acquire()
        assert b.remaining == 0
        # Simulate 1 second passing (should refill 1 token)
        b._last_refill -= 1.0
        assert b.remaining >= 1

    def test_retry_after_when_empty(self):
        b = TokenBucket(capacity=60, window_seconds=60.0)
        for _ in range(60):
            b.try_acquire()
        assert b.retry_after > 0

    def test_retry_after_when_available(self):
        b = TokenBucket(capacity=10)
        assert b.retry_after == 0.0

    def test_capacity_ceiling(self):
        b = TokenBucket(capacity=5)
        # Simulate lots of time passing
        b._last_refill -= 1000.0
        assert b.remaining <= 5


class TestCheckRateLimit:
    def setup_method(self):
        _buckets.clear()
        _concurrent.clear()

    def test_disabled_always_allows(self):
        """When RPM=0, rate limiting is disabled."""
        import multillm.rate_limit as rl
        orig = rl.RATE_LIMIT_RPM
        rl.RATE_LIMIT_RPM = 0
        try:
            allowed, headers = check_rate_limit("test:client")
            assert allowed
            assert headers == {}
        finally:
            rl.RATE_LIMIT_RPM = orig

    def test_enabled_allows_within_limit(self):
        import multillm.rate_limit as rl
        orig = rl.RATE_LIMIT_RPM
        rl.RATE_LIMIT_RPM = 100
        try:
            allowed, headers = check_rate_limit("test:client")
            assert allowed
            assert "X-RateLimit-Limit" in headers
            assert headers["X-RateLimit-Limit"] == "100"
        finally:
            rl.RATE_LIMIT_RPM = orig

    def test_enabled_blocks_over_limit(self):
        import multillm.rate_limit as rl
        orig = rl.RATE_LIMIT_RPM
        rl.RATE_LIMIT_RPM = 3
        try:
            check_rate_limit("test:flood")
            check_rate_limit("test:flood")
            check_rate_limit("test:flood")
            allowed, headers = check_rate_limit("test:flood")
            assert not allowed
            assert "Retry-After" in headers
        finally:
            rl.RATE_LIMIT_RPM = orig


class TestConcurrentLimiting:
    def setup_method(self):
        _concurrent.clear()

    def test_disabled_always_allows(self):
        import multillm.rate_limit as rl
        orig = rl.RATE_LIMIT_CONCURRENT
        rl.RATE_LIMIT_CONCURRENT = 0
        try:
            assert acquire_concurrent("c1")
        finally:
            rl.RATE_LIMIT_CONCURRENT = orig

    def test_allows_within_limit(self):
        import multillm.rate_limit as rl
        orig = rl.RATE_LIMIT_CONCURRENT
        rl.RATE_LIMIT_CONCURRENT = 2
        try:
            assert acquire_concurrent("c1")
            assert acquire_concurrent("c1")
            assert not acquire_concurrent("c1")
        finally:
            rl.RATE_LIMIT_CONCURRENT = orig

    def test_release_frees_slot(self):
        import multillm.rate_limit as rl
        orig = rl.RATE_LIMIT_CONCURRENT
        rl.RATE_LIMIT_CONCURRENT = 1
        try:
            assert acquire_concurrent("c1")
            assert not acquire_concurrent("c1")
            release_concurrent("c1")
            assert acquire_concurrent("c1")
        finally:
            rl.RATE_LIMIT_CONCURRENT = orig

    def test_per_client_isolation(self):
        import multillm.rate_limit as rl
        orig = rl.RATE_LIMIT_CONCURRENT
        rl.RATE_LIMIT_CONCURRENT = 1
        try:
            assert acquire_concurrent("c1")
            assert acquire_concurrent("c2")  # different client, allowed
        finally:
            rl.RATE_LIMIT_CONCURRENT = orig


class TestRateLimitStatus:
    def setup_method(self):
        _buckets.clear()
        _concurrent.clear()

    def test_status_structure(self):
        status = rate_limit_status()
        assert "enabled" in status
        assert "rpm_limit" in status
        assert "concurrent_limit" in status
        assert "active_clients" in status
