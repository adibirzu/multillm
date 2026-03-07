"""
Active backend health checks for MultiLLM Gateway.

Runs periodic probes against each backend and maintains health state
used by the router to skip degraded backends before user requests fail.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from .config import (
    OLLAMA_URL, LMSTUDIO_URL,
    OPENROUTER_KEY, OPENAI_KEY, ANTHROPIC_KEY, GEMINI_KEY,
    GROQ_KEY, DEEPSEEK_KEY, MISTRAL_KEY, TOGETHER_KEY,
    XAI_KEY, FIREWORKS_KEY,
    AZURE_OPENAI_KEY, AZURE_OPENAI_ENDPOINT,
    OCA_ENDPOINT,
)
from .oca_auth import get_oca_bearer_token
from .resilience import get_breaker

log = logging.getLogger("multillm.health")


# ── Health states ────────────────────────────────────────────────────────────

@dataclass
class BackendHealth:
    status: str = "unknown"  # healthy, degraded, unhealthy, unconfigured
    last_check: float = 0.0
    last_latency_ms: float = 0.0
    last_error: Optional[str] = None
    consecutive_failures: int = 0
    check_count: int = 0

    def mark_healthy(self, latency_ms: float):
        self.status = "healthy"
        self.last_check = time.time()
        self.last_latency_ms = latency_ms
        self.last_error = None
        self.consecutive_failures = 0
        self.check_count += 1

    def mark_degraded(self, latency_ms: float, reason: str):
        self.status = "degraded"
        self.last_check = time.time()
        self.last_latency_ms = latency_ms
        self.last_error = reason
        self.check_count += 1

    def mark_unhealthy(self, error: str):
        self.status = "unhealthy"
        self.last_check = time.time()
        self.last_error = error
        self.consecutive_failures += 1
        self.check_count += 1

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "last_check_ago": f"{time.time() - self.last_check:.0f}s" if self.last_check else "never",
            "latency_ms": round(self.last_latency_ms, 1),
            "last_error": self.last_error,
            "consecutive_failures": self.consecutive_failures,
            "checks_total": self.check_count,
        }


# ── Global health registry ──────────────────────────────────────────────────

_health: dict[str, BackendHealth] = {}
_check_task: Optional[asyncio.Task] = None

DEGRADED_LATENCY_MS = 5000  # >5s = degraded
CHECK_INTERVAL = 120  # seconds between health check cycles


def get_health(backend: str) -> BackendHealth:
    if backend not in _health:
        _health[backend] = BackendHealth()
    return _health[backend]


def is_backend_healthy(backend: str) -> bool:
    """Check if a backend is usable (healthy or degraded, not unhealthy)."""
    h = _health.get(backend)
    if h is None:
        return True  # unknown = assume ok
    return h.status in ("healthy", "degraded", "unknown")


def all_health_status() -> dict[str, dict]:
    """Get health status for all checked backends."""
    result = {}
    for name, h in _health.items():
        breaker = get_breaker(name)
        result[name] = {
            **h.to_dict(),
            "circuit_breaker": breaker.state,
        }
    return result


# ── Probe functions ──────────────────────────────────────────────────────────

async def _probe_http(url: str, backend: str, timeout: float = 10.0) -> tuple[bool, float, str]:
    """Probe an HTTP endpoint. Returns (ok, latency_ms, error)."""
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(url)
            latency = (time.monotonic() - t0) * 1000
            if r.status_code < 400:
                return True, latency, ""
            return False, latency, f"HTTP {r.status_code}"
    except Exception as e:
        latency = (time.monotonic() - t0) * 1000
        return False, latency, f"{type(e).__name__}: {e}"


async def _probe_api_key(url: str, api_key: str, backend: str, timeout: float = 10.0) -> tuple[bool, float, str]:
    """Probe a cloud API with key auth (list models endpoint)."""
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(
                f"{url}/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            latency = (time.monotonic() - t0) * 1000
            if r.status_code < 400:
                return True, latency, ""
            return False, latency, f"HTTP {r.status_code}"
    except Exception as e:
        latency = (time.monotonic() - t0) * 1000
        return False, latency, f"{type(e).__name__}: {e}"


# ── Check all backends ───────────────────────────────────────────────────────

BACKEND_PROBES = {
    "ollama": lambda: _probe_http(f"{OLLAMA_URL}/api/tags", "ollama", timeout=5),
    "lmstudio": lambda: _probe_http(f"{LMSTUDIO_URL}/v1/models", "lmstudio", timeout=5),
    "openai": lambda: _probe_api_key("https://api.openai.com", OPENAI_KEY, "openai") if OPENAI_KEY else _skip("openai"),
    "openrouter": lambda: _probe_api_key("https://openrouter.ai/api", OPENROUTER_KEY, "openrouter") if OPENROUTER_KEY else _skip("openrouter"),
    "groq": lambda: _probe_api_key("https://api.groq.com/openai", GROQ_KEY, "groq") if GROQ_KEY else _skip("groq"),
    "deepseek": lambda: _probe_api_key("https://api.deepseek.com", DEEPSEEK_KEY, "deepseek") if DEEPSEEK_KEY else _skip("deepseek"),
    "mistral": lambda: _probe_api_key("https://api.mistral.ai", MISTRAL_KEY, "mistral") if MISTRAL_KEY else _skip("mistral"),
    "together": lambda: _probe_api_key("https://api.together.xyz", TOGETHER_KEY, "together") if TOGETHER_KEY else _skip("together"),
    "xai": lambda: _probe_api_key("https://api.x.ai", XAI_KEY, "xai") if XAI_KEY else _skip("xai"),
    "fireworks": lambda: _probe_api_key("https://api.fireworks.ai/inference", FIREWORKS_KEY, "fireworks") if FIREWORKS_KEY else _skip("fireworks"),
}


async def _skip(backend: str) -> tuple[bool, float, str]:
    return False, 0.0, "not configured"


async def _probe_anthropic() -> tuple[bool, float, str]:
    if not ANTHROPIC_KEY:
        return False, 0.0, "not configured"
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://api.anthropic.com/v1/models",
                headers={"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01"},
            )
            latency = (time.monotonic() - t0) * 1000
            if r.status_code < 400:
                return True, latency, ""
            return False, latency, f"HTTP {r.status_code}"
    except Exception as e:
        latency = (time.monotonic() - t0) * 1000
        return False, latency, f"{type(e).__name__}: {e}"


async def _probe_oca() -> tuple[bool, float, str]:
    if not OCA_ENDPOINT:
        return False, 0.0, "not configured"
    token = await get_oca_bearer_token()
    if not token:
        return False, 0.0, "auth failed"
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{OCA_ENDPOINT}/20250206/app/litellm/models",
                headers={"Authorization": f"Bearer {token}", "client": "multillm-gateway"},
            )
            latency = (time.monotonic() - t0) * 1000
            if r.status_code < 400:
                return True, latency, ""
            return False, latency, f"HTTP {r.status_code}"
    except Exception as e:
        latency = (time.monotonic() - t0) * 1000
        return False, latency, f"{type(e).__name__}: {e}"


async def _probe_gemini() -> tuple[bool, float, str]:
    if not GEMINI_KEY:
        return False, 0.0, "not configured"
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_KEY}",
            )
            latency = (time.monotonic() - t0) * 1000
            if r.status_code < 400:
                return True, latency, ""
            return False, latency, f"HTTP {r.status_code}"
    except Exception as e:
        latency = (time.monotonic() - t0) * 1000
        return False, latency, f"{type(e).__name__}: {e}"


async def check_all_backends():
    """Run health probes against all backends concurrently."""
    probes = {**BACKEND_PROBES}
    probes["anthropic"] = _probe_anthropic
    probes["oca"] = _probe_oca
    probes["gemini"] = _probe_gemini

    tasks = {name: asyncio.create_task(fn()) for name, fn in probes.items()}

    for name, task in tasks.items():
        try:
            ok, latency_ms, error = await task
            h = get_health(name)
            if error == "not configured":
                h.status = "unconfigured"
                h.last_check = time.time()
            elif ok:
                if latency_ms > DEGRADED_LATENCY_MS:
                    h.mark_degraded(latency_ms, f"slow: {latency_ms:.0f}ms")
                else:
                    h.mark_healthy(latency_ms)
            else:
                h.mark_unhealthy(error)
        except Exception as e:
            get_health(name).mark_unhealthy(str(e))

    healthy = sum(1 for h in _health.values() if h.status == "healthy")
    total = sum(1 for h in _health.values() if h.status != "unconfigured")
    log.info("Health check complete: %d/%d backends healthy", healthy, total)


# ── Background check loop ───────────────────────────────────────────────────

async def _health_check_loop():
    """Periodically check all backends."""
    while True:
        try:
            await check_all_backends()
        except Exception as e:
            log.error("Health check loop error: %s", e)
        await asyncio.sleep(CHECK_INTERVAL)


def start_health_checks():
    """Start the background health check task."""
    global _check_task
    if _check_task is None or _check_task.done():
        _check_task = asyncio.create_task(_health_check_loop())
        log.info("Background health checks started (interval=%ds)", CHECK_INTERVAL)


def stop_health_checks():
    """Stop the background health check task."""
    global _check_task
    if _check_task and not _check_task.done():
        _check_task.cancel()
        _check_task = None
