"""
Resilience controls for MultiLLM Gateway.

Provides retry with exponential backoff and per-backend circuit breakers.
Circuit breakers prevent hammering failing backends and enable fast fallback.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

log = logging.getLogger("multillm.resilience")

# ── Transient errors worth retrying ──────────────────────────────────────────

RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}

RETRYABLE_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.PoolTimeout,
    ConnectionRefusedError,
    OSError,
)


# ── Circuit Breaker ──────────────────────────────────────────────────────────

@dataclass
class CircuitBreaker:
    """Per-backend circuit breaker with three states: closed, open, half-open."""

    failure_threshold: int = 5
    recovery_timeout: float = 60.0  # seconds before half-open probe
    half_open_max: int = 1  # concurrent requests allowed in half-open

    # Internal state
    _failures: int = field(default=0, repr=False)
    _last_failure_time: float = field(default=0.0, repr=False)
    _state: str = field(default="closed", repr=False)
    _half_open_count: int = field(default=0, repr=False)
    _total_trips: int = field(default=0, repr=False)

    @property
    def state(self) -> str:
        if self._state == "open":
            if time.monotonic() - self._last_failure_time >= self.recovery_timeout:
                self._state = "half-open"
                self._half_open_count = 0
        return self._state

    @property
    def is_available(self) -> bool:
        s = self.state
        if s == "closed":
            return True
        if s == "half-open":
            return self._half_open_count < self.half_open_max
        return False  # open

    def record_success(self):
        if self._state == "half-open":
            log.info("Circuit breaker closing after successful probe")
        self._failures = 0
        self._state = "closed"
        self._half_open_count = 0

    def record_failure(self):
        self._failures += 1
        self._last_failure_time = time.monotonic()
        if self._state == "half-open":
            self._state = "open"
            self._total_trips += 1
            log.warning("Circuit breaker re-opened after half-open probe failure")
        elif self._failures >= self.failure_threshold:
            self._state = "open"
            self._total_trips += 1
            log.warning(
                "Circuit breaker opened after %d consecutive failures",
                self._failures,
            )

    def record_half_open_attempt(self):
        self._half_open_count += 1

    def status(self) -> dict:
        return {
            "state": self.state,
            "failures": self._failures,
            "threshold": self.failure_threshold,
            "recovery_timeout": self.recovery_timeout,
            "total_trips": self._total_trips,
        }


# ── Global breaker registry ──────────────────────────────────────────────────

_breakers: dict[str, CircuitBreaker] = {}


def get_breaker(backend: str) -> CircuitBreaker:
    if backend not in _breakers:
        _breakers[backend] = CircuitBreaker()
    return _breakers[backend]


def all_breaker_status() -> dict[str, dict]:
    return {name: b.status() for name, b in _breakers.items()}


# ── Retry with backoff ───────────────────────────────────────────────────────

def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, RETRYABLE_EXCEPTIONS):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS_CODES
    return False


async def with_retry(
    coro_factory,
    backend: str,
    max_retries: int = 2,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
) -> any:
    """Execute an async operation with retry + circuit breaker.

    Args:
        coro_factory: Callable that returns a new coroutine on each call.
                      Must be a factory (not a coroutine) since coroutines
                      can only be awaited once.
        backend: Backend name for circuit breaker lookup.
        max_retries: Maximum retry attempts (0 = no retries).
        base_delay: Initial backoff delay in seconds.
        max_delay: Maximum backoff delay cap.

    Returns:
        The result of the coroutine.

    Raises:
        The last exception if all retries are exhausted or breaker is open.
    """
    breaker = get_breaker(backend)

    if not breaker.is_available:
        raise BackendUnavailableError(
            f"Circuit breaker open for '{backend}' — "
            f"{breaker._failures} consecutive failures, "
            f"recovery in {breaker.recovery_timeout - (time.monotonic() - breaker._last_failure_time):.0f}s"
        )

    if breaker.state == "half-open":
        breaker.record_half_open_attempt()

    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            result = await coro_factory()
            breaker.record_success()
            return result
        except Exception as exc:
            last_exc = exc
            if not _is_retryable(exc) or attempt >= max_retries:
                breaker.record_failure()
                raise

            delay = min(base_delay * (2 ** attempt), max_delay)
            # Respect Retry-After header for 429s
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                retry_after = exc.response.headers.get("retry-after")
                if retry_after:
                    try:
                        delay = min(float(retry_after), max_delay)
                    except ValueError:
                        pass

            log.warning(
                "Retrying %s (attempt %d/%d) after %s, delay=%.1fs",
                backend, attempt + 1, max_retries, type(exc).__name__, delay,
            )
            await asyncio.sleep(delay)

    # Should not reach here, but just in case
    breaker.record_failure()
    raise last_exc


class BackendUnavailableError(Exception):
    """Raised when a circuit breaker is open for a backend."""
    pass
