"""
Semantic caching layer using Redis LangCache.

Intercepts LLM requests at the gateway level. Before calling any backend,
checks if a semantically similar prompt was already answered. Cache hits
return instantly with zero token cost.

Configuration (via settings or env vars):
    LANGCACHE_ENABLED=true
    LANGCACHE_HOST=your-redis-host
    LANGCACHE_CACHE_ID=your-cache-id
    LANGCACHE_API_KEY=your-api-key
    LANGCACHE_THRESHOLD=0.92  (similarity threshold, 0.0 to 1.0)
"""

import json
import logging
import os
import time
from typing import Optional

log = logging.getLogger("multillm.caching")

# ── Configuration ────────────────────────────────────────────────────────────

LANGCACHE_ENABLED = os.getenv("LANGCACHE_ENABLED", "false").lower() in ("true", "1", "yes")
LANGCACHE_HOST = os.getenv("LANGCACHE_HOST", "")
LANGCACHE_CACHE_ID = os.getenv("LANGCACHE_CACHE_ID", "")
LANGCACHE_API_KEY = os.getenv("LANGCACHE_API_KEY", "")
LANGCACHE_THRESHOLD = float(os.getenv("LANGCACHE_THRESHOLD", "0.92"))
LANGCACHE_CROSS_MODEL = os.getenv("LANGCACHE_CROSS_MODEL", "false").lower() in ("true", "1", "yes")

# ── Cache client ─────────────────────────────────────────────────────────────

_cache_client = None
_cache_stats = {"hits": 0, "misses": 0, "stores": 0, "errors": 0}


def _get_client():
    """Get or create the LangCache client."""
    global _cache_client

    if _cache_client is not None:
        return _cache_client

    if not LANGCACHE_ENABLED:
        return None

    if not all([LANGCACHE_HOST, LANGCACHE_CACHE_ID, LANGCACHE_API_KEY]):
        log.warning("LangCache enabled but missing config (HOST, CACHE_ID, or API_KEY)")
        return None

    try:
        from langcache import LangCache
        _cache_client = LangCache(
            server_url=f"https://{LANGCACHE_HOST}" if not LANGCACHE_HOST.startswith("http") else LANGCACHE_HOST,
            cache_id=LANGCACHE_CACHE_ID,
            api_key=LANGCACHE_API_KEY,
        )
        log.info("LangCache connected (host=%s, cache=%s, threshold=%.2f)",
                 LANGCACHE_HOST, LANGCACHE_CACHE_ID, LANGCACHE_THRESHOLD)
        return _cache_client
    except ImportError:
        log.warning("langcache package not installed. Run: pip install langcache")
        return None
    except Exception as e:
        log.error("LangCache init failed: %s", e)
        return None


def _extract_prompt_text(body: dict) -> str:
    """Extract a cache key from the Anthropic-format message body."""
    messages = body.get("messages", [])
    parts = []

    # Include system prompt if present
    system = body.get("system", "")
    if system:
        if isinstance(system, str):
            parts.append(f"[system]{system}")
        elif isinstance(system, list):
            for block in system:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(f"[system]{block['text']}")

    # Include user messages (last N for context)
    for msg in messages[-3:]:  # Last 3 messages for context
        role = msg.get("role", "")
        content = msg.get("content", "")
        if isinstance(content, str):
            parts.append(f"[{role}]{content}")
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(f"[{role}]{block['text']}")

    return "\n".join(parts)


def _make_attributes(model_alias: str, backend: str, project: str, for_search: bool = False) -> dict:
    """Create cache attributes for scoping.

    When LANGCACHE_CROSS_MODEL is True and for_search is True, omits model/backend
    so any cached answer can serve any model request for the same prompt.
    """
    if LANGCACHE_CROSS_MODEL and for_search:
        return {"project": project}
    return {
        "model": model_alias,
        "backend": backend,
        "project": project,
    }


# ── Public API ───────────────────────────────────────────────────────────────

async def cache_search(
    body: dict,
    model_alias: str,
    backend: str,
    project: str,
    threshold: Optional[float] = None,
) -> Optional[dict]:
    """
    Search the semantic cache for a matching response.

    Returns the cached Anthropic-format response dict if found, None on miss.
    """
    client = _get_client()
    if not client:
        return None

    prompt_text = _extract_prompt_text(body)
    if not prompt_text or len(prompt_text) < 10:
        return None

    try:
        result = client.search(
            prompt=prompt_text,
            similarity_threshold=threshold or LANGCACHE_THRESHOLD,
            attributes=_make_attributes(model_alias, backend, project, for_search=True),
        )

        if result and hasattr(result, "response") and result.response:
            _cache_stats["hits"] += 1
            log.info("Cache HIT for model=%s (similarity search)", model_alias)
            # Parse the stored response (we store it as JSON)
            try:
                cached_response = json.loads(result.response)
                # Mark as cached
                cached_response["_cached"] = True
                cached_response["_cache_entry_id"] = getattr(result, "entry_id", None)
                return cached_response
            except json.JSONDecodeError:
                # Plain text response — wrap in Anthropic format
                from .converters import make_anthropic_response
                resp = make_anthropic_response(
                    text=result.response,
                    model=f"{model_alias} (cached)",
                    input_tokens=0,
                    output_tokens=0,
                )
                resp["_cached"] = True
                return resp

        _cache_stats["misses"] += 1
        return None

    except Exception as e:
        _cache_stats["errors"] += 1
        log.debug("Cache search error: %s", e)
        return None


async def cache_store(
    body: dict,
    response: dict,
    model_alias: str,
    backend: str,
    project: str,
) -> bool:
    """
    Store a response in the semantic cache.

    Only stores successful responses with actual content.
    """
    client = _get_client()
    if not client:
        return False

    prompt_text = _extract_prompt_text(body)
    if not prompt_text or len(prompt_text) < 10:
        return False

    # Don't cache error responses or empty content
    content = response.get("content", [])
    if not content:
        return False
    if response.get("stop_reason") == "error":
        return False

    # Don't cache tool_use responses (they're context-dependent)
    has_tool_use = any(b.get("type") == "tool_use" for b in content if isinstance(b, dict))
    if has_tool_use:
        return False

    try:
        # Store the full response as JSON
        response_json = json.dumps(response)
        client.set(
            prompt=prompt_text,
            response=response_json,
            attributes=_make_attributes(model_alias, backend, project),
        )
        _cache_stats["stores"] += 1
        log.debug("Cached response for model=%s (%d chars)", model_alias, len(response_json))
        return True

    except Exception as e:
        _cache_stats["errors"] += 1
        log.debug("Cache store error: %s", e)
        return False


def get_cache_stats() -> dict:
    """Get cache statistics."""
    total = _cache_stats["hits"] + _cache_stats["misses"]
    hit_rate = (_cache_stats["hits"] / total * 100) if total > 0 else 0
    return {
        **_cache_stats,
        "total_lookups": total,
        "hit_rate_pct": round(hit_rate, 1),
        "enabled": LANGCACHE_ENABLED,
        "connected": _cache_client is not None,
        "cross_model": LANGCACHE_CROSS_MODEL,
        "threshold": LANGCACHE_THRESHOLD,
    }


async def cache_flush() -> bool:
    """Flush the entire cache."""
    client = _get_client()
    if not client:
        return False
    try:
        client.flush()
        _cache_stats["hits"] = 0
        _cache_stats["misses"] = 0
        _cache_stats["stores"] = 0
        log.info("Cache flushed")
        return True
    except Exception as e:
        log.error("Cache flush failed: %s", e)
        return False
