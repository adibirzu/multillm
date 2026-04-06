"""
Langfuse LLM observability integration for MultiLLM Gateway.

Records every LLM call as a Langfuse generation with full token usage,
model metadata, latency, and cost. Provides rich LLM-specific observability
complementing the infrastructure-level traces sent to OCI APM.

Architecture: Direct SDK integration (no collector needed) — the gateway
already proxies every LLM call, so we emit Langfuse events inline.

Uses Langfuse SDK v4 API (start_observation / start_as_current_observation).
"""

import logging
from typing import Optional

from .config import (
    LANGFUSE_ENABLED, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST,
)

log = logging.getLogger("multillm.langfuse")

_client = None


def init_langfuse() -> bool:
    """Initialize Langfuse client. Returns True if successful."""
    global _client

    if not LANGFUSE_ENABLED:
        log.info("Langfuse disabled (set LANGFUSE_ENABLED=true)")
        return False

    if not LANGFUSE_PUBLIC_KEY or not LANGFUSE_SECRET_KEY:
        log.warning("Langfuse enabled but missing keys (LANGFUSE_PUBLIC_KEY/SECRET_KEY)")
        return False

    try:
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=LANGFUSE_PUBLIC_KEY,
            secret_key=LANGFUSE_SECRET_KEY,
            host=LANGFUSE_HOST,
        )
        # Verify auth
        _client.auth_check()
        log.info("Langfuse v3 initialized: host=%s (auth OK)", LANGFUSE_HOST)
        return True
    except ImportError:
        log.warning("langfuse package not installed (pip install langfuse)")
        return False
    except Exception as e:
        log.error("Langfuse init failed: %s", e)
        return False


def shutdown_langfuse() -> None:
    """Flush and shutdown Langfuse client."""
    global _client
    if _client:
        try:
            _client.flush()
            _client.shutdown()
        except Exception:
            pass
        _client = None


# Backend → provider display name
_PROVIDER_MAP = {
    "ollama": "ollama", "lmstudio": "lmstudio", "openai": "openai",
    "anthropic": "anthropic", "openrouter": "openrouter", "gemini": "google",
    "groq": "groq", "deepseek": "deepseek", "mistral": "mistral",
    "together": "together", "xai": "xai", "fireworks": "fireworks",
    "oca": "oracle-code-assist", "azure_openai": "azure-openai",
    "bedrock": "aws-bedrock", "codex_cli": "openai-codex",
    "gemini_cli": "google-gemini",
}


def trace_llm_generation(
    *,
    model_alias: str,
    backend: str,
    real_model: str,
    project: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_create_tokens: int = 0,
    latency_ms: float = 0,
    cost_usd: float = 0,
    status: str = "ok",
    error_message: Optional[str] = None,
    is_streaming: bool = False,
    request_id: Optional[str] = None,
    prompt_text: Optional[str] = None,
    response_text: Optional[str] = None,
) -> None:
    """Record an LLM call as a Langfuse generation (v4 SDK API).

    Uses start_observation(as_type='generation') to create a generation
    span with full token usage, model, latency, and cost metadata.
    """
    if not _client:
        return

    try:
        provider = _PROVIDER_MAP.get(backend, backend)

        # Build usage_details (v4 SDK uses usage_details dict instead of usage)
        usage_details = {
            "input": input_tokens,
            "output": output_tokens,
            "total": input_tokens + output_tokens,
        }
        if cache_read_tokens:
            usage_details["cache_read"] = cache_read_tokens
        if cache_create_tokens:
            usage_details["cache_create"] = cache_create_tokens

        # Create a generation observation using v4 API
        generation = _client.start_observation(
            name=f"{model_alias}",
            as_type="generation",
            model=real_model or model_alias,
            model_parameters={
                "provider": provider,
                "backend": backend,
                "streaming": is_streaming,
            },
            input=prompt_text[:500] if prompt_text else None,
            output=response_text[:500] if response_text else None,
            usage_details=usage_details,
            metadata={
                "project": project,
                "request_id": request_id or "",
                "cache_read_tokens": cache_read_tokens,
                "cache_create_tokens": cache_create_tokens,
                "latency_ms": round(latency_ms, 1),
            },
            level="ERROR" if status != "ok" else "DEFAULT",
            status_message=error_message if error_message else None,
        )

        # End the generation
        generation.end()

    except Exception as e:
        log.debug("Langfuse trace failed: %s", e)


def get_langfuse_status() -> dict:
    """Get Langfuse connection status for the dashboard."""
    return {
        "enabled": LANGFUSE_ENABLED,
        "initialized": _client is not None,
        "host": LANGFUSE_HOST if LANGFUSE_ENABLED else None,
        "has_keys": bool(LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY),
    }
