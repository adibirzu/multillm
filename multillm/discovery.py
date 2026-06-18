# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 MultiLLM contributors

"""
Dynamic model discovery for all backends.

Queries each backend's API to find available models, then merges with
static routes from config.py. Supports: Ollama, LM Studio, OpenAI,
OpenRouter, Gemini.

Usage:
    models = await discover_all_models()
    routes = await refresh_routes()  # merges discovered + static
"""

import logging
import re
import time

import httpx

from .config import (
    OLLAMA_URL, LMSTUDIO_URL,
    OPENROUTER_KEY, OPENAI_KEY, GEMINI_KEY,
    GROQ_KEY, DEEPSEEK_KEY, MISTRAL_KEY, TOGETHER_KEY,
    XAI_KEY, FIREWORKS_KEY,
)

log = logging.getLogger("multillm.discovery")

# Cache discovered models (refresh at most every 5 minutes)
_discovery_cache: dict = {}
_cache_timestamp: float = 0.0
CACHE_TTL = 300  # seconds


async def discover_ollama() -> list[dict]:
    """Query Ollama for installed models via GET /api/tags."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags")
            r.raise_for_status()
            data = r.json()

        models = []
        for m in data.get("models", []):
            name = m.get("name", "")
            # Strip :latest suffix for cleaner aliases
            alias = name.split(":")[0] if ":" in name and name.endswith(":latest") else name
            models.append({
                "id": f"ollama/{alias}",
                "backend": "ollama",
                "model": name,
                "name": alias,
                "catalog_source": "local_api",
                "size": m.get("size", 0),
                "parameter_size": m.get("details", {}).get("parameter_size", ""),
                "family": m.get("details", {}).get("family", ""),
                "quantization": m.get("details", {}).get("quantization_level", ""),
            })
        log.info("Ollama: discovered %d models", len(models))
        return models
    except Exception as e:
        log.debug("Ollama discovery failed: %s", e)
        return []


async def discover_lmstudio() -> list[dict]:
    """Query LM Studio for loaded models via GET /v1/models."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{LMSTUDIO_URL}/v1/models")
            r.raise_for_status()
            data = r.json()

        models = []
        for m in data.get("data", []):
            model_id = m.get("id", "")
            models.append({
                "id": f"lmstudio/{model_id}",
                "backend": "lmstudio",
                "model": model_id,
                "name": model_id,
                "catalog_source": "local_api",
                "owned_by": m.get("owned_by", "lmstudio"),
            })
        log.info("LM Studio: discovered %d models", len(models))
        return models
    except Exception as e:
        log.debug("LM Studio discovery failed: %s", e)
        return []


async def discover_openai() -> list[dict]:
    """Query OpenAI for available models via GET /v1/models."""
    if not OPENAI_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {OPENAI_KEY}"},
            )
            r.raise_for_status()
            data = r.json()

        # Filter to chat-capable models
        chat_prefixes = ("gpt-4", "gpt-3.5", "o1", "o3", "o4", "codex")
        models = []
        for m in data.get("data", []):
            mid = m.get("id", "")
            if any(mid.startswith(p) for p in chat_prefixes):
                models.append({
                    "id": f"openai/{mid}",
                    "backend": "openai",
                    "model": mid,
                    "name": mid,
                    "catalog_source": "api",
                    "owned_by": m.get("owned_by", "openai"),
                })
        log.info("OpenAI: discovered %d chat models", len(models))
        return models
    except Exception as e:
        log.debug("OpenAI discovery failed: %s", e)
        return []


async def discover_openrouter() -> list[dict]:
    """Query OpenRouter for available models."""
    if not OPENROUTER_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {OPENROUTER_KEY}"},
            )
            r.raise_for_status()
            data = r.json()

        models = []
        for m in data.get("data", []):
            mid = m.get("id", "")
            # Create a short alias from provider/model format
            short = mid.replace("/", "-").replace(":", "-")
            models.append({
                "id": f"openrouter/{short}",
                "backend": "openrouter",
                "model": mid,
                "name": m.get("name", short),
                "catalog_source": "api",
                "context_length": m.get("context_length", 0),
                "pricing": m.get("pricing", {}),
            })
        log.info("OpenRouter: discovered %d models", len(models))
        return models
    except Exception as e:
        log.debug("OpenRouter discovery failed: %s", e)
        return []


async def discover_gemini() -> list[dict]:
    """Query Gemini for available models."""
    if not GEMINI_KEY:
        return []
    try:
        # Use the REST API directly (no SDK dependency for discovery)
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_KEY}",
            )
            r.raise_for_status()
            data = r.json()

        models = []
        for m in data.get("models", []):
            name = m.get("name", "").replace("models/", "")
            # Only include generative models
            if "generateContent" in str(m.get("supportedGenerationMethods", [])):
                models.append({
                    "id": f"gemini/{name}",
                    "backend": "gemini",
                    "model": name,
                    "name": m.get("displayName", name),
                    "catalog_source": "api",
                    "input_token_limit": m.get("inputTokenLimit", 0),
                    "output_token_limit": m.get("outputTokenLimit", 0),
                })
        log.info("Gemini: discovered %d models", len(models))
        return models
    except Exception as e:
        log.debug("Gemini discovery failed: %s", e)
        return []


