"""
Shared httpx client pool — one persistent AsyncClient per backend.

Eliminates per-request TCP/TLS overhead by reusing connections.
Each backend gets its own client with appropriate timeout/limits.
"""

import httpx
import logging

log = logging.getLogger("multillm.http_pool")

# Backend-specific timeout profiles
_PROFILES = {
    "ollama":    httpx.Timeout(300.0, connect=5.0),   # local, large models can be slow
    "lmstudio":  httpx.Timeout(120.0, connect=5.0),   # local
    "openai":    httpx.Timeout(120.0, connect=10.0),   # cloud
    "openrouter": httpx.Timeout(120.0, connect=10.0),
    "anthropic": httpx.Timeout(120.0, connect=10.0),
    "oca":       httpx.Timeout(180.0, connect=10.0),   # OCA can be slow
    "gemini":    httpx.Timeout(120.0, connect=10.0),
    "groq":      httpx.Timeout(60.0, connect=10.0),    # Groq is ultra-fast
    "deepseek":  httpx.Timeout(120.0, connect=10.0),
    "mistral":   httpx.Timeout(120.0, connect=10.0),
    "together":  httpx.Timeout(120.0, connect=10.0),
    "xai":       httpx.Timeout(120.0, connect=10.0),
    "fireworks": httpx.Timeout(120.0, connect=10.0),
    "azure_openai": httpx.Timeout(120.0, connect=10.0),
    "default":   httpx.Timeout(120.0, connect=10.0),
}

# Connection limits per backend
_LIMITS = {
    "ollama":   httpx.Limits(max_connections=20, max_keepalive_connections=10),
    "lmstudio": httpx.Limits(max_connections=10, max_keepalive_connections=5),
    "default":  httpx.Limits(max_connections=30, max_keepalive_connections=15),
}

_clients: dict[str, httpx.AsyncClient] = {}


def get_client(backend: str) -> httpx.AsyncClient:
    """Get or create a shared AsyncClient for the given backend."""
    if backend not in _clients:
        timeout = _PROFILES.get(backend, _PROFILES["default"])
        limits = _LIMITS.get(backend, _LIMITS["default"])
        _clients[backend] = httpx.AsyncClient(
            timeout=timeout,
            limits=limits,
            http2=True,
            follow_redirects=True,
        )
        log.info("Created HTTP client pool for %s (timeout=%s)", backend, timeout)
    return _clients[backend]


async def close_all():
    """Close all client pools. Call on shutdown."""
    for name, client in _clients.items():
        await client.aclose()
        log.info("Closed HTTP client pool: %s", name)
    _clients.clear()
