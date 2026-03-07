"""
Lightweight rate limiting for MultiLLM Gateway.

Token bucket per API key / IP / project with configurable limits.
Uses in-memory counters (no Redis dependency for single-process deployment).
"""

import os
import time
import logging
from collections import defaultdict
from dataclasses import dataclass, field

log = logging.getLogger("multillm.rate_limit")

# ── Configuration ────────────────────────────────────────────────────────────

# Requests per minute per client (0 = disabled)
RATE_LIMIT_RPM = int(os.getenv("RATE_LIMIT_RPM", "0"))
# Max concurrent streaming requests per client
RATE_LIMIT_CONCURRENT = int(os.getenv("RATE_LIMIT_CONCURRENT", "0"))


def is_rate_limiting_enabled() -> bool:
    return RATE_LIMIT_RPM > 0 or RATE_LIMIT_CONCURRENT > 0


# ── Token Bucket ─────────────────────────────────────────────────────────────

@dataclass
class TokenBucket:
    """Sliding-window token bucket rate limiter."""
    capacity: int  # max tokens (requests) per window
    window_seconds: float = 60.0
    _tokens: float = field(init=False)
    _last_refill: float = field(init=False)

    def __post_init__(self):
        self._tokens = float(self.capacity)
        self._last_refill = time.monotonic()

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self._last_refill
        refill = elapsed * (self.capacity / self.window_seconds)
        self._tokens = min(self.capacity, self._tokens + refill)
        self._last_refill = now

    def try_acquire(self) -> bool:
        """Try to consume one token. Returns True if allowed."""
        self._refill()
        if self._tokens >= 1:
            self._tokens -= 1
            return True
        return False

    @property
    def remaining(self) -> int:
        self._refill()
        return int(self._tokens)

    @property
    def retry_after(self) -> float:
        """Seconds until next token is available."""
        if self._tokens >= 1:
            return 0.0
        return (1 - self._tokens) * (self.window_seconds / self.capacity)


# ── Per-client tracking ──────────────────────────────────────────────────────

_buckets: dict[str, TokenBucket] = {}
_concurrent: dict[str, int] = defaultdict(int)


def _get_bucket(client_id: str) -> TokenBucket:
    if client_id not in _buckets:
        _buckets[client_id] = TokenBucket(capacity=RATE_LIMIT_RPM)
    return _buckets[client_id]


def get_client_id(request) -> str:
    """Extract client identifier from request (API key or IP)."""
    # Prefer API key if present
    auth = request.headers.get("x-api-key", "")
    if auth:
        return f"key:{auth[:8]}"
    # Fall back to IP
    client_host = request.client.host if request.client else "unknown"
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        client_host = forwarded.split(",")[0].strip()
    return f"ip:{client_host}"


def check_rate_limit(client_id: str) -> tuple[bool, dict]:
    """Check if a request is allowed. Returns (allowed, headers_dict)."""
    if not RATE_LIMIT_RPM:
        return True, {}

    bucket = _get_bucket(client_id)
    headers = {
        "X-RateLimit-Limit": str(RATE_LIMIT_RPM),
        "X-RateLimit-Remaining": str(bucket.remaining),
    }

    if bucket.try_acquire():
        return True, headers

    retry = bucket.retry_after
    headers["Retry-After"] = str(int(retry) + 1)
    headers["X-RateLimit-Remaining"] = "0"
    log.warning("Rate limit exceeded for %s, retry_after=%.1fs", client_id, retry)
    return False, headers


def acquire_concurrent(client_id: str) -> bool:
    """Try to acquire a concurrent request slot."""
    if not RATE_LIMIT_CONCURRENT:
        return True
    if _concurrent[client_id] >= RATE_LIMIT_CONCURRENT:
        log.warning("Concurrent limit exceeded for %s (%d/%d)",
                     client_id, _concurrent[client_id], RATE_LIMIT_CONCURRENT)
        return False
    _concurrent[client_id] += 1
    return True


def release_concurrent(client_id: str):
    """Release a concurrent request slot."""
    if _concurrent[client_id] > 0:
        _concurrent[client_id] -= 1


def rate_limit_status() -> dict:
    """Get current rate limit state for admin API."""
    return {
        "enabled": is_rate_limiting_enabled(),
        "rpm_limit": RATE_LIMIT_RPM,
        "concurrent_limit": RATE_LIMIT_CONCURRENT,
        "active_clients": len(_buckets),
        "clients": {
            cid: {
                "remaining": b.remaining,
                "concurrent": _concurrent.get(cid, 0),
            }
            for cid, b in _buckets.items()
        },
    }