async def _discover_openai_compat(
    backend: str, api_key: str, base_url: str, prefix: str = "",
) -> list[dict]:
    """Generic discovery for any OpenAI-compatible API that supports GET /v1/models."""
    if not api_key:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{base_url}/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            r.raise_for_status()
            data = r.json()
        models = []
        for m in data.get("data", []):
            mid = m.get("id", "")
            alias = f"{prefix}/{mid}" if prefix else mid
            models.append({
                "id": alias,
                "backend": backend,
                "model": mid,
                "name": mid.split("/")[-1] if "/" in mid else mid,
                "catalog_source": "api",
            })
        if models:
            log.info("%s: discovered %d models", backend.capitalize(), len(models))
        return models
    except Exception as e:
        log.debug("%s discovery failed: %s", backend.capitalize(), e)
        return []


async def discover_groq() -> list[dict]:
    return await _discover_openai_compat("groq", GROQ_KEY, "https://api.groq.com/openai", "groq")

async def discover_deepseek() -> list[dict]:
    return await _discover_openai_compat("deepseek", DEEPSEEK_KEY, "https://api.deepseek.com", "deepseek")

async def discover_mistral() -> list[dict]:
    return await _discover_openai_compat("mistral", MISTRAL_KEY, "https://api.mistral.ai", "mistral")

async def discover_together() -> list[dict]:
    return await _discover_openai_compat("together", TOGETHER_KEY, "https://api.together.xyz", "together")

async def discover_xai() -> list[dict]:
    return await _discover_openai_compat("xai", XAI_KEY, "https://api.x.ai", "xai")

async def discover_fireworks() -> list[dict]:
    return await _discover_openai_compat("fireworks", FIREWORKS_KEY, "https://api.fireworks.ai/inference", "fireworks")


async def discover_all_models(force: bool = False) -> dict[str, list[dict]]:
    """
    Discover models from all backends. Results are cached for CACHE_TTL seconds.

    Returns: {"ollama": [...], "lmstudio": [...], "openai": [...], ...}
    """
    global _discovery_cache, _cache_timestamp

    if not force and _discovery_cache and (time.time() - _cache_timestamp) < CACHE_TTL:
        return _discovery_cache

    import asyncio
    results = await asyncio.gather(
        discover_ollama(),
        discover_lmstudio(),
        discover_openai(),
        discover_openrouter(),
        discover_gemini(),
        discover_groq(),
        discover_deepseek(),
        discover_mistral(),
        discover_together(),
        discover_xai(),
        discover_fireworks(),
        return_exceptions=True,
    )

    backends = [
        "ollama", "lmstudio", "openai", "openrouter", "gemini",
        "groq", "deepseek", "mistral", "together", "xai", "fireworks",
    ]
    cache = {}
    for name, result in zip(backends, results):
        if isinstance(result, Exception):
            log.warning("Discovery failed for %s: %s", name, result)
            cache[name] = []
        else:
            cache[name] = result

    _discovery_cache = cache
    _cache_timestamp = time.time()
    total = sum(len(v) for v in cache.values())
    log.info("Model discovery complete: %d models across %d backends",
             total, sum(1 for v in cache.values() if v))
    return cache


def discovered_to_routes(discovered: dict[str, list[dict]]) -> dict[str, dict]:
    """Convert discovered models to route format compatible with config.py."""
    routes = {}
    for backend, models in discovered.items():
        for m in models:
            alias = m["id"]
            routes[alias] = {
                "backend": backend,
                "model": m["model"],
                "discovered": True,
                "name": m.get("name", ""),
            }
    return routes


# ── Installed-aware local routing ──────────────────────────────────────────
#
# Backends that run on the user's own machine and expose a model list. A model
# is only in ``_discovery_cache`` if its probe succeeded, so presence here means
# "installed and reachable when last discovered" — the signal we route on.
LOCAL_DISCOVERABLE_BACKENDS = ("ollama", "lmstudio")


def _parse_parameter_size(value: str) -> float:
    """Parse an Ollama ``parameter_size`` like ``"30B"`` / ``"3.8B"`` into billions.

    Returns 0.0 when the value is missing or unparseable so it sorts last.
    """
    if not value:
        return 0.0
    match = re.match(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([bBmM])?", str(value))
    if not match:
        return 0.0
    number = float(match.group(1))
    unit = (match.group(2) or "b").lower()
    return number / 1000.0 if unit == "m" else number


def _capability_key(model: dict) -> tuple[float, int]:
    """Rank key for a local model: larger parameter count (then bytes) is more capable."""
    return (
        _parse_parameter_size(model.get("parameter_size", "")),
        int(model.get("size", 0) or 0),
    )


def get_discovered_local_models() -> list[dict]:
    """Return locally-installed + reachable models from the discovery cache.

    Reads the module cache populated by :func:`discover_all_models`; does not
    perform any network I/O. Empty until the first discovery pass has run.
    """
    models: list[dict] = []
    for backend in LOCAL_DISCOVERABLE_BACKENDS:
        models.extend(_discovery_cache.get(backend, []))
    return models


def rank_local_models(models: list[dict]) -> list[dict]:
    """Order local models most-capable first using a parameter-size heuristic."""
    return sorted(models, key=_capability_key, reverse=True)


def resolve_local_target(
    *,
    reachable_backends: "set[str] | None" = None,
) -> "tuple[str, dict] | None":
    """Pick the best installed + reachable local model as a route target.

    Selects the highest-capability model among locally-discovered backends,
    optionally restricted to ``reachable_backends`` (e.g. health-gated set).
    Returns ``(alias, route_dict)`` or ``None`` when nothing local is available.
    """
    candidates = get_discovered_local_models()
    if reachable_backends is not None:
        candidates = [m for m in candidates if m.get("backend") in reachable_backends]
    if not candidates:
        return None
    best = rank_local_models(candidates)[0]
    route = {
        "backend": best["backend"],
        "model": best["model"],
        "discovered": True,
        "name": best.get("name", ""),
    }
    return best["id"], route
